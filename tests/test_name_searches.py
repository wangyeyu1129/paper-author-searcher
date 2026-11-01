"""Name searches (Europe PMC, Crossref) and Europe PMC / CORE work lookups, with synthetic responses only."""

import unittest
from dataclasses import replace

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import DEFAULT_LISTED_WORKS, DEFAULT_NAME_SEARCHES
from paper_author_searcher.listed_works import AuthorMatcher
from paper_author_searcher.matching import institution_mentioned, school_initialism, school_named
from paper_author_searcher.models import ProfessorProfile
from paper_author_searcher.name_searches import (
    CoreWorkLookup,
    CrossrefAuthorSearch,
    EuropePmcAuthorSearch,
    EuropePmcWorkLookup,
    initials,
)
from paper_author_searcher.settings import Credentials, Settings

ORCID = '0000-0002-1825-0097'


def profile(orcid=None):
    return ProfessorProfile(
        name='Mei Lin',
        affiliations=['Example University'],
        author_ids={},
        identity_confidence=1.0,
        status='resolved',
        evidence=[],
        orcid=orcid,
        requested_name='Mei Lin',
        requested_university='Example University',
    )


def pmc_author(first, last, orcid=None, affiliation='Dept of Biology, Example University, USA'):
    author = {
        'fullName': f'{last} {first[0]}',
        'firstName': first,
        'lastName': last,
        'authorAffiliationDetailsList': {'authorAffiliation': [{'affiliation': affiliation}]},
    }
    if orcid:
        author['authorId'] = {'type': 'ORCID', 'value': orcid}
    return author


def pmc_result(identifier, *authors, title='A study of cells', doi=None):
    return {
        'id': identifier,
        'source': 'MED',
        'pmid': identifier,
        'doi': doi,
        'title': title + '.',
        'pubYear': '2024',
        'journalInfo': {'journal': {'title': 'Cell'}},
        'pubTypeList': {'pubType': ['research-article']},
        'authorList': {'author': list(authors)},
    }


class Client:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.requests = []

    def get(self, url, params=None, headers=None, xml=False):
        self.requests.append((url, dict(params or {}), dict(headers or {})))
        page = self.pages.pop(0) if self.pages else {}
        if isinstance(page, Exception):
            raise page
        return page


def pmc_page(results, cursor):
    return {'resultList': {'result': results}, 'nextCursorMark': cursor}


class EuropePmcSearchTests(unittest.TestCase):
    def search(self, client, **config):
        return EuropePmcAuthorSearch(
            client, 'https://pmc/search', replace(DEFAULT_NAME_SEARCHES, **config), current_year=2026
        )

    def test_query_names_family_initials_institution_and_years(self):
        query = self.search(Client()).query(profile())
        self.assertEqual(query, 'AUTH:"Lin M" AND AFF:"Example University" AND PUB_YEAR:[2021 TO 2026]')
        self.assertEqual(initials('Jean-Luc Marie'), 'JLM')

    def test_records_name_the_person_by_first_name_and_page_until_the_cursor_repeats(self):
        client = Client(
            pmc_page([pmc_result('1', pmc_author('Mei', 'Lin'), pmc_author('Ana', 'Other'))], 'c2'),
            pmc_page([pmc_result('2', pmc_author('Ming', 'Lin'))], 'c2'),
        )
        result = self.search(client).collect(profile())
        self.assertTrue(result.complete)
        self.assertEqual([record.source_paper_id for record in result.papers], ['MED:1'])
        record = result.papers[0]
        self.assertEqual(record.source_author_id, 'name:mei lin')
        self.assertEqual(record.title, 'A study of cells')
        self.assertEqual(record.external_ids, {'pmid': '1'})
        self.assertIn('Example University', record.author_details[0]['affiliations'][0]['raw_affiliation_string'])
        self.assertEqual(client.requests[1][1]['cursorMark'], 'c2')
        self.assertEqual(client.requests[0][1]['pageSize'], DEFAULT_NAME_SEARCHES.europepmc_page_size)

    def test_a_byline_orcid_other_than_the_persons_excludes_that_author(self):
        client = Client(pmc_page([pmc_result('1', pmc_author('Mei', 'Lin', orcid='0000-0001-0000-0001'))], None))
        self.assertEqual(self.search(client).collect(profile(orcid=ORCID)).papers, [])

    def test_the_result_limit_leaves_the_search_incomplete_and_errors_are_isolated(self):
        client = Client(pmc_page([pmc_result('1', pmc_author('Mei', 'Lin'))], 'next'))
        limited = self.search(client, maximum_results=1).collect(profile())
        self.assertFalse(limited.complete)
        failed = self.search(Client(RuntimeError('HTTP 503'))).collect(profile())
        self.assertEqual((failed.error, failed.papers), ('HTTP 503', []))


def crossref_item(doi, given, affiliation):
    return {
        'DOI': doi,
        'title': ['A study of materials'],
        'issued': {'date-parts': [[2024]]},
        'author': [{'given': given, 'family': 'Lin', 'affiliation': [{'name': affiliation}]}],
    }


def crossref_page(items, cursor='next'):
    return {'message': {'items': items, 'next-cursor': cursor}}


