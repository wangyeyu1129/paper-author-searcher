"""Phase 1 identity inputs: profile links, ORCID registry search, page discovery and ranking (synthetic fixtures)."""

import unittest
from dataclasses import replace

from paper_author_searcher import ProfessorQuery as Q
from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import DEFAULT_DISCOVERY, DiscoveryConfig, ProfileLinkConfig
from paper_author_searcher.http import SourceError, URLProbe
from paper_author_searcher.identity_anchors import RecordedAnchorFinder, ScholarlyAnchorFinder, publication_links
from paper_author_searcher.identity_discovery import (
    DiscoveredPage,
    Discovery,
    IdentityDiscovery,
    OrcidRegistrySearch,
    OrcidResearcherUrls,
    PageRanker,
    WebPageSearch,
    split_name,
)
from paper_author_searcher.precision_guards import DepartmentMatcher
from paper_author_searcher.profile_links import ProfileLinkParser
from paper_author_searcher.settings import Credentials, Settings
from paper_author_searcher.source_catalog import WEB_SEARCH
from paper_author_searcher.status import AnchorKind
from paper_author_searcher.web_search import BraveSearch, ExaSearch, SearchHit, TavilySearch

ORCID = '0000-0002-1825-0097'


def query(**changes):
    return Q('Mei Lin', 'Example University', **changes)


class Client:
    """Answers GETs by URL fragment, serves pages by URL and records every request."""

    def __init__(self, answers=None, pages=None, post=None):
        self.answers = answers or {}
        self.pages = pages or {}
        self.post = post or {}
        self.requests = []

    def get(self, url, params=None, headers=None, xml=False):
        self.requests.append((url, dict(params or {}), dict(headers or {})))
        for fragment, answer in self.answers.items():
            if fragment in url:
                return answer(params) if callable(answer) else answer
        return {}

    def post_json(self, url, payload, params=None, headers=None, expected_type=dict):
        self.requests.append((url, payload, dict(headers or {})))
        return self.post

    def probe(self, url, maximum_bytes=1024, html_bytes=0, maximum_redirects=5):
        if url not in self.pages:
            raise SourceError(f'{url} HTTP 404', status_code=404)
        return URLProbe(url, 200, 'text/html; charset=utf-8', {}, self.pages[url].encode('utf-8'))


def registry_person(orcid, given='Mei', family='Lin', institutions=('Example University',)):
    return {'orcid-id': orcid, 'given-names': given, 'family-names': family, 'institution-name': list(institutions)}


class ProfileLinkTests(unittest.TestCase):
    def test_each_supported_profile_link_names_its_source_and_identifier(self):
        parser = ProfileLinkParser()
        cases = {
            f'https://orcid.org/{ORCID}': ('orcid', ORCID),
            'https://openalex.org/authors/A5023888391': ('openalex', 'A5023888391'),
            'https://openalex.org/A5023888391': ('openalex', 'A5023888391'),
            'https://www.semanticscholar.org/author/Mei-Lin/1741101': ('semantic_scholar', '1741101'),
            'https://dblp.org/pid/12/3456.html': ('dblp', '12/3456'),
            'https://openreview.net/profile?id=~Mei_Lin1': ('openreview', '~Mei_Lin1'),
            'https://scholar.google.com/citations?hl=en&user=AbC-123_x': ('google_scholar', 'AbC-123_x'),
        }
        for url, expected in cases.items():
            link = parser.parse(url)
            self.assertIsNotNone(link, url)
            self.assertEqual((link.source, link.identifier), expected, url)

    def test_unknown_links_are_ignored_and_duplicates_collapse(self):
        parser = ProfileLinkParser()
        self.assertIsNone(parser.parse('https://example.edu/~mlin'))
        links = parser.parse_all([f'https://orcid.org/{ORCID}', f'https://orcid.org/{ORCID}', 'https://x.org'])
        self.assertEqual(len(links), 1)

    def test_patterns_come_from_configuration_and_need_an_id_group(self):
        parser = ProfileLinkParser(ProfileLinkConfig(patterns=(('lab', r'lab\.example\.edu/people/(?P<id>\w+)'),)))
        self.assertEqual(parser.parse('https://lab.example.edu/people/mlin').identifier, 'mlin')
        with self.assertRaises(ValueError):
            ProfileLinkParser(ProfileLinkConfig(patterns=(('bad', r'example\.edu/(\w+)'),)))

    def test_query_lists_links_and_pages_and_counts_them_as_hints(self):
        supplied = query(scholar_url='https://scholar.google.com/citations?user=x', profile_urls=['https://a.org'])
        self.assertEqual(supplied.links(), ['https://scholar.google.com/citations?user=x', 'https://a.org'])
        self.assertTrue(supplied.has_hints())
        pages = query(homepage='https://h.edu', publication_pages=['https://h.edu/cv.pdf', 'https://h.edu'])
        self.assertEqual(pages.pages(), ['https://h.edu', 'https://h.edu/cv.pdf'])
        self.assertFalse(query().has_hints())


