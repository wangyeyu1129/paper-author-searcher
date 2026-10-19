"""Source adapters for identity metadata. No PDFs or publication bodies."""

from typing import List, Protocol

from .config import DEFAULT_MATCHING, DEFAULT_SOURCES
from .http import HttpClient, SourceError
from .matching import best_similarity, canonical_doi, institution_similarity, name_similarity
from .models import AuthorCandidate, ProfessorQuery


class AuthorProvider(Protocol):
    name: str

    def search(self, query: ProfessorQuery) -> List[AuthorCandidate]: ...


def _display_names(items):
    names = []
    for item in items or []:
        if item.get('display_name'):
            names.append(item['display_name'])
    return names


class OpenAlexProvider:
    name = 'openalex'
    WORK_FIELDS = 'id,doi,title,authorships,topics'
    TOPIC_LEVELS = ('subfield', 'field', 'domain')

    def __init__(
        self,
        client=None,
        api_key=None,
        limit=None,
        enrich_limit=None,
        works_limit=None,
        source_config=DEFAULT_SOURCES,
        matching_config=DEFAULT_MATCHING,
    ):
        self.client = client or HttpClient()
        self.api_key = api_key
        self.base_url = source_config.openalex_url.rstrip('/')
        self.limit = source_config.author_limit if limit is None else limit
        self.enrich_limit = source_config.enrichment_limit if enrich_limit is None else enrich_limit
        self.works_limit = source_config.works_limit if works_limit is None else works_limit
        self.matching_config = matching_config
        self.source_config = source_config

    def search(self, query):
        data = self.client.get(
            self.base_url + '/authors',
            {'search': query.name, 'per-page': self.limit, 'api_key': self.api_key},
        )
        candidates = {}
        scoped = set()
        for item in data.get('results', []):
            candidate = self._parse_author(item)
            candidates[candidate.author_id] = candidate
        try:
            for item in self._institution_scoped_authors(query):
                candidate = candidates.setdefault(item['id'].rsplit('/', 1)[-1], self._parse_author(item))
                scoped.add(candidate.author_id)
        except SourceError as exc:
            for candidate in candidates.values():
                candidate.metadata_errors.append('institution-scoped search failed: ' + str(exc))
        candidates = list(candidates.values())
        for candidate in self._enrichment_candidates(query, candidates, scoped):
            try:
                self._enrich(candidate)
            except SourceError as exc:
                candidate.metadata_errors.append(str(exc))
        return candidates

    def _parse_author(self, item):
        current = _display_names(item.get('last_known_institutions'))
        historical = []
        for affiliation in item.get('affiliations') or []:
            institution = affiliation.get('institution') or {}
            if institution.get('display_name'):
                historical.append(institution['display_name'])
        topics = []
        for topic in item.get('topics') or item.get('x_concepts') or []:
            if topic.get('display_name'):
                topics.append(topic['display_name'])
            for level in self.TOPIC_LEVELS:
                value = topic.get(level) or {}
                if value.get('display_name'):
                    topics.append(value['display_name'])
        return AuthorCandidate(
            source=self.name,
            author_id=item['id'].rsplit('/', 1)[-1],
            name=item['display_name'],
            affiliations=sorted(set(current + historical)),
            current_affiliations=current,
            aliases=item.get('display_name_alternatives') or [],
            topics=topics,
            orcid=item.get('orcid'),
            url=item['id'],
        )

    def lookup(self, author_id):
        """Fetch one author by ID (used when an identity anchor names a profile the search missed)."""
        item = self.client.get(self.base_url + '/authors/' + author_id, {'api_key': self.api_key})
        candidate = self._parse_author(item)
        try:
            self._enrich(candidate)
        except SourceError as exc:
            candidate.metadata_errors.append(str(exc))
        return candidate

    def _institution_ids(self, query):
        universities = [query.university, *list(query.university_aliases)]
        data = self.client.get(
            self.base_url + '/institutions',
            {
                'search': query.university,
                'per-page': self.source_config.institution_limit,
                'select': 'id,display_name',
                'api_key': self.api_key,
            },
        )
        ids = []
        for item in data.get('results', []):
            similarity = best_similarity(
                universities,
                [item.get('display_name') or ''],
                lambda a, b: institution_similarity(a, b, self.matching_config),
            )
            if similarity >= self.matching_config.matched_school:
                ids.append(item['id'].rsplit('/', 1)[-1])
        return ids

    def _institution_scoped_authors(self, query):
        """Authors with the name who were ever affiliated with the requested institution."""
        if not self.source_config.institution_scoped_search:
            return []
        institution_ids = self._institution_ids(query)
        if not institution_ids:
            return []
        data = self.client.get(
            self.base_url + '/authors',
            {
                'search': query.name,
                'per-page': self.limit,
                'api_key': self.api_key,
                'filter': 'affiliations.institution.id:' + '|'.join(institution_ids),
            },
        )
        return data.get('results', [])

    def _enrichment_candidates(self, query, candidates, scoped=frozenset()):
        scored = []
        for candidate in candidates:
            similarity = best_similarity(
                [query.name],
                [candidate.name, *candidate.aliases],
                lambda a, b: name_similarity(a, b, self.matching_config),
            )
            if similarity >= self.matching_config.compatible_name:
                scored.append((similarity, candidate))
        # Institution-scoped matches are enriched first: they are the plausible targets.
        scored.sort(key=lambda item: (item[1].author_id not in scoped, -item[0]))
        return [candidate for _, candidate in scored[: self.enrich_limit]]

    def _enrich(self, candidate):
        params = {
            'filter': 'authorships.author.id:' + candidate.author_id,
            'per-page': self.works_limit,
            'sort': 'cited_by_count:desc',
            'select': self.WORK_FIELDS,
            'api_key': self.api_key,
        }
        data = self.client.get(self.base_url + '/works', params)
        for work in data.get('results', []):
            if work.get('doi'):
                candidate.paper_ids.append(canonical_doi(work['doi']))
            for authorship in work.get('authorships') or []:
                self._add_authorship(candidate, authorship)
            candidate.topics.extend(_display_names(work.get('topics')))
        candidate.affiliations = sorted(set(candidate.affiliations))

    @staticmethod
    def _add_authorship(candidate, authorship):
        author = authorship.get('author') or {}
        author_id = (author.get('id') or '').rsplit('/', 1)[-1]
        if author_id == candidate.author_id:
            candidate.affiliations.extend(_display_names(authorship.get('institutions')))
            if not candidate.orcid:
                candidate.orcid = author.get('orcid')
        elif author.get('display_name'):
            candidate.coauthors.append(author['display_name'])


