"""Property-based tests: invariants checked on generated inputs, not only hand-picked examples."""

import json
import unittest

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st
except ImportError:  # dev extra not installed; CI installs it and forbids skips
    given = None

from paper_author_searcher.benchmark.truth_sources import _bibtex_value
from paper_author_searcher.matching import canonical_doi, name_similarity, name_similarity_upper_bound, normalize
from paper_author_searcher.settings import Settings
from paper_author_searcher.status import OwnershipStatus

REQUIRES_HYPOTHESIS = unittest.skipIf(given is None, 'hypothesis (dev extra) is required')

if given is not None:
    LETTERS = st.characters(whitelist_categories=('Lu', 'Ll', 'Lt', 'Mn'), max_codepoint=0x24F)
    WORD = st.text(LETTERS, min_size=1, max_size=10)
    INITIAL = st.builds(lambda letter: letter + '.', st.sampled_from('ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
    NAME = st.lists(st.one_of(WORD, INITIAL), min_size=1, max_size=4).map(' '.join)
    YEAR_SUFFIX = st.one_of(st.just(''), st.integers(1950, 2030).map(lambda year: f' {year}'))
    DOI = st.builds(
        lambda registrant, suffix: f'10.{registrant}/{suffix}',
        st.integers(1000, 99999),
        st.text(st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'), max_codepoint=0x7A), min_size=1, max_size=12),
    )
    PREFIX = st.sampled_from(['', 'doi:', 'https://doi.org/', 'http://dx.doi.org/', 'DOI:', 'HTTPS://DOI.ORG/'])
else:  # placeholders so the decorators below still parse
    NAME = YEAR_SUFFIX = DOI = PREFIX = None


def property_test(*strategies):
    def decorate(test):
        if given is None:
            return REQUIRES_HYPOTHESIS(test)
        return settings(max_examples=300, deadline=None)(given(*strategies)(test))

    return decorate


class NameMatchingProperties(unittest.TestCase):
    @property_test(NAME, NAME, YEAR_SUFFIX)
    def test_upper_bound_never_undercuts_similarity(self, left, right, year):
        """E4's grouping shortcut is exact only while this holds."""
        self.assertGreaterEqual(name_similarity_upper_bound(left, right + year), name_similarity(left, right + year))

    @property_test(NAME)
    def test_a_name_matches_itself_and_its_reordering(self, name):
        if normalize(name):
            self.assertEqual(name_similarity(name, name), 1.0)
            self.assertEqual(name_similarity(name, ' '.join(reversed(name.split()))), 1.0)

    @property_test(NAME)
    def test_normalize_is_idempotent(self, name):
        self.assertEqual(normalize(normalize(name)), normalize(name))


class DOIProperties(unittest.TestCase):
    @property_test(DOI, PREFIX)
    def test_prefixes_and_case_do_not_change_the_canonical_doi(self, doi, prefix):
        canonical = canonical_doi(doi)
        self.assertEqual(canonical_doi(prefix + doi.upper()), canonical)
        self.assertEqual(canonical_doi(canonical), canonical)


class ParserProperties(unittest.TestCase):
    if given is not None:
        TEXT = st.text(max_size=60)
        CURSOR = st.integers(0, 70)
    else:
        TEXT = CURSOR = None

    @property_test(TEXT, CURSOR)
    def test_bibtex_values_stay_inside_the_text_and_advance(self, text, cursor):
        value, end = _bibtex_value(text, cursor)
        self.assertLessEqual(end, max(len(text), cursor))
        self.assertGreaterEqual(end, min(cursor, len(text)))
        if value:
            self.assertIn(value, text)


class SettingsProperties(unittest.TestCase):
    if given is not None:
        RETRIES = st.integers(0, 50)
        TIMEOUT = st.floats(0.1, 600, allow_nan=False)
        FLAG = st.booleans()
    else:
        RETRIES = TIMEOUT = FLAG = None

    @property_test(RETRIES, TIMEOUT, FLAG)
    def test_environment_and_json_overrides_round_trip(self, retries, timeout, flag):
        from_json = Settings.from_mapping(
            json.loads(
                json.dumps(
                    {'http': {'retries': retries, 'timeout': timeout}, 'matching': {'merge_split_profiles': flag}}
                )
            )
        )
        from_environment = Settings.load(
            environ={
                'PAPER_SEARCH__HTTP__RETRIES': str(retries),
                'PAPER_SEARCH__HTTP__TIMEOUT': repr(timeout),
                'PAPER_SEARCH__MATCHING__MERGE_SPLIT_PROFILES': str(flag).lower(),
            }
        )
        self.assertEqual(from_json.http, from_environment.http)
        self.assertEqual(from_json.matching, from_environment.matching)
        self.assertEqual(from_json.http.retries, retries)


class StatusProperties(unittest.TestCase):
    if given is not None:
        STATUS = st.sampled_from(list(OwnershipStatus))
    else:
        STATUS = None

    @property_test(STATUS)
    def test_status_members_serialise_as_their_value(self, status):
        self.assertEqual(json.loads(json.dumps({'status': status}))['status'], status.value)
        self.assertEqual(OwnershipStatus(json.loads(json.dumps(status))), status)


if __name__ == '__main__':
    unittest.main()