def affiliations(kind, *entries):
    """An ORCID affiliation section; each entry is (institution, department, end year or None)."""
    return {
        'affiliation-group': [
            {
                'summaries': [
                    {
                        f'{kind}-summary': {
                            'department-name': department,
                            'organization': {'name': school},
                            'end-date': {'year': {'value': str(end)}} if end else None,
                        }
                    }
                ]
            }
            for school, department, end in entries
        ]
    }


class OrcidRegistrySearchTests(unittest.TestCase):
    def search(self, results, jobs=(('Example University', 'Department of Physics', None),), studies=(), **config):
        client = Client(
            {
                'expanded-search': {'expanded-result': results, 'num-found': len(results)},
                'employments': affiliations('employment', *jobs),
                'educations': affiliations('education', *studies),
            }
        )
        step = OrcidRegistrySearch(
            client,
            config=replace(DEFAULT_DISCOVERY, **config),
            department_agrees=DepartmentMatcher().agree,
            current_year=2026,
        )
        return step, client

    def adopted(self, step, **changes):
        found = Discovery()
        step.discover(query(**changes), found)
        return found

    def test_a_match_without_a_current_affiliation_at_the_institution_is_not_adopted(self):
        elsewhere, _ = self.search([registry_person(ORCID)], jobs=(('Other University', 'Physics', None),))
        found = self.adopted(elsewhere)
        self.assertIsNone(found.orcid)
        self.assertIn('no current affiliation at the requested institution', found.evidence[0])
        former, _ = self.search(
            [registry_person(ORCID)],
            jobs=(('Other University', None, None),),
            studies=(('Example University', 'Physics', 2025),),
        )
        self.assertIsNone(self.adopted(former).orcid)
        unchecked, _ = self.search([registry_person(ORCID)], jobs=(), orcid_require_affiliation=False)
        self.assertEqual(self.adopted(unchecked).orcid, ORCID)

    def test_a_current_education_confirms_a_student(self):
        student, _ = self.search([registry_person(ORCID)], jobs=(), studies=(('Example University', 'Physics', 2026),))
        self.assertEqual(self.adopted(student, department='Physics').orcid, ORCID)
        only_jobs, _ = self.search(
            [registry_person(ORCID)],
            jobs=(),
            studies=(('Example University', 'Physics', None),),
            orcid_affiliation_sections=('employments',),
        )
        self.assertIsNone(self.adopted(only_jobs).orcid)

    def test_a_requested_department_must_agree_with_the_affiliation(self):
        for department, adopted in (('Physics', True), ('Computer Science', False)):
            step, _ = self.search([registry_person(ORCID)])
            self.assertEqual(self.adopted(step, department=department).orcid == ORCID, adopted, department)
        unnamed, _ = self.search([registry_person(ORCID)], jobs=(('Example University', None, None),))
        self.assertIsNone(self.adopted(unnamed, department='Physics').orcid)

    def test_an_uncommon_name_needs_no_agreeing_department(self):
        step, _ = self.search([registry_person(ORCID)])
        step.name_is_common = lambda name: False
        self.assertEqual(self.adopted(step, department='Computer Science').orcid, ORCID)
        step, _ = self.search([registry_person(ORCID)])
        step.name_is_common = lambda name: True
        self.assertIsNone(self.adopted(step, department='Computer Science').orcid)

    def test_the_only_compatible_person_at_the_institution_is_adopted(self):
        step, client = self.search([registry_person(ORCID)])
        found = Discovery()
        step.discover(query(), found)
        self.assertEqual(found.orcid, ORCID)
        self.assertIn('family-name:"Lin" AND given-names:"Mei"', client.requests[0][1]['q'])
        self.assertIn('affiliation-org-name:"Example University"', client.requests[0][1]['q'])

    def test_several_matches_adopt_nothing(self):
        step, _ = self.search([registry_person(ORCID), registry_person('0000-0001-0000-0002')])
        found = Discovery()
        step.discover(query(), found)
        self.assertIsNone(found.orcid)
        self.assertIn('2 registry matches', found.evidence[0])

    def test_other_institutions_and_other_names_are_not_matches(self):
        step, _ = self.search(
            [registry_person(ORCID, institutions=('Other College',)), registry_person('0000-0001', given='Wei')]
        )
        found = Discovery()
        step.discover(query(), found)
        self.assertIsNone(found.orcid)

    def test_email_is_searched_first_and_a_supplied_orcid_skips_the_search(self):
        step, client = self.search([])
        step.discover(query(email='mlin@example.edu'), Discovery())
        self.assertEqual(client.requests[0][1]['q'], 'email:"mlin@example.edu"')
        step, client = self.search([registry_person(ORCID)])
        step.discover(query(orcid=ORCID), Discovery())
        self.assertEqual(client.requests, [])

    def test_names_split_into_given_and_family(self):
        self.assertEqual(split_name('Christopher D. Manning'), ('Christopher D.', 'Manning'))
        self.assertEqual(split_name('Plato'), ('', 'Plato'))


