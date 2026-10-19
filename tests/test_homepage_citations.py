"""Homepage citations: titles from title-only pages, and preferring entries that name the person."""

import dataclasses
import unittest

from paper_author_searcher import ProfessorQuery as Q
from paper_author_searcher.config import DEFAULT_ANCHORS, DEFAULT_SOURCES
from paper_author_searcher.homepage_citations import HomepageCitation, HtmlCitationParser, mentions, prioritise
from paper_author_searcher.http import URLProbe
from paper_author_searcher.identity_anchors import ScholarlyAnchorFinder

TITLE_ONLY_PAGE = """
<div class="publications">
  <ul>
    <li><b>Stable Lithium Metal Anodes with Interfacial Engineering</b>, Nature Energy 2024.</li>
    <li>"Measuring Upward Mobility Across American Neighborhoods", Working paper, 2023.</li>
    <li><a href="/cv.pdf">CV</a></li>
  </ul>
</div>
"""

LAB_PAGE = (
    ''.join(
        f'<p>A. Other, B. Person. Lab paper {index}. <a href="https://doi.org/10.1000/lab{index}">doi</a></p>'
        for index in range(20)
    )
    + '<p>J. Lee, L. Lu. Student paper. <a href="https://doi.org/10.1000/student">doi</a></p>'
)


class Client:
    """Serves one homepage and records every metadata request."""

    def __init__(self, page):
        self.page = page
        self.requests = []

    def probe(self, url, maximum_bytes=1024, html_bytes=0, maximum_redirects=5):
        return URLProbe(url, 200, 'text/html; charset=utf-8', {}, self.page.encode('utf-8'))

    def get(self, url, params=None, headers=None, xml=False):
        self.requests.append((url, dict(params or {})))
        return {'results': [], 'authorships': [], 'authors': []}


def homepage_query(name='Jonathan Lee'):
    return Q(name, 'Example University', homepage='https://lab.example.edu/publications')


class ParserTests(unittest.TestCase):
    def test_titles_come_from_emphasised_and_quoted_spans(self):
        citations = HtmlCitationParser().parse(TITLE_ONLY_PAGE)
        titles = [title for citation in citations for title in citation.titles]
        self.assertEqual(
            titles,
            [
                'Stable Lithium Metal Anodes with Interfacial Engineering',
                'Measuring Upward Mobility Across American Neighborhoods',
            ],
        )

    def test_identifiers_are_read_from_links_per_citation(self):
        citations = HtmlCitationParser().parse(LAB_PAGE)
        self.assertEqual(len(citations), 21)
        self.assertEqual(citations[-1].dois, ('10.1000/student',))

    def test_title_bounds_come_from_configuration(self):
        strict = HtmlCitationParser(dataclasses.replace(DEFAULT_ANCHORS, title_minimum_words=12))
        self.assertEqual([c.titles for c in strict.parse(TITLE_ONLY_PAGE) if c.titles], [])

    def test_mentions_accepts_initials_in_either_order(self):
        for text in ('Yang Liu and others', 'Y. Liu, Z. Wang', 'Liu Y, Wang Z'):
            self.assertTrue(mentions('Yang Liu', text), text)
        self.assertFalse(mentions('Yang Liu', 'Z. Wang, Q. Liu'))

    def test_citations_naming_the_person_come_first(self):
        mine, other = HomepageCitation('J. Lee, X. Y.'), HomepageCitation('A. Other')
        self.assertEqual(prioritise([other, mine], 'Jonathan Lee'), [mine, other])


class FinderTests(unittest.TestCase):
    def test_title_only_page_anchors_by_title(self):
        client = Client(TITLE_ONLY_PAGE)
        ScholarlyAnchorFinder(client).find(homepage_query('Yi Cui'))
        searches = [params['filter'] for url, params in client.requests if url.endswith('/works')]
        self.assertEqual(len(searches), 2)
        self.assertTrue(searches[0].startswith('title.search:Stable Lithium Metal Anodes'))

    def test_title_match_anchors_semantic_scholar_through_its_doi(self):
        class TitleClient(Client):
            def get(self, url, params=None, headers=None, xml=False):
                self.requests.append((url, dict(params or {})))
                if url.endswith('/works'):
                    title = 'Stable Lithium Metal Anodes with Interfacial Engineering'
                    return {'results': [{'title': title, 'doi': 'https://doi.org/10.1000/anode', 'authorships': []}]}
                if '/paper/' in url:
                    return {'authors': [{'authorId': '55', 'name': 'Yi Cui'}]}
                return {'results': []}

        client = TitleClient(TITLE_ONLY_PAGE)
        found = ScholarlyAnchorFinder(client).find(homepage_query('Yi Cui'))
        lookup = DEFAULT_SOURCES.semantic_scholar_url + '/paper/DOI:10.1000/anode'
        self.assertIn(lookup, [url for url, _ in client.requests])
        self.assertEqual(found.author_ids.get('semantic_scholar'), {'55'})

    def test_named_citations_win_the_anchor_budget(self):
        client = Client(LAB_PAGE)
        ScholarlyAnchorFinder(client).find(homepage_query())
        looked_up = [url.rsplit('doi:', 1)[-1] for url, _ in client.requests if '/works/doi:' in url]
        self.assertEqual(len(looked_up), DEFAULT_ANCHORS.maximum_anchor_papers)
        self.assertEqual(looked_up[0], '10.1000/student')

    def test_identifier_pages_spend_no_budget_on_titles(self):
        client = Client(LAB_PAGE)
        ScholarlyAnchorFinder(client).find(homepage_query())
        self.assertFalse(any(url.endswith('/works') for url, _ in client.requests))


if __name__ == '__main__':
    unittest.main()
