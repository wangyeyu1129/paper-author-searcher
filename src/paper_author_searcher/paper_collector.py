"""Confirmed identity selection and conservative, traceable metadata deduplication."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import CONTENDER_STATUS
from .paper_deduplication import deduplicate_papers
from .paper_ownership import PaperOwnershipValidator
from .models import PaperSourceResult, PaperCollectionResult
from .paper_providers import OpenAlexPaperProvider, SemanticScholarPaperProvider, DBLPPaperProvider


class IdentityConfirmationRequired(ValueError):
    pass

def hold_for_review(papers, contenders):
    """Papers gathered from competing namesake profiles are leads, never verified authorship."""
    note = 'Identity unconfirmed: collected from {} competing profiles; verification withheld'.format(contenders)
    for paper in papers:
        for record in [paper] + list(paper.records):
            if record.ownership_status == 'verified':
                record.ownership_status = 'needs_review'
                record.ownership_evidence = list(record.ownership_evidence) + [note]


class PaperCollector:
    def __init__(self, providers=None, ownership_validator=None):
        if providers is None:
            providers = [
                OpenAlexPaperProvider(), SemanticScholarPaperProvider(), DBLPPaperProvider(),
            ]
        self.providers = providers
        self.ownership_validator = ownership_validator or PaperOwnershipValidator()

    def _select_ids(self, profile, selected_author):
        if selected_author is not None:
            source, author_id = selected_author
            known_ids = profile.author_id_aliases.get(source, [])
            representative = profile.author_ids.get(source)
            if author_id not in known_ids and author_id != representative:
                raise IdentityConfirmationRequired('Selected Author ID is not in this candidate')
            # Manual confirmation applies to this ID only, never competing candidates.
            return {source: [author_id]}, 'manual_author_id'
        if profile.status not in ('resolved', CONTENDER_STATUS):
            raise IdentityConfirmationRequired('Select an Author ID before collecting an unconfirmed identity')
        selected = {}
        for source, author_id in profile.author_ids.items():
            aliases = profile.author_id_aliases.get(source) or [author_id]
            selected[source] = sorted(set(aliases + [author_id]))
        if not selected:
            raise IdentityConfirmationRequired('No confirmed Author IDs available')
        if profile.status == CONTENDER_STATUS:
            return selected, CONTENDER_STATUS
        return selected, 'resolver_resolved'

    def collect(self, profile, selected_author=None, progress=None):
        selected, confirmation = self._select_ids(profile, selected_author)
        available = {provider.name: provider for provider in self.providers}
        sources = []
        tasks = []
        # Missing identity in a configured source is a coverage gap, not an empty bibliography.
        for source in sorted(set(available) | set(selected)):
            if source not in selected:
                sources.append(PaperSourceResult(
                    source=source, author_id=None, error='No confirmed Author ID for this source',
                ))
                continue
            for author_id in selected[source]:
                if source not in available:
                    sources.append(PaperSourceResult(
                        source=source, author_id=author_id, error='Paper Provider unavailable',
                    ))
                else:
                    tasks.append((available[source], author_id))
        with ThreadPoolExecutor(max_workers=max(1, len(available))) as pool:
            futures = {}
            for provider, author_id in tasks:
                future = pool.submit(provider.collect, author_id)
                futures[future] = (provider.name, author_id)
            for future in as_completed(futures):
                source, author_id = futures[future]
                try:
                    sources.append(future.result())
                except Exception as exc:
                    sources.append(PaperSourceResult(source=source, author_id=author_id, error=str(exc)))
        sources.sort(key=lambda result: (result.source, result.author_id or ''))
        records = []
        for source in sources:
            records.extend(source.papers)
        complete = bool(sources) and all(source.complete for source in sources)
        if progress:
            progress('Deduplicating papers', len(records), len(records))
        papers = deduplicate_papers(records)
        if progress:
            progress('Validating ownership', len(papers), len(papers))
        self.ownership_validator.validate(papers, profile)
        if confirmation == CONTENDER_STATUS:
            hold_for_review(papers, len(selected.get('openalex', [])) or sum(map(len, selected.values())))
        return PaperCollectionResult(
            papers=papers, sources=sources, complete=complete,
            selected_author_ids=selected, identity_confirmation=confirmation,
            professor_identity_status=profile.status,
        )