class PageRankerTests(unittest.TestCase):
    ranker = PageRanker()

    def test_own_academic_publication_page_outranks_a_namesake_elsewhere(self):
        own = SearchHit('https://cs.example.edu/~meilin/publications', 'Mei Lin - Publications', 'Example University')
        other = SearchHit('https://other.org/people/mei-lin', 'Mei Lin', 'Other Corp')
        self.assertGreaterEqual(self.ranker.score(query(), own), DEFAULT_DISCOVERY.minimum_page_score)
        self.assertLess(self.ranker.score(query(), other), self.ranker.score(query(), own))

    def test_aggregators_score_zero(self):
        hit = SearchHit('https://www.researchgate.net/profile/Mei-Lin', 'Mei Lin Example University', '')
        self.assertEqual(self.ranker.score(query(), hit), 0.0)

    def test_the_email_domain_proves_the_institution(self):
        hit = SearchHit('https://people.example.edu/mlin', 'Home', '')
        features = self.ranker.features(query(email='mlin@example.edu'), hit)
        self.assertEqual(features['institution'], 1.0)

    def test_weights_come_from_configuration(self):
        hit = SearchHit('https://cs.example.edu/~meilin', 'Mei Lin', '')
        only_title = PageRanker(
            replace(
                DEFAULT_DISCOVERY,
                weight_name_in_url=0,
                weight_institution=0,
                weight_publication_page=0,
                weight_academic_host=0,
            )
        )
        self.assertEqual(only_title.score(query(), hit), DEFAULT_DISCOVERY.weight_name_in_title)


