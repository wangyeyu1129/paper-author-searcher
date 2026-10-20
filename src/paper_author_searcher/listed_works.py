"""Fetch the works a person lists themselves that author-ID collection never returned.

OpenAlex files some of a common name's papers under author IDs nobody selected (Yang Liu, Huan Zhang, Wei Wang),
so collecting the selected IDs cannot find them. The person's own list (homepage, supplied ORCID) names them: each
listed work missing from the collection is looked up once and enters it under the work's name-compatible author,
with evidence saying why. Ownership decides afterwards (``OwnershipConfig.verify_listed_works``).
"""

import logging
from collections.abc import Iterable, Sequence
from urllib.parse import quote

from .config import DEFAULT_LISTED_WORKS, DEFAULT_MATCHING, ListedWorksConfig, MatchingConfig
from .evaluation_matching import bibliographic_name_agrees
from .http import SourceError
from .logging_config import log_isolated_failure
from .matching import ARXIV_DOI_PREFIX, name_similarity, normalize
from .models import PaperRecord, PaperSourceResult, ProfessorProfile
from .paper_providers import OpenAlexPaperProvider

logger = logging.getLogger(__name__)

LISTED_SOURCE_ID = 'listed-works'
RETRIEVED_EVIDENCE = "Retrieved because the researcher's own publication list names this work"


class ListedWorkRetriever:
    def __init__(
        self,
        papers: OpenAlexPaperProvider,
        config: ListedWorksConfig = DEFAULT_LISTED_WORKS,
        matching: MatchingConfig = DEFAULT_MATCHING,
    ) -> None:
        self.papers = papers
        self.config = config
        self.matching = matching

    def missing(self, profile: ProfessorProfile, records: Iterable[PaperRecord]) -> list[str]:
        covered: set[str] = set()
        for record in records:
            covered |= record.work_keys()
        return sorted(set(profile.listed_works) - covered)

    def retrieve(self, profile: ProfessorProfile, records: Sequence[PaperRecord]) -> PaperSourceResult:
        result = PaperSourceResult(source=self.papers.name, author_id=LISTED_SOURCE_ID)
        if not self.config.enabled:
            result.pagination_complete = result.complete = True
            return result
        missing = self.missing(profile, records)
        for key in missing[: self.config.maximum_lookups]:
            try:
                work = self._lookup(key)
            except SourceError as exc:
                log_isolated_failure(logger, 'Listed work %s lookup failed: %s', key, exc)
                result.metadata_errors.append({'paper_id': key, 'error': str(exc)})
                continue
            result.records_received += bool(work)
            author_id = self._target_author(work, profile) if work else None
            if work is None or author_id is None:
                continue
            record = self.papers.parse_work(work, author_id)
            record.attribution_evidence.append(RETRIEVED_EVIDENCE)
            result.papers.append(record)
        result.pagination_complete = True
        result.complete = len(missing) <= self.config.maximum_lookups and not result.metadata_errors
        return result

    def _lookup(self, key: str) -> dict | None:
        kind, _, value = key.partition(':')
        client, base, api_key = self.papers.provider.client, self.papers.provider.base_url, self.papers.provider.api_key
        fields = {'select': OpenAlexPaperProvider.FIELDS, 'api_key': api_key}
        if kind in ('doi', 'arxiv'):
            doi = value if kind == 'doi' else ARXIV_DOI_PREFIX + value
            try:
                return client.get(base + '/works/doi:' + quote(doi, safe='/'), fields)
            except SourceError as exc:
                if exc.status_code == 404:
                    return None  # not in OpenAlex
                raise
        data = client.get(
            base + '/works',
            {
                **fields,
                'filter': 'title.search:' + value.replace(',', ' '),
                'per-page': self.config.title_search_results,
            },
        )
        return next((work for work in data.get('results') or [] if normalize(work.get('title') or '') == value), None)

    def _target_author(self, work: dict, profile: ProfessorProfile) -> str | None:
        """The work's one author whose name is the person's; ambiguous or absent means no record."""
        matches = []
        for authorship in work.get('authorships') or []:
            author = authorship.get('author') or {}
            names = [author.get('display_name') or '', authorship.get('raw_author_name') or '']
            if author.get('id') and any(
                self._compatible(target, name) for target in profile.target_names() for name in names
            ):
                matches.append(author['id'])
        return matches[0] if len(set(matches)) == 1 else None

    def _compatible(self, target: str, name: str) -> bool:
        if not name:
            return False
        return name_similarity(
            target, name, self.matching
        ) >= self.matching.compatible_name or bibliographic_name_agrees(target, name)
