"""Identity anchors from caller hints (ORCID, homepage, known papers), with synthetic fixtures only."""

import dataclasses
import json
import unittest

from paper_author_searcher import AuthorCandidate as C
from paper_author_searcher import ProfessorQuery as Q
from paper_author_searcher import ProfessorResolver
from paper_author_searcher.config import DEFAULT_MATCHING
from paper_author_searcher.identity_anchors import (
    IdentityAnchors,
    RecordedAnchorFinder,
    ScholarlyAnchorFinder,
    homepage_papers,
    paper_hints,
)
from paper_author_searcher.identity_ranking import IdentityRanking
from paper_author_searcher.models import AnchorEvidence, ProfessorProfile


class Provider:
    name = 'openalex'

    def __init__(self, records, extra=None):
        self.records = records
        self.extra = extra or {}
        self.lookups = []

    def search(self, query):
        return list(self.records)

    def lookup(self, author_id):
        self.lookups.append(author_id)
        return self.extra[author_id]


class FixedAnchors:
    def __init__(self, anchors):
        self.anchors = anchors

    def find(self, query):
        return self.anchors


def anchors(*ids, orcid=None):
    found = IdentityAnchors(orcid=orcid)
    for author_id in ids:
        found.add('openalex', author_id, 'author of DOI 10.1000/known')
    return found


def query(**hints):
    return Q('Mei Lin', 'Example University', known_papers=['10.1000/known'], **hints)


SCHOOL = ['Example University']


class RecordedAnchorTests(unittest.TestCase):
    def test_saved_identity_anchors_are_replayed_without_resolver_derived_ones(self):
        identity = {
            'profile': {'identity_anchors': [{'source': 'openalex', 'author_id': 'A1', 'reason': 'ORCID 0000-0001'}]},
            'candidates': [
                {'identity_anchors': ['openalex A1: ORCID 0000-0001', 'semantic_scholar 9: author of DOI:10.1/x']},
                {'identity_anchors': ['openalex A2: ORCID matches the supplied ORCID']},
            ],
        }
        found = RecordedAnchorFinder.from_identity(identity).find(query(orcid='0000-0001'))
        self.assertEqual(found.author_ids, {'openalex': {'A1'}, 'semantic_scholar': {'9'}})
        self.assertEqual(found.orcid, '0000-0001')
        self.assertEqual(len(found.evidence), 2)


class HintParsingTests(unittest.TestCase):
    def test_known_papers_are_split_by_kind(self):
        hints = paper_hints(['https://doi.org/10.1000/ABC.1', 'arXiv:2401.01234v2', 'A Study of Things'])
        self.assertEqual(hints, {'doi': ['10.1000/abc.1'], 'arxiv': ['2401.01234'], 'title': ['A Study of Things']})

    def test_homepage_links_become_paper_hints(self):
        html = (
            '<a href="https://doi.org/10.1000/xyz">x</a> <a href="https://arxiv.org/abs/2301.00001">y</a>'
            ' <a href="https://doi.org/10.1000/xyz">dup</a>'
        )
        self.assertEqual(homepage_papers(html), ['10.1000/xyz', '2301.00001'])

    def test_query_without_hints_triggers_no_anchor_lookup(self):
        self.assertFalse(Q('Mei Lin', 'Example University').has_hints())
        self.assertTrue(Q('Mei Lin', 'Example University', orcid='0000-0000-0000-0001').has_hints())