class PageDiscoveryTests(unittest.TestCase):
    def test_orcid_researcher_urls_become_pages_with_the_orcid_bonus(self):
        client = Client(
            {
                'researcher-urls': {
                    'researcher-url': [
                        {'url-name': 'Homepage', 'url': {'value': 'https://cs.example.edu/~meilin'}},
                        {'url-name': 'LinkedIn', 'url': {'value': 'https://linkedin.com/in/meilin'}},
                    ]
                }
            }
        )
        found = Discovery(orcid=ORCID)
        OrcidResearcherUrls(client, PageRanker()).discover(query(), found)
        self.assertEqual([page.url for page in found.pages], ['https://cs.example.edu/~meilin'])
        self.assertGreaterEqual(found.pages[0].score, DEFAULT_DISCOVERY.weight_orcid_listed)

    def test_web_search_keeps_hits_above_the_threshold_once_each(self):
        class Search:
            name = 'fake'

            def __init__(self):
                self.queries = []

            def search(self, text, count):
                self.queries.append(text)
                return [
                    SearchHit('https://cs.example.edu/~meilin/pubs', 'Mei Lin publications', 'Example University'),
                    SearchHit('https://news.example.com/x', 'Campus news', ''),
                ]

        search = Search()
        found = Discovery()
        WebPageSearch(search, PageRanker()).discover(query(), found)
        self.assertEqual(search.queries, ['"Mei Lin" Example University', '"Mei Lin" Example University publications'])
        self.assertEqual([page.url for page in found.pages], ['https://cs.example.edu/~meilin/pubs'])

    def test_a_failing_step_is_recorded_and_later_steps_still_run(self):
        class Broken:
            name = 'broken'

            def discover(self, query, found):
                raise SourceError('HTTP 503')

        class Adds:
            name = 'adds'

            def discover(self, query, found):
                found.add_page(DiscoveredPage('https://a.edu', 1.0, 'test'))

        found = IdentityDiscovery([Broken(), Adds()]).discover(query())
        self.assertEqual(found.errors, ['broken: HTTP 503'])
        self.assertEqual(len(found.pages), 1)

    def test_best_pages_are_highest_scoring_first(self):
        found = Discovery()
        for url, score in (('https://a.edu', 0.6), ('https://b.edu', 0.9), ('https://a.edu', 1.0)):
            found.add_page(DiscoveredPage(url, score, 'test'))
        self.assertEqual([page.url for page in found.best_pages(5)], ['https://b.edu', 'https://a.edu'])


class WebSearchAdapterTests(unittest.TestCase):
    def test_brave_reads_web_results_with_the_subscription_token(self):
        client = Client({'brave': {'web': {'results': [{'url': 'https://a.edu', 'title': 'A', 'description': 'd'}]}}})
        hits = BraveSearch(client, 'https://api.brave/search', 'key').search('q', 5)
        self.assertEqual(hits, [SearchHit('https://a.edu', 'A', 'd')])
        self.assertEqual(client.requests[0][2]['X-Subscription-Token'], 'key')

    def test_tavily_and_exa_post_their_queries(self):
        client = Client(post={'results': [{'url': 'https://a.edu', 'title': 'A', 'content': 'c', 'text': 't'}]})
        self.assertEqual(TavilySearch(client, 'https://t', 'k').search('q', 3)[0].snippet, 'c')
        self.assertEqual(client.requests[-1][1], {'query': 'q', 'max_results': 3})
        self.assertEqual(ExaSearch(client, 'https://e', 'k').search('q', 3)[0].snippet, 't')
        self.assertEqual(client.requests[-1][2], {'x-api-key': 'k'})


PERSONAL_PAGE = """<html><body>
<p>M. Lin, A. Coauthor. Named paper. <a href="https://doi.org/10.1000/own">doi</a></p>
<p>B. Other. Someone else's paper. <a href="https://doi.org/10.1000/foreign">doi</a></p>
<a href="/~meilin/publications.html">Publications</a> <a href="https://elsewhere.org/papers">Papers</a>
</body></html>"""
PUBLICATIONS_PAGE = '<p>M. Lin. Listed paper. <a href="https://doi.org/10.1000/listed">doi</a></p>'


class Discovered:
    def __init__(self, found):
        self.found = found

    def discover(self, query):
        return self.found


