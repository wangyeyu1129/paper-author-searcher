"""Other names of a school, and names that only one person holds."""

import unittest
from dataclasses import replace

from paper_author_searcher.http import SourceError
from paper_author_searcher.institution_names import InstitutionNames
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.name_rarity import NameRarity

LSE = {
    'display_name': 'London School of Economics and Political Science',
    'display_name_alternatives': ['LSE', 'London School of Economics'],
    'display_name_acronyms': ['LSE'],
}


class Client:
    def __init__(self, results=None, error=None):
        self.results, self.error, self.calls = results or [], error, []

    def get(self, url, params=None, headers=None, xml=False):
        self.calls.append((url, params))
        if self.error:
            raise self.error
        return {'results': self.results}


def query(university):
    return ProfessorQuery(name='Guy Michaels', university=university)


class InstitutionNamesTests(unittest.TestCase):
    def expand(self, university, results):
        return InstitutionNames(Client(results), 'https://api.openalex.org').expand(query(university))

    def test_a_short_name_gains_the_sources_full_name(self):
        expanded = self.expand('London School of Economics', [LSE])
        self.assertIn('London School of Economics and Political Science', expanded.university_aliases)

    def test_an_acronym_is_enough(self):
        self.assertIn('London School of Economics and Political Science', self.expand('LSE', [LSE]).university_aliases)

    def test_another_school_found_by_the_search_is_not_taken(self):
        other = {'display_name': 'Lahore School of Economics', 'display_name_alternatives': []}
        self.assertEqual(self.expand('London School of Economics', [other]).university_aliases, [])

    def test_the_first_matching_record_wins_over_later_ones(self):
        other = {'display_name': 'Lahore School of Economics'}
        expanded = self.expand('London School of Economics', [other, LSE])
        self.assertNotIn('Lahore School of Economics', expanded.university_aliases)
        self.assertIn('LSE', expanded.university_aliases)

    def test_a_failed_lookup_changes_nothing(self):
        request = replace(query('MIT'), university_aliases=['M.I.T.'])
        names = InstitutionNames(Client(error=SourceError('down')), 'https://api.openalex.org')
        self.assertIs(names.expand(request), request)


def rarity(results, collides=False, error=None):
    return NameRarity(Client(results, error), 'https://api.openalex.org', None, lambda _: collides, 0.9)


def author(identifier, name, works):
    return {'id': f'https://openalex.org/{identifier}', 'display_name': name, 'works_count': works}


class NameRarityTests(unittest.TestCase):
    def test_nearly_all_works_under_the_persons_ids_is_rare(self):
        people = [author('A1', 'Guy Michaels', 114), author('A2', 'Guy Michaels', 3), author('A3', 'Guy Natraj', 40)]
        self.assertTrue(rarity(people).rare('Guy Michaels', ['A1']))

    def test_merged_fragments_count_as_the_person(self):
        people = [author('A1', 'Guy Michaels', 50), author('A2', 'Guy Michaels', 50)]
        self.assertTrue(rarity(people).rare('Guy Michaels', ['A1', 'https://openalex.org/A2']))

    def test_a_shared_name_is_not_rare(self):
        people = [author('A1', 'Wei Wang', 400), author('A2', 'Wei Wang', 390)]
        self.assertFalse(rarity(people).rare('Wei Wang', ['A1']))

    def test_a_name_that_collides_easily_is_never_rare(self):
        limits = rarity([author('A1', 'Yue Li', 900)], collides=True)
        self.assertFalse(limits.rare('Yue Li', ['A1']))
        self.assertEqual(limits.client.calls, [])

    def test_no_data_is_not_rare(self):
        self.assertFalse(rarity([]).rare('Ada Lovelace', ['A1']))
        self.assertFalse(rarity([], error=SourceError('down')).rare('Ada Lovelace', ['A1']))


if __name__ == '__main__':
    unittest.main()