class FinderTests(unittest.TestCase):
    def test_doi_and_orcid_lookups_find_name_compatible_authors(self):
        class Client:
            def get(self, url, params=None, headers=None, xml=False):
                if url.endswith('/authors'):
                    return {'results': [{'id': 'https://openalex.org/A9', 'display_name': 'Mei Lin'}]}
                if '/works/doi:' in url:
                    return {
                        'authorships': [
                            {'author': {'id': 'https://openalex.org/A1', 'display_name': 'Mei Lin'}},
                            {'author': {'id': 'https://openalex.org/A2', 'display_name': 'Jane Smith'}},
                            {
                                'author': {
                                    'id': 'https://openalex.org/A3',
                                    'display_name': 'Mei Lin',
                                    'orcid': 'https://orcid.org/0000-0000-0000-0002',
                                }
                            },
                        ]
                    }
                if '/paper/' in url:
                    return {'authors': [{'authorId': '77', 'name': 'M. Lin'}, {'authorId': '78', 'name': 'J. Smith'}]}
                if url.endswith('/0000-0000-0000-0001/works'):
                    doi = {'external-id-type': 'doi', 'external-id-value': '10.1000/Listed'}
                    title = {'title': {'title': {'value': 'An Untitled Identifier-free Work'}}}
                    return {'group': [{'external-ids': {'external-id': [doi]}}, {'work-summary': [title]}]}
                raise AssertionError(url)

        found = ScholarlyAnchorFinder(Client()).find(query(orcid='0000-0000-0000-0001'))
        self.assertEqual(found.author_ids['openalex'], {'A9', 'A1'})
        self.assertEqual(found.author_ids['semantic_scholar'], {'77'})
        # The supplied ORCID's works are the person's own list.
        self.assertEqual(found.listed_works, ['doi:10.1000/listed', 'title:an untitled identifier free work'])


class ResolverAnchorTests(unittest.TestCase):
    def resolve(self, records, found, extra=None):
        provider = Provider(records, extra)
        resolver = ProfessorResolver([provider], anchor_finder=FixedAnchors(found))
        return resolver.resolve(query()), provider

    def test_anchor_breaks_a_close_namesake_tie(self):
        result, _ = self.resolve(
            [C('openalex', 'A1', 'Mei Lin', SCHOOL), C('openalex', 'B1', 'Mei Lin', SCHOOL)], anchors('B1')
        )
        self.assertEqual(result.profile.author_ids['openalex'], 'B1')
        self.assertEqual(result.profile.status, 'resolved')
        self.assertIsNone(result.ambiguity)
        self.assertTrue(any('Identity anchor' in line for line in result.profile.evidence))

    def test_anchor_evidence_is_structured_and_round_trips_through_json(self):
        result, _ = self.resolve(
            [C('openalex', 'A1', 'Mei Lin', SCHOOL), C('openalex', 'B1', 'Mei Lin', SCHOOL)], anchors('B1')
        )
        anchor = result.profile.identity_anchors[0]
        self.assertIsInstance(anchor, AnchorEvidence)
        self.assertEqual((anchor.source, anchor.author_id), ('openalex', 'B1'))
        saved = json.loads(json.dumps(result.to_dict()))
        self.assertEqual(saved['profile']['identity_anchors'][0]['author_id'], 'B1')
        restored = ProfessorProfile(**saved['profile'])
        self.assertEqual(restored.identity_anchors, result.profile.identity_anchors)

    def test_anchored_fragments_merge_into_one_identity(self):
        result, _ = self.resolve(
            [
                C('openalex', 'A1', 'Mei Lin', SCHOOL),
                C('openalex', 'A2', 'Mei Lin', SCHOOL),
                C('openalex', 'B1', 'Mei Lin', SCHOOL),
            ],
            anchors('A1', 'A2'),
        )
        self.assertEqual(sorted(result.profile.author_id_aliases['openalex']), ['A1', 'A2'])
        self.assertEqual(result.profile.status, 'resolved')

    def test_anchored_profile_missing_from_search_is_fetched(self):
        hidden = C('openalex', 'H1', 'Mei Lin', SCHOOL)
        result, provider = self.resolve(
            [C('openalex', 'B1', 'Mei Lin', ['Other College'])], anchors('H1'), {'H1': hidden}
        )
        self.assertEqual(provider.lookups, ['H1'])
        self.assertEqual(result.profile.author_ids['openalex'], 'H1')

    def test_supplied_orcid_outranks_a_better_scoring_namesake(self):
        orcid = '0000-0000-0000-0001'
        records = [
            C('openalex', 'A1', 'Mei Lin', SCHOOL),
            C('openalex', 'B1', 'Mei Lin', ['Other College'], orcid=orcid),
        ]
        result, _ = self.resolve(records, anchors(orcid=orcid))
        self.assertEqual(result.profile.author_ids['openalex'], 'B1')
        self.assertEqual(result.profile.status, 'resolved')
        self.assertIsNone(result.ambiguity)
        self.assertEqual([anchor.kind for anchor in result.profile.identity_anchors], ['orcid'])

    def test_anchor_contradicted_by_the_records_own_orcid_is_ignored(self):
        orcid = '0000-0000-0000-0001'
        found = anchors('B2', orcid=orcid)  # e.g. a source returned a namesake for the ORCID lookup
        records = [
            C('openalex', 'B1', 'Mei Lin', ['Other College'], orcid=orcid),
            C('openalex', 'B2', 'Mei Lin', ['Other College'], orcid='0000-0000-0000-0009'),
        ]
        result, _ = self.resolve(records, found)
        self.assertEqual(result.profile.author_id_aliases['openalex'], ['B1'])
        self.assertFalse(result.profile.orcid_conflict)
        self.assertEqual(result.profile.status, 'resolved')

    def test_anchor_precedence_is_configurable(self):
        orcid = '0000-0000-0000-0001'
        records = [
            C('openalex', 'A1', 'Mei Lin', SCHOOL),
            C('openalex', 'B1', 'Mei Lin', ['Other College'], orcid=orcid),
        ]
        config = dataclasses.replace(DEFAULT_MATCHING, precedence_anchor_kinds=())
        resolver = ProfessorResolver(
            [Provider(records)], config=config, anchor_finder=FixedAnchors(anchors(orcid=orcid))
        )
        self.assertEqual(resolver.resolve(query()).profile.author_ids['openalex'], 'A1')

    def test_paper_anchors_do_not_take_precedence(self):
        records = [C('openalex', 'A1', 'Mei Lin', SCHOOL), C('openalex', 'B1', 'Mei Lin', ['Other College'])]
        result, _ = self.resolve(records, anchors('B1'))
        ranking = IdentityRanking()
        self.assertFalse(any(ranking.has_precedence(profile) for profile in result.candidates))

    def test_orcid_conflict_is_penalised(self):
        conflicting = C('openalex', 'B1', 'Mei Lin', SCHOOL, orcid='0000-0000-0000-0009')
        result, _ = self.resolve(
            [C('openalex', 'A1', 'Mei Lin', SCHOOL), conflicting], anchors(orcid='0000-0000-0000-0001')
        )
        by_id = {profile.author_ids['openalex']: profile for profile in result.candidates}
        self.assertLess(by_id['B1'].match_score, by_id['A1'].match_score)
        self.assertIn('ORCID conflicts with the supplied ORCID', by_id['B1'].evidence)


