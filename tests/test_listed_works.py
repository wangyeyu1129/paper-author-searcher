"""Works on the person's own list: retrieval of missing ones, the ownership claim, contender adjudication."""

import dataclasses
import unittest

from paper_author_searcher.config import DEFAULT_COLLECTION, ListedWorksConfig, OwnershipConfig
from paper_author_searcher.contender_adjudication import DepartmentAdjudicator
from paper_author_searcher.http import SourceError
from paper_author_searcher.listed_works import (
    RETRIEVED_EVIDENCE,
    ArxivWorkLookup,
    AuthorMatcher,
    CrossrefWorkLookup,
    ListedWorkRetriever,
    OpenAlexWorkLookup,
)
from paper_author_searcher.models import PaperRecord, ProfessorProfile
from paper_author_searcher.paper_deduplication import deduplicate_papers
from paper_author_searcher.paper_ownership import LISTED_CLAIM, PaperOwnershipValidator
from paper_author_searcher.paper_providers import OpenAlexPaperProvider


def work(title, doi, author_id='https://openalex.org/A9', name='Mei Lin'):
    return {
        'id': 'https://openalex.org/W' + title.replace(' ', ''),
        'title': title,
        'doi': 'https://doi.org/' + doi,
        'publication_year': 2024,
        'type': 'article',
        'authorships': [
            {'author': {'id': author_id, 'display_name': name}, 'raw_author_name': name},
            {'author': {'id': 'https://openalex.org/A2', 'display_name': 'Jane Smith'}},
        ],
    }


class Client:
    def __init__(self, works):
        self.works = works
        self.requests = []

    def get(self, url, params=None, headers=None, xml=False):
        self.requests.append(url)
        if '/works/doi:' in url:
            doi = url.rsplit('doi:', 1)[-1]
            if doi not in self.works:
                raise SourceError('HTTP 404', status_code=404)
            return self.works[doi]
        title = (params or {}).get('filter', '').removeprefix('title.search:')
        return {'results': [item for item in self.works.values() if item['title'].lower() == title.lower()]}


class Provider:
    def __init__(self, client):
        self.client, self.base_url, self.api_key = client, 'https://api.openalex.org', None


def profile(listed=(), **values):
    data = {
        'name': 'Mei Lin',
        'affiliations': ['Example University'],
        'author_ids': {'openalex': 'A1'},
        'identity_confidence': 0.9,
        'status': 'resolved',
        'evidence': [],
        'author_id_aliases': {'openalex': ['A1']},
        'listed_works': list(listed),
        'requested_name': 'Mei Lin',
    }
    data.update(values)
    return ProfessorProfile(**data)


def retriever(works, *extra, **config):
    settings = ListedWorksConfig(**config)
    openalex = OpenAlexWorkLookup(OpenAlexPaperProvider(Provider(Client(works))), settings, AuthorMatcher())
    return ListedWorkRetriever([openalex, *extra], settings)


