"""Confirmed identity selection and conservative, traceable metadata deduplication."""

import logging
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Any, Protocol

from .config import DEFAULT_COLLECTION, DEFAULT_DEDUPLICATION, DeduplicationConfig, RawMetadataPolicy
from .contender_adjudication import ContenderEvidence, DepartmentAdjudicator
from .fragment_merging import Fragment, FragmentMerger
from .listed_works import ListedWorkRetriever
from .logging_config import log_isolated_failure
from .models import Paper, PaperCollectionResult, PaperRecord, PaperSourceResult, ProfessorProfile
from .paper_authorship import canonical_author_id
from .paper_deduplication import deduplicate_papers
from .paper_ownership import PaperOwnershipValidator
from .paper_providers import PaperProvider
from .raw_metadata import apply_policy
from .status import IdentityConfirmation, OwnershipStatus, ResolutionStatus

logger = logging.getLogger(__name__)


class NameSearch(Protocol):
    """A paper source keyed by the person's name instead of an author ID (arXiv author search)."""

    name: str

    def author_key(self, profile: ProfessorProfile) -> str: ...

    def collect(self, profile: ProfessorProfile) -> PaperSourceResult: ...


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
        ownership_validator: PaperOwnershipValidator | None = None,
        deduplication: DeduplicationConfig = DEFAULT_DEDUPLICATION,
        concurrency: Mapping[str, int] | None = None,
        raw_metadata: str = RawMetadataPolicy.FULL,
        listed_works: ListedWorkRetriever | None = None,
        adjudicator: DepartmentAdjudicator | None = None,
        name_searches: Sequence[NameSearch] = (),
        fragment_merger: FragmentMerger | None = None,
    ) -> None:
        self.providers = list(providers)
        # Optional: fetch works the person lists themselves that no selected author ID returned.
        self.listed_works = listed_works
        # Optional: settle collected contenders by the requested department on their own papers.
        self.adjudicator = adjudicator
        # Optional: sources searched by the person's name; their records join under a name key.
        self.name_searches = list(name_searches)
        # Optional: merge the person's split author profiles found by their verified coauthor network.
        self.fragment_merger = fragment_merger
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
        self, profile: ProfessorProfile, selected_author: tuple[str, str] | None
    ) -> tuple[dict[str, list[str]], str]:
        if selected_author is not None:
            source, author_id = selected_author
            known_ids = profile.author_id_aliases.get(source, [])
            representative = profile.author_ids.get(source)
            if author_id not in known_ids and author_id != representative:
                raise IdentityConfirmationRequired('Selected Author ID is not in this candidate')
            # Manual confirmation applies to this ID only, never competing candidates.
            return {source: [author_id]}, IdentityConfirmation.MANUAL
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
        return selected, IdentityConfirmation.RESOLVER

    @staticmethod
    def _settle(
        profile: ProfessorProfile, winner: ContenderEvidence, records: list[PaperRecord]
    ) -> tuple[ProfessorProfile, dict[str, list[str]], list[PaperRecord]]:
        """Narrow the merged contender profile to the adjudicated contender."""
        selected = {source: sorted(ids) for source, ids in winner.author_ids.items() if ids}
        wanted = {(source, canonical_author_id(source, value)) for source, ids in selected.items() for value in ids}
        narrowed = replace(
            profile,
            author_ids={source: ids[0] for source, ids in selected.items()},
            author_id_aliases=selected,
            status=ResolutionStatus.RESOLVED,
            contender_ids=[],
            evidence=[
                *profile.evidence,
                f'Department adjudication: {winner.share:.0%} of {winner.mentions} unit mentions on this '
                f"profile's papers name {profile.requested_department}; no rival profile does",
            ],
        )
        kept = [
            record
            for record in records
            if (record.source, canonical_author_id(record.source, record.source_author_id)) in wanted
        ]
        return narrowed, selected, kept

    def _merge_fragments(
        self,
        profile: ProfessorProfile,
        records: list[PaperRecord],
        available: Mapping[str, PaperProvider],
        selected: dict[str, list[str]],
    ) -> tuple[ProfessorProfile, list[PaperSourceResult]]:
        """Collect the split profiles whose coauthors match the person's verified papers (a provisional
        validation of what was collected so far); they become part of the selected identity."""
        fragments = [
            fragment
            for fragment in map(Fragment.from_dict, profile.split_fragments)
            if fragment.source in available and fragment.author_id not in selected.get(fragment.source, [])
        ]
        if not fragments or self.fragment_merger is None:
            return profile, []
        provisional = deduplicate_papers(records, self.deduplication)
        self.ownership_validator.validate(provisional, profile)
        verified = [paper for paper in provisional if paper.ownership_status == OwnershipStatus.VERIFIED]
        fetched = {(item.source, item.author_id): available[item.source].collect(item.author_id) for item in fragments}
        # Each fragment's own papers are its evidence; only accepted fragments keep them.
        papered = [
            fragment.with_papers(fetched[fragment.source, fragment.author_id].papers, profile.target_names())
            for fragment in fragments
        ]
        decisions = [item for item in self.fragment_merger.select(profile, verified, papered) if item.accepted]
        merged = []
        for decision in decisions:
            fragment = decision.fragment
            merged.append(fetched[fragment.source, fragment.author_id])
            selected[fragment.source] = sorted({*selected.get(fragment.source, []), fragment.author_id})
        if not decisions:
            return profile, []
        aliases = {**profile.author_id_aliases, **{source: list(ids) for source, ids in selected.items()}}
        evidence = [*profile.evidence, *(decision.evidence() for decision in decisions)]
        return replace(profile, author_id_aliases=aliases, evidence=evidence), merged

    def collect(
        self,
        profile: ProfessorProfile,
        selected_author: tuple[str, str] | None = None,
        progress: Callable[..., None] | None = None,
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
        if confirmation == ResolutionStatus.AMBIGUOUS_CONTENDERS and self.adjudicator is not None:
            winner = self.adjudicator.choose(profile, records)
            if winner is not None:
                profile, selected, records = self._settle(profile, winner, records)
                confirmation = IdentityConfirmation.DEPARTMENT
        if confirmation != ResolutionStatus.AMBIGUOUS_CONTENDERS:
            for search in self.name_searches:
                found = search.collect(profile)
                sources.append(found)
                records.extend(found.papers)
                key = search.author_key(profile)
                selected[search.name] = sorted({*selected.get(search.name, []), key})
                aliases = {**profile.author_id_aliases, search.name: selected[search.name]}
                profile = replace(profile, author_id_aliases=aliases)
        if self.listed_works is not None and confirmation != ResolutionStatus.AMBIGUOUS_CONTENDERS:
            listed = self.listed_works.retrieve(profile, records)
            sources.append(listed)
            records.extend(listed.papers)
        if self.fragment_merger is not None and confirmation != ResolutionStatus.AMBIGUOUS_CONTENDERS:
            profile, merged = self._merge_fragments(profile, records, available, selected)
            sources.extend(merged)
            records.extend(paper for fetched in merged for paper in fetched.papers)
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