if __name__ == '__main__':
    unittest.main()


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        from paper_author_searcher.identity_adjudication import AdjudicationVerdict

        self.Verdict = AdjudicationVerdict

    def resolver(self, verdict):
        class Judge:
            calls = 0

            def judge(inner, query, first, second):
                Judge.calls += 1
                return verdict

        records = [C('openalex', 'A1', 'Mei Lin', SCHOOL), C('openalex', 'A2', 'Mei Lin', SCHOOL)]
        return ProfessorResolver([Provider(records)], adjudicator=Judge()), Judge

    def test_confident_same_person_verdict_merges_profiles(self):
        resolver, _ = self.resolver(self.Verdict('yes', 'both', 0.9, 'Shared lab', model='fixture'))
        result = resolver.resolve(Q('Mei Lin', 'Example University'))
        self.assertEqual(sorted(result.profile.author_id_aliases['openalex']), ['A1', 'A2'])
        self.assertEqual(result.profile.status, 'resolved')
        self.assertIn('LLM adjudication', result.profile.adjudication)

    def test_confident_target_verdict_breaks_the_tie(self):
        resolver, _ = self.resolver(self.Verdict('no', 'second', 0.95, 'Topics match the query'))
        result = resolver.resolve(Q('Mei Lin', 'Example University'))
        self.assertEqual(len(result.candidates), 2)
        self.assertIsNone(result.ambiguity)
        self.assertIsNotNone(result.profile.adjudication)

    def test_low_confidence_or_unsure_leaves_the_contest_ambiguous(self):
        for verdict in (self.Verdict('yes', 'both', 0.5, 'weak'), self.Verdict('unsure', 'unsure', 0.9, '?')):
            resolver, _ = self.resolver(verdict)
            self.assertEqual(resolver.resolve(Q('Mei Lin', 'Example University')).profile.status, 'ambiguous')

    def test_anchored_contests_never_reach_the_adjudicator(self):
        resolver, judge = self.resolver(self.Verdict('yes', 'both', 0.9, 'x'))
        resolver.anchor_finder = FixedAnchors(anchors('A1'))
        resolver.resolve(query())
        self.assertEqual(judge.calls, 0)

    def test_claude_adjudicator_parses_structured_output_and_handles_refusal(self):
        from types import SimpleNamespace

        from paper_author_searcher.identity_adjudication import ClaudeIdentityAdjudicator

        class Messages:
            def __init__(self, response):
                self.response, self.kwargs = response, None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return self.response

        good = SimpleNamespace(
            stop_reason='end_turn',
            model='claude-opus-5-5',
            content=[
                SimpleNamespace(
                    type='text', text='{"same_person": "yes", "target": "both", "confidence": 0.86, "rationale": "lab"}'
                )
            ],
        )
        messages = Messages(good)
        client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
        first, second = [C('openalex', 'A1', 'Mei Lin', SCHOOL)], [C('openalex', 'A2', 'Mei Lin', SCHOOL)]
        verdict = ClaudeIdentityAdjudicator(client).judge(query(), first, second)
        self.assertEqual((verdict.same_person, verdict.confidence), ('yes', 0.86))
        self.assertEqual(messages.kwargs['model'], 'claude-opus-5-5')
        self.assertEqual(messages.kwargs['output_config']['format']['type'], 'json_schema')
        self.assertIn('"openalex:A2"', messages.kwargs['messages'][0]['content'])
        messages.response = SimpleNamespace(stop_reason='refusal', content=[])
        self.assertTrue(ClaudeIdentityAdjudicator(client).judge(query(), first, second).errors)