class AnchorFinderIntegrationTests(unittest.TestCase):
    def finder(self, client, found=None, **config):
        return ScholarlyAnchorFinder(
            client,
            discovery=Discovered(found or Discovery()),
            discovery_config=replace(DEFAULT_DISCOVERY, **config),
        )

    def test_supplied_profile_links_anchor_their_ids_and_supply_the_orcid(self):
        links = [
            'https://openalex.org/authors/A123',
            'https://www.semanticscholar.org/author/9',
            f'https://orcid.org/{ORCID}',
            'https://scholar.google.com/citations?user=abc',
        ]
        anchors = self.finder(Client()).find(query(profile_urls=links))
        self.assertEqual(anchors.orcid, ORCID)
        self.assertTrue(anchors.contains('openalex', 'A123'))
        self.assertTrue(anchors.contains('semantic_scholar', '9'))
        kinds = {item.kind for item in anchors.evidence if item.reason.startswith('profile link')}
        self.assertEqual(kinds, {AnchorKind.PROFILE})

    def test_a_discovered_orcid_is_used_and_recorded(self):
        anchors = self.finder(Client(), Discovery(orcid=ORCID, evidence=['why'])).find(query())
        self.assertEqual(anchors.orcid, ORCID)
        self.assertEqual(anchors.discovered['orcid'], ORCID)
        replayed = RecordedAnchorFinder.from_identity({'profile': {'discovered': anchors.discovered}}).find(query())
        self.assertEqual(replayed.orcid, ORCID)

    def test_discovered_pages_count_only_citations_naming_the_person(self):
        page = 'https://cs.example.edu/~meilin/'
        client = Client(pages={page: PERSONAL_PAGE})
        found = Discovery(pages=[DiscoveredPage(page, 0.9, 'web')])
        anchors = self.finder(client, found, follow_publication_links=False).find(query())
        self.assertEqual(anchors.listed_works, ['doi:10.1000/own'])

    def test_a_supplied_page_follows_its_publication_link_on_the_same_site(self):
        page = 'https://cs.example.edu/~meilin/'
        client = Client(pages={page: PERSONAL_PAGE, page + 'publications.html': PUBLICATIONS_PAGE})
        anchors = self.finder(client).find(query(homepage=page))
        self.assertIn('doi:10.1000/listed', anchors.listed_works)

    def test_a_redirected_page_resolves_its_links_against_where_it_was_served(self):
        page = 'https://cs.example.edu/~meilin'
        served = page + '/'
        client = Client(pages={page: PERSONAL_PAGE, served + 'publications.html': PUBLICATIONS_PAGE})
        answer = client.probe

        def probe(url, *args, **kwargs):
            response = answer(url, *args, **kwargs)
            return replace(response, url=served) if url == page else response

        client.probe = probe
        anchors = self.finder(client).find(query(homepage=page))
        self.assertIn('doi:10.1000/listed', anchors.listed_works)

    def test_publication_links_are_same_site_and_marked(self):
        links = publication_links(PERSONAL_PAGE, 'https://cs.example.edu/~meilin/')
        self.assertEqual(links, ['https://cs.example.edu/~meilin/publications.html'])

    def test_an_unreadable_page_is_an_error_not_a_failure(self):
        anchors = self.finder(Client()).find(query(homepage='https://gone.example.edu'))
        self.assertTrue(any('gone.example.edu' in error for error in anchors.errors))


class CompositionTests(unittest.TestCase):
    def test_discovery_steps_follow_configuration_and_credentials(self):
        container = ServiceContainer(Settings(), RunOptions(mode='metadata'))
        self.assertEqual([step.name for step in container.discovery.steps], ['orcid_search', 'orcid_researcher_urls'])
        keyed = replace(Settings(), credentials=Credentials(tavily_api_key='k'))
        steps = ServiceContainer(keyed, RunOptions(mode='metadata')).discovery.steps
        self.assertIsInstance(steps[-1].search, TavilySearch)
        disabled = replace(Settings(), discovery=DiscoveryConfig(enabled=False))
        self.assertEqual(ServiceContainer(disabled).discovery.steps, [])

    def test_web_search_uses_the_first_provider_with_a_key(self):
        keys = Credentials(tavily_api_key='t', exa_api_key='e')
        preferred = replace(Settings(), credentials=keys, discovery=DiscoveryConfig(web_search=('exa', 'tavily')))
        self.assertIsInstance(ServiceContainer(preferred).web_search, ExaSearch)
        self.assertIsNone(ServiceContainer(replace(preferred, credentials=Credentials())).web_search)
        off = replace(preferred, discovery=DiscoveryConfig(web_search=()))
        self.assertIsNone(ServiceContainer(off).web_search)

    def test_every_web_search_provider_is_registered(self):
        self.assertEqual(set(WEB_SEARCH.names()), {'brave', 'tavily', 'exa'})


if __name__ == '__main__':
    unittest.main()