class CrossrefSearchTests(unittest.TestCase):
    def search(self, client, **config):
        return CrossrefAuthorSearch(
            client, 'https://api.crossref.org/', replace(DEFAULT_NAME_SEARCHES, **config), mailto='a@b.org'
        )

    def test_only_works_whose_byline_prints_the_institution_count(self):
        client = Client(
            crossref_page(
                [
                    crossref_item('10.1/a', 'Mei', 'School of Physics, Example University, City'),
                    crossref_item('10.1/b', 'Mei', 'Other College'),
                ]
            ),
            crossref_page([]),
        )
        result = self.search(client).collect(profile())
        self.assertEqual([record.doi for record in result.papers], ['10.1/a'])
        self.assertEqual(result.papers[0].source, 'crossref')
        params = client.requests[0][1]
        self.assertEqual((params['query.author'], params['query.affiliation']), ('Mei Lin', 'Example University'))
        self.assertEqual(params['mailto'], 'a@b.org')

    def test_paging_stops_at_the_first_page_that_yields_nothing(self):
        client = Client(crossref_page([crossref_item('10.1/b', 'Mei', 'Other College')]), crossref_page([]))
        result = self.search(client).collect(profile())
        self.assertEqual((result.pages_fetched, result.complete), (1, True))

    def test_the_affiliation_rule_can_be_turned_off(self):
        client = Client(crossref_page([crossref_item('10.1/b', 'Mei', 'Other College')], cursor=None))
        result = self.search(client, crossref_require_affiliation=False).collect(profile())
        self.assertEqual(len(result.papers), 1)


class LookupTests(unittest.TestCase):
    authors = AuthorMatcher()

    def test_europepmc_lookup_by_doi_and_exact_title(self):
        client = Client(
            pmc_page([pmc_result('7', pmc_author('Mei', 'Lin'), doi='10.1/x')], None),
            pmc_page([pmc_result('8', pmc_author('Mei', 'Lin'), title='Other title')], None),
        )
        lookup = EuropePmcWorkLookup(client, 'https://pmc', DEFAULT_LISTED_WORKS, self.authors)
        self.assertEqual(lookup.find('doi:10.1/x', profile()).doi, '10.1/x')
        self.assertEqual(client.requests[0][1]['query'], 'DOI:"10.1/x"')
        self.assertIsNone(lookup.find('title:a study of cells', profile()))
        self.assertIsNone(lookup.find('arxiv:2401.00001', profile()))

    def test_core_lookup_sends_its_key_and_reads_works(self):
        work = {'id': 99, 'title': 'A study of cells', 'authors': [{'name': 'Lin, Mei'}, {'name': 'Mei Lin'}]}
        client = Client({'results': [work]})
        lookup = CoreWorkLookup(client, 'https://core/v3/', 'secret', DEFAULT_LISTED_WORKS, self.authors)
        record = lookup.find('title:a study of cells', profile())
        self.assertEqual(client.requests[0][0], 'https://core/v3/search/works')
        self.assertEqual(client.requests[0][2], {'Authorization': 'Bearer secret'})
        self.assertIsNone(record)  # two byline entries compatible with the person: ambiguous, no record
        single = Client({'results': [dict(work, authors=[{'name': 'Mei Lin'}], doi='10.1/C')]})
        found = CoreWorkLookup(single, 'https://core/v3', 'k', DEFAULT_LISTED_WORKS, self.authors).find(
            'doi:10.1/c', profile()
        )
        self.assertEqual((found.source, found.doi), ('core', '10.1/c'))


class MatchingTests(unittest.TestCase):
    def test_a_school_has_initials_only_when_they_are_long_enough_to_tell(self):
        self.assertEqual(school_initialism('Massachusetts Institute of Technology'), 'MIT')
        self.assertEqual(school_initialism('University of California, Los Angeles'), 'UCLA')
        self.assertEqual(school_initialism('University of Illinois Urbana-Champaign'), 'UIUC')
        self.assertIsNone(school_initialism('Stanford University'))

    def test_a_page_names_a_school_in_full_or_by_its_exact_initials(self):
        mit = ['Massachusetts Institute of Technology']
        self.assertTrue(school_named(mit, 'Assistant Professor at MIT.'))
        self.assertTrue(school_named(mit, 'Massachusetts Institute of Technology, Cambridge'))
        self.assertFalse(school_named(mit, 'Arbeit mit Studierenden'))
        self.assertFalse(school_named(['Stanford University'], 'University of Toronto'))

    def test_institution_mentioned_needs_every_distinctive_word(self):
        self.assertTrue(institution_mentioned('Stanford University', 'Dept. of Genetics, Stanford University, CA'))
        self.assertFalse(institution_mentioned('Stanford University', 'University of Toronto'))
        self.assertFalse(institution_mentioned('University of the', 'anything'))


class CompositionTests(unittest.TestCase):
    def test_name_searches_and_keyed_lookups_follow_configuration(self):
        container = ServiceContainer(Settings(), RunOptions(mode='metadata'))
        self.assertEqual([search.name for search in container.name_searches], ['arxiv', 'europepmc', 'crossref'])
        lookups = [lookup.name for lookup in container.listed_work_retriever.lookups]
        self.assertNotIn('core', lookups)
        self.assertIn('europepmc', lookups)
        keyed = replace(Settings(), credentials=Credentials(core_api_key='k'))
        keyed_lookups = ServiceContainer(keyed, RunOptions(mode='metadata')).listed_work_retriever.lookups
        self.assertEqual(keyed_lookups[-1].name, 'core')


if __name__ == '__main__':
    unittest.main()