ARXIV_FEED = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2401.01234v2</id><title>Preprint Only Study</title>
<published>2024-01-03T00:00:00Z</published>
<author><name>Mei Lin</name><arxiv:affiliation>Example University</arxiv:affiliation></author>
<author><name>Jane Smith</name></author></entry></feed>"""


class FeedClient:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None, xml=False):
        import xml.etree.ElementTree as ET

        self.calls.append(params)
        if xml:
            return ET.fromstring(ARXIV_FEED)
        title = (params or {}).get('query.bibliographic', '')
        if title.lower() == 'publisher only study':
            author = {'given': 'Mei', 'family': 'Lin', 'affiliation': [{'name': 'Dept. of X, Example University'}]}
            item = {
                'DOI': '10.2000/pub',
                'title': ['Publisher Only Study'],
                'author': [author],
                'issued': {'date-parts': [[2023]]},
            }
            return {'message': {'items': [item]}}
        return {'message': {'items': []}}


class RetrievalTests(unittest.TestCase):
    def test_missing_listed_works_are_fetched_under_the_name_compatible_author(self):
        works = {'10.1000/missing': work('Missing Study', '10.1000/missing')}
        result = retriever(works).retrieve(profile(['doi:10.1000/missing', 'doi:10.1000/unknown']), [])
        self.assertEqual(len(result.papers), 1)
        record = result.papers[0]
        self.assertEqual(record.source_author_id, 'https://openalex.org/A9')
        self.assertTrue(record.attribution_evidence[-1].startswith(RETRIEVED_EVIDENCE))
        self.assertEqual(result.metadata_errors, [])  # an unknown DOI is a 404, not a failure

    def test_works_already_collected_are_not_fetched_again(self):
        collected = OpenAlexPaperProvider(Provider(Client({}))).parse_work(work('Known', '10.1000/known'), 'A1')
        fetcher = retriever({'10.1000/known': work('Known', '10.1000/known')})
        self.assertEqual(fetcher.missing(profile(['doi:10.1000/known']), [collected]), [])

    def test_exact_titles_are_looked_up_and_lookups_are_capped(self):
        works = {'10.1000/t': work('Title Only Study', '10.1000/t')}
        self.assertEqual(len(retriever(works).retrieve(profile(['title:title only study']), []).papers), 1)
        capped = retriever(works, maximum_lookups=0).retrieve(profile(['title:title only study']), [])
        self.assertEqual((capped.papers, capped.complete), ([], False))

    def test_the_chain_falls_through_to_crossref_and_arxiv(self):
        feed = FeedClient()
        crossref = CrossrefWorkLookup(feed, 'https://api.crossref.org', ListedWorksConfig(), AuthorMatcher())
        arxiv = ArxivWorkLookup(
            feed, 'https://export.arxiv.org/api/query', ListedWorksConfig(arxiv_request_interval=0), AuthorMatcher()
        )
        chain = retriever({}, crossref, arxiv)
        result = chain.retrieve(profile(['title:publisher only study', 'title:preprint only study']), [])
        by_source = {record.source: record for record in result.papers}
        self.assertEqual(set(by_source), {'crossref', 'arxiv'})
        self.assertEqual(
            by_source['crossref'].author_details[0]['affiliations'][0]['raw_affiliation_string'],
            'Dept. of X, Example University',
        )
        self.assertEqual(by_source['arxiv'].external_ids, {'arxiv': '2401.01234'})
        self.assertTrue(all('found in' in record.attribution_evidence[-1] for record in result.papers))

    def test_a_failing_source_is_skipped_not_fatal(self):
        class Broken:
            name = 'broken'

            def find(self, key, target):
                raise SourceError('HTTP 500', status_code=500)

        works = {'10.1000/missing': work('Missing Study', '10.1000/missing')}
        settings = ListedWorksConfig()
        openalex = OpenAlexWorkLookup(OpenAlexPaperProvider(Provider(Client(works))), settings, AuthorMatcher())
        result = ListedWorkRetriever([Broken(), openalex], settings).retrieve(profile(['doi:10.1000/missing']), [])
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(result.metadata_errors[0]['source'], 'broken')

    def test_a_work_without_a_compatible_author_is_skipped(self):
        works = {'10.1000/x': work('Other', '10.1000/x', name='Someone Else')}
        self.assertEqual(retriever(works).retrieve(profile(['doi:10.1000/x']), []).papers, [])


class ClaimTests(unittest.TestCase):
    def record(self, author_id='A9'):
        provider = OpenAlexPaperProvider(Provider(Client({})))
        return provider.parse_work(
            work('Listed Study', '10.1000/listed', 'https://openalex.org/' + author_id), author_id
        )

    def validate(self, record, target, config=None):
        papers = deduplicate_papers([record])
        PaperOwnershipValidator(config or OwnershipConfig()).validate(papers, target)
        return papers[0]

    def test_a_listed_work_is_verified_even_under_an_unselected_author_id(self):
        paper = self.validate(self.record(), profile(['doi:10.1000/listed']))
        self.assertEqual(paper.ownership_status, 'verified')
        self.assertTrue(any(LISTED_CLAIM in line for line in paper.ownership_evidence))

    def test_the_claim_needs_the_listing_and_can_be_disabled(self):
        self.assertEqual(self.validate(self.record(), profile()).ownership_status, 'needs_review')
        off = OwnershipConfig(verify_listed_works=False)
        self.assertEqual(
            self.validate(self.record(), profile(['doi:10.1000/listed']), off).ownership_status, 'needs_review'
        )


class AdjudicationTests(unittest.TestCase):
    def records(self, author_id, unit, count):
        result = []
        for index in range(count):
            detail = {
                'author_id': author_id,
                'name': 'Xiang Zhou',
                'affiliations': [{'raw_affiliation_string': f'{unit}, Harvard University'}],
            }
            result.append(
                PaperRecord(
                    f'Paper {author_id} {index}',
                    ['Xiang Zhou'],
                    2024,
                    None,
                    None,
                    None,
                    'openalex',
                    f'W{author_id}{index}',
                    author_id,
                    None,
                    author_details=[detail],
                )
            )
        return result

    def target(self):
        return profile(
            name='Xiang Zhou',
            status='ambiguous_contenders',
            requested_department='Sociology',
            contender_ids=[{'openalex': ['S1']}, {'openalex': ['G1']}, {'openalex': ['D1']}],
        )

    def test_the_one_contender_naming_the_department_wins(self):
        records = [*self.records('S1', 'Department of Sociology', 6), *self.records('G1', 'School of Geology', 6)]
        winner = DepartmentAdjudicator().choose(self.target(), records)
        self.assertEqual(winner.author_ids, {'openalex': ['S1']})

    def test_no_winner_when_a_rival_also_names_it_or_evidence_is_thin(self):
        rivals = [*self.records('S1', 'Department of Sociology', 6), *self.records('G1', 'Department of Sociology', 6)]
        self.assertIsNone(DepartmentAdjudicator().choose(self.target(), rivals))
        thin = self.records('S1', 'Department of Sociology', 2)
        self.assertIsNone(DepartmentAdjudicator().choose(self.target(), thin))
        off = dataclasses.replace(DEFAULT_COLLECTION, contender_department_adjudication=False)
        records = [*self.records('S1', 'Department of Sociology', 6), *self.records('G1', 'School of Geology', 6)]
        self.assertIsNone(DepartmentAdjudicator(off).choose(self.target(), records))


if __name__ == '__main__':
    unittest.main()