class AnchorEvidenceTests(unittest.TestCase):
    def test_legacy_text_dicts_and_instances_are_accepted(self):
        expected = AnchorEvidence('semantic_scholar', 'B2', 'author of ARXIV:2501.00001', work='arxiv:2501.00001')
        self.assertEqual(AnchorEvidence.coerce('semantic_scholar B2: author of ARXIV:2501.00001'), expected)
        self.assertEqual(AnchorEvidence.coerce(dataclasses.asdict(expected)), expected)
        self.assertIs(AnchorEvidence.coerce(expected), expected)
        self.assertEqual(str(expected), 'semantic_scholar B2: author of ARXIV:2501.00001')

    def test_anchor_kind_is_kept_or_recovered_from_old_checkpoints(self):
        self.assertEqual(AnchorEvidence.coerce('openalex A1: ORCID 0000-0001').kind, 'orcid')
        self.assertEqual(AnchorEvidence.coerce('openalex A1: author of DOI 10.1/x').kind, 'paper')
        stored = {'source': 'openalex', 'author_id': 'A1', 'reason': 'custom', 'kind': 'orcid'}
        self.assertEqual(AnchorEvidence.coerce(stored).kind, 'orcid')

    def test_anchor_work_is_recovered_from_old_reason_texts(self):
        cases = {
            'author of DOI 10.1000/XYZ': 'doi:10.1000/xyz',
            'author of DOI:10.48550/arxiv.2401.01234': 'arxiv:2401.01234',
            'author of arXiv 2401.01234': 'arxiv:2401.01234',
            'author of title "Stable Anodes"': 'title:stable anodes',
            'ORCID 0000-0001': '',
        }
        for reason, work in cases.items():
            self.assertEqual(AnchorEvidence.coerce('openalex A1: ' + reason).work, work, reason)

    def test_malformed_legacy_text_is_rejected(self):
        with self.assertRaises(ValueError):
            AnchorEvidence.coerce('no separator here')

    def test_profiles_from_old_checkpoints_load_structured_anchors(self):
        profile = ProfessorProfile(
            'Mei Lin', [], {'openalex': 'A1'}, 0.9, 'resolved', [], identity_anchors=['openalex A1: ok']
        )
        self.assertEqual(profile.identity_anchors, [AnchorEvidence('openalex', 'A1', 'ok')])

    def test_evidence_lookup_matches_fields_not_prefixes(self):
        found = IdentityAnchors()
        found.add('openalex', 'A1', 'first')
        found.add('openalex', 'A10', 'second')
        self.assertEqual([item.reason for item in found.evidence_for('openalex', 'A1')], ['first'])
