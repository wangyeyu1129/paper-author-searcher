import unittest

from paper_author_searcher.config import DEFAULT_DISCOVERY
from paper_author_searcher.curated_positions import PositionMatcher
from paper_author_searcher.dblp_discovery import DblpProfileDiscovery
from paper_author_searcher.identity_discovery import Discovery, PageRanker
from paper_author_searcher.models import AuthorCandidate, CuratedPosition, ProfessorQuery

QUERY = ProfessorQuery('Yang Liu', 'Tsinghua University', department='Computer Science and Technology')
HOME = CuratedPosition('dblp', ('Tsinghua University',), ('department of computer science and technology',), True)
INSTITUTE = CuratedPosition('dblp', ('Tsinghua University',), ('institute for ai industry research',), True)


def profile(author_id, position, links=(), orcid=None, name='Yang Liu 0005'):
    candidate = AuthorCandidate('dblp', author_id, name, orcid=orcid)
    candidate.positions = [position]
    candidate.links = list(links)
    return candidate


class Search:
    def __init__(self, *candidates):
        self.candidates = list(candidates)

    def search(self, query):
        return self.candidates


def discover(*candidates, query=QUERY):
    found = Discovery()
    step = DblpProfileDiscovery(Search(*candidates), PositionMatcher(), PageRanker(DEFAULT_DISCOVERY))
    step.discover(query, found)
    return found


class DblpProfileDiscoveryTests(unittest.TestCase):
    def test_the_one_profile_in_the_department_gives_homepage_and_orcid(self):
        found = discover(
            profile(
                '51/3710-5',
                HOME,
                ['http://nlp.csai.tsinghua.edu.cn/~ly/', 'https://openreview.net/profile?id=~Yang_Liu1'],
                'https://orcid.org/0000-0002-3087-242X',
            ),
            profile('51/3710-165', INSTITUTE, ['https://example.edu/other'], 'https://orcid.org/0000-0003-3800-3533'),
        )
        self.assertEqual([page.url for page in found.pages], ['http://nlp.csai.tsinghua.edu.cn/~ly/'])
        self.assertEqual(found.orcid, '0000-0002-3087-242X')
        self.assertTrue(any('51/3710-5' in line for line in found.evidence))

    def test_two_profiles_in_the_department_give_nothing(self):
        found = discover(profile('a', HOME, ['https://a.example.edu/']), profile('b', HOME, ['https://b.example.edu/']))
        self.assertEqual((found.pages, found.orcid), ([], None))
        self.assertTrue(any('2 place' in line for line in found.evidence))

    def test_without_a_requested_department_nothing_is_read(self):
        query = ProfessorQuery('Yang Liu', 'Tsinghua University')
        self.assertEqual(discover(profile('a', HOME, ['https://a.example.edu/']), query=query).pages, [])

    def test_an_incompatible_name_is_not_the_person(self):
        found = discover(profile('a', HOME, ['https://a.example.edu/'], name='Yan Lin 0001'))
        self.assertEqual(found.pages, [])

    def test_an_orcid_already_found_is_kept(self):
        found = Discovery(orcid='0000-0001-0000-0000')
        step = DblpProfileDiscovery(
            Search(profile('a', HOME, [], 'https://orcid.org/0000-0002-3087-242X')),
            PositionMatcher(),
            PageRanker(DEFAULT_DISCOVERY),
        )
        step.discover(QUERY, found)
        self.assertEqual(found.orcid, '0000-0001-0000-0000')


if __name__ == '__main__':
    unittest.main()