class SemanticScholarProvider:
    name = 'semantic_scholar'
    AUTHOR_FIELDS = 'name,affiliations,externalIds,url,' 'papers.title,papers.externalIds,papers.authors'

    def __init__(self, client=None, api_key=None, limit=None, source_config=DEFAULT_SOURCES):
        self.client = client or HttpClient()
        self.api_key = api_key
        self.base_url = source_config.semantic_scholar_url.rstrip('/')
        self.limit = source_config.author_limit if limit is None else limit

    def search(self, query):
        headers = {}
        if self.api_key:
            headers['x-api-key'] = self.api_key
        data = self.client.get(
            self.base_url + '/author/search',
            {'query': query.name, 'limit': self.limit, 'fields': self.AUTHOR_FIELDS},
            headers,
        )
        candidates = []
        for item in data.get('data', []):
            candidates.append(self._parse_author(item))
        return candidates

    def lookup(self, author_id):
        headers = {'x-api-key': self.api_key} if self.api_key else {}
        item = self.client.get(self.base_url + '/author/' + str(author_id), {'fields': self.AUTHOR_FIELDS}, headers)
        return self._parse_author(item)

    def _parse_author(self, item):
        dois = []
        topics = []
        coauthors = []
        for paper in item.get('papers') or []:
            doi = (paper.get('externalIds') or {}).get('DOI')
            if doi:
                dois.append(canonical_doi(doi))
            if paper.get('title'):
                topics.append(paper['title'])
            for author in paper.get('authors') or []:
                is_candidate = str(author.get('authorId')) == str(item['authorId'])
                if author.get('name') and not is_candidate:
                    coauthors.append(author['name'])
        return AuthorCandidate(
            source=self.name,
            author_id=item['authorId'],
            name=item['name'],
            affiliations=item.get('affiliations') or [],
            topics=topics,
            orcid=(item.get('externalIds') or {}).get('ORCID'),
            paper_ids=dois,
            url=item.get('url'),
            coauthors=coauthors,
        )


class DBLPProvider:
    name = 'dblp'

    def __init__(self, client=None, limit=None, source_config=DEFAULT_SOURCES):
        self.client = client or HttpClient()
        self.base_url = source_config.dblp_url.rstrip('/')
        self.limit = source_config.dblp_author_limit if limit is None else limit

    def search(self, query):
        data = self.client.get(
            self.base_url + '/search/author/api',
            {'q': query.name, 'format': 'json', 'h': self.limit},
        )
        hits = data.get('result', {}).get('hits', {}).get('hit', [])
        if isinstance(hits, dict):
            hits = [hits]
        candidates = []
        for hit in hits:
            info = hit['info']
            candidate = AuthorCandidate(
                source=self.name,
                author_id=self._author_id(info['url']),
                name=info['author'],
                url=info['url'],
            )
            try:
                root = self.client.get(
                    self.base_url + '/pid/' + candidate.author_id + '.xml',
                    xml=True,
                )
                self._add_person_metadata(candidate, root)
                self._add_publication_metadata(candidate, root)
            except SourceError as exc:
                candidate.metadata_errors.append(str(exc))
            candidates.append(candidate)
        return candidates

    @staticmethod
    def _author_id(url):
        author_id = url.split('/pid/')[-1]
        if author_id.endswith('.html'):
            author_id = author_id[: -len('.html')]
        return author_id

    @staticmethod
    def _add_person_metadata(candidate, root):
        for person in root.findall('person'):
            for author in person.findall('author'):
                if author.text:
                    candidate.aliases.append(author.text)
            for note in person.findall('note'):
                if note.get('type') == 'affiliation' and note.text:
                    candidate.affiliations.append(note.text)
            for url in person.findall('url'):
                if url.text and 'orcid.org/' in url.text:
                    candidate.orcid = url.text

    @staticmethod
    def _add_publication_metadata(candidate, root):
        for record in root.findall('r'):
            for author in record.findall('.//author'):
                if author.text and author.text not in candidate.aliases:
                    candidate.coauthors.append(author.text)
            title = record.find('.//title')
            if title is not None:
                candidate.topics.append(''.join(title.itertext()))
            for reference in record.findall('.//ee'):
                if reference.text and 'doi.org/' in reference.text:
                    candidate.paper_ids.append(canonical_doi(reference.text))
