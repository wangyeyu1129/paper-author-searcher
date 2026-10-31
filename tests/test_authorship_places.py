import unittest

from paper_author_searcher.authorship_places import AuthorshipPlaces
from paper_author_searcher.http import SourceError


class Client:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, params))
        if self.error:
            raise self.error
        return self.responses[url.rsplit('/', 1)[-1]]


class AuthorshipPlacesTests(unittest.TestCase):
    def places(self, *args, **kwargs):
        return AuthorshipPlaces(Client(*args, **kwargs), 'https://api.openalex.org', None, batch_size=2)

    def test_history_joins_affiliations_and_last_known(self):
        places = self.places(
            {
                'A1': {
                    'affiliations': [{'institution': {'display_name': 'UCLA'}}],
                    'last_known_institutions': [{'display_name': 'UIUC'}],
                }
            }
        )
        self.assertEqual(places.history(['A1']), {'UCLA', 'UIUC'})

    def test_history_is_empty_when_the_source_fails(self):
        self.assertEqual(self.places(error=SourceError('down')).history(['A1']), set())

    def test_only_the_target_name_counts_and_dois_are_bare(self):
        work = {
            'doi': 'https://doi.org/10.1/X',
            'authorships': [
                {'author': {'display_name': 'Huan Zhang'}, 'institutions': [{'display_name': 'Queen Mary'}]},
                {'author': {'display_name': 'Simon Dixon'}, 'institutions': [{'display_name': 'Elsewhere'}]},
            ],
        }
        places = self.places({'works': {'results': [work]}})
        found = places.on_bylines(['10.1/x'], lambda name: name == 'Huan Zhang')
        self.assertEqual(found, {'10.1/x': {'Queen Mary'}})

    def test_a_paper_with_no_printed_institution_is_absent(self):
        work = {'doi': 'https://doi.org/10.1/x', 'authorships': [{'author': {'display_name': 'H'}, 'institutions': []}]}
        self.assertEqual(self.places({'works': {'results': [work]}}).on_bylines(['10.1/x'], lambda _: True), {})

    def test_dois_go_in_batches(self):
        places = self.places({'works': {'results': []}})
        places.on_bylines(['a', 'b', 'c'], lambda _: True)
        self.assertEqual(len(places.client.calls), 2)

    def test_a_failed_batch_loses_only_that_batch(self):
        self.assertEqual(self.places(error=SourceError('down')).on_bylines(['a'], lambda _: True), {})


if __name__ == '__main__':
    unittest.main()
