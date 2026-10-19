"""Confirmed identity selection and conservative, traceable metadata deduplication."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import DEFAULT_COLLECTION, DEFAULT_DEDUPLICATION, DeduplicationConfig, RawMetadataPolicy
from .logging_config import log_isolated_failure
from .models import Paper, PaperCollectionResult, PaperSourceResult, ProfessorProfile
from .paper_deduplication import deduplicate_papers
from .paper_ownership import PaperOwnershipValidator
from .paper_providers import PaperProvider
from .raw_metadata import apply_policy
from .status import OwnershipStatus, ResolutionStatus

logger = logging.getLogger(__name__)


class IdentityConfirmationRequired(ValueError):
    pass


def hold_for_review(papers: Sequence[Paper], contenders: int) -> None:
    """Papers gathered from competing namesake profiles are leads, never verified authorship."""
    note = f'Identity unconfirmed: collected from {contenders} competing profiles; verification withheld'
    for paper in papers:
        for record in [paper, *list(paper.records)]:
            if record.ownership_status == OwnershipStatus.VERIFIED:
                record.ownership_status = OwnershipStatus.NEEDS_REVIEW
                record.ownership_evidence = [*list(record.ownership_evidence), note]


class PaperCollector:
    def __init__(
        self,
        providers: Iterable[PaperProvider],
        ownership_validator: Optional[PaperOwnershipValidator] = None,
        deduplication: DeduplicationConfig = DEFAULT_DEDUPLICATION,
        concurrency: Optional[Mapping[str, int]] = None,
        raw_metadata: str = RawMetadataPolicy.FULL,
    ) -> None:
        self.providers = list(providers)
        self.ownership_validator = ownership_validator or PaperOwnershipValidator()
        self.deduplication = deduplication
        # Author IDs paged at once per source; sources run side by side.
        self.concurrency = dict(DEFAULT_COLLECTION.source_concurrency if concurrency is None else concurrency)
        self.raw_metadata = RawMetadataPolicy(raw_metadata)

    @staticmethod
    def _collect_one(provider: PaperProvider, author_id: str, gate: Any) -> PaperSourceResult:
        with gate:
            return provider.collect(author_id)

    def _select_ids(
        self, profile: ProfessorProfile, selected_author: Optional[Tuple[str, str]]
    ) -> Tuple[Dict[str, List[str]], str]:
        if selected_author is not None:
            source, author_id = selected_author
            known_ids = profile.author_id_aliases.get(source, [])
            representative = profile.author_ids.get(source)
            if author_id not in known_ids and author_id != representative:
                raise IdentityConfirmationRequired('Selected Author ID is not in this candidate')
            # Manual confirmation applies to this ID only, never competing candidates.
            return {source: [author_id]}, 'manual_author_id'
        if profile.status not in (ResolutionStatus.RESOLVED, ResolutionStatus.AMBIGUOUS_CONTENDERS):
            raise IdentityConfirmationRequired('Select an Author ID before collecting an unconfirmed identity')
        selected = {}
        for source, author_id in profile.author_ids.items():
            aliases = profile.author_id_aliases.get(source) or [author_id]
            selected[source] = sorted({*aliases, author_id})
        if not selected:
            raise IdentityConfirmationRequired('No confirmed Author IDs available')
        if profile.status == ResolutionStatus.AMBIGUOUS_CONTENDERS:
            return selected, ResolutionStatus.AMBIGUOUS_CONTENDERS
        return selected, 'resolver_resolved'

    def collect(
        self,
        profile: ProfessorProfile,
        selected_author: Optional[Tuple[str, str]] = None,
        progress: Optional[Callable[..., None]] = None,
    ) -> PaperCollectionResult:
        selected, confirmation = self._select_ids(profile, selected_author)
        available = {provider.name: provider for provider in self.providers}
        sources = []
        tasks = []
        # Missing identity in a configured source is a coverage gap, not an empty bibliography.
        for source in sorted(set(available) | set(selected)):
            if source not in selected:
                sources.append(
                    PaperSourceResult(
                        source=source,
                        author_id=None,
                        error='No confirmed Author ID for this source',
                    )
                )
                continue
            for author_id in selected[source]:
                if source not in available:
                    sources.append(
                        PaperSourceResult(
                            source=source,
                            author_id=author_id,
                            error='Paper Provider unavailable',
                        )
                    )
                else:
                    tasks.append((available[source], author_id))
        limits = {name: self.concurrency.get(name, 1) for name in available}
        gates = {name: threading.BoundedSemaphore(limit) for name, limit in limits.items()}
        workers = max(1, min(len(tasks), sum(limits.values())))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='paper-collection') as pool:
            futures = {}
            for provider, author_id in tasks:
                future = pool.submit(self._collect_one, provider, author_id, gates[provider.name])
                futures[future] = (provider.name, author_id)
            for future in as_completed(futures):
                source, author_id = futures[future]
                try:
                    sources.append(future.result())
                except Exception as exc:
                    log_isolated_failure(logger, 'Paper source %s failed for author %s: %s', source, author_id, exc)
                    sources.append(PaperSourceResult(source=source, author_id=author_id, error=str(exc)))
        sources.sort(key=lambda result: (result.source, result.author_id or ''))
        records = []
        for fetched in sources:
            records.extend(fetched.papers)
        complete = bool(sources) and all(fetched.complete for fetched in sources)
        if progress:
            progress('Deduplicating papers', len(records), len(records))
        papers = deduplicate_papers(records, self.deduplication)
        if progress:
            progress('Validating ownership', len(papers), len(papers))
        self.ownership_validator.validate(papers, profile)
        apply_policy(papers, self.raw_metadata)
        if confirmation == ResolutionStatus.AMBIGUOUS_CONTENDERS:
            hold_for_review(papers, len(selected.get('openalex', [])) or sum(map(len, selected.values())))
        return PaperCollectionResult(
            papers=papers,
            sources=sources,
            complete=complete,
            selected_author_ids=selected,
            identity_confirmation=confirmation,
            professor_identity_status=profile.status,
        )
