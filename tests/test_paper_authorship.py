"""Synthetic API payloads exercise paper-level identity and unsafe author lists."""
import json
import unittest
from dataclasses import asdict, replace
from unittest.mock import patch

from paper_author_searcher.config import OwnershipConfig
from paper_author_searcher.http import SourceError
from paper_author_searcher.models import PaperCollectionResult, PaperSourceResult
from paper_author_searcher.paper_deduplication import deduplicate_papers
from paper_author_searcher.paper_reanalysis import saved_collection_profile
from paper_author_searcher.paper_ownership import PaperOwnershipValidator
from paper_author_searcher.paper_providers import OpenAlexPaperProvider, SemanticScholarPaperProvider
from paper_author_searcher.providers import OpenAlexProvider, SemanticScholarProvider
from paper_author_searcher.cli import _refresh_collection
from test_paper_validation import record, profile, validate
from test_paper_collector import openalex_work


class SequenceClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, xml=False):
        self.calls.append((url, params))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def s2_work(author_id='author-b'):
    return {
        'paperId': 'synthetic-paper', 'title': 'Synthetic publication', 'year': 2024,
        'authors': [{'authorId': author_id, 'name': 'Alex Morgan'},
                    {'authorId': 'coauthor-id', 'name': 'Jane Smith'}],
        'externalIds': {'DOI': '10.example/one', 'CorpusId': 123},
    }


class PaperAuthorProviderTests(unittest.TestCase):
    def test_openalex_author_entity_orcid_is_not_independent_ownership_proof(self):
        item = openalex_work('https://openalex.org/Wsynthetic', 'author-a')
        item['type'] = 'article'
        item['authorships'][0]['author']['orcid'] = 'https://orcid.org/profile-orcid'
        parsed = OpenAlexPaperProvider()._parse_paper(item, 'author-a')
        self.assertEqual(parsed.author_details[0]['orcid_scope'], 'resolved_author')
        result = validate([parsed])[0]
        self.assertEqual(result.ownership_status, 'needs_review')
        self.assertTrue(any('not independent' in text for text in result.ownership_evidence))

    def test_openalex_requests_and_preserves_raw_byline_institutions_and_ids(self):
        item = openalex_work('https://openalex.org/Wsynthetic', 'author-a')
        item['is_authors_truncated'] = False
        item['ids'] = {'openalex': item['id'], 'doi': 'https://doi.org/10.example/one', 'pmid': None}
        detail = item['authorships'][0]
        detail.update({
            'raw_author_name': 'A. Morgan', 'author_position': 'first', 'is_corresponding': True,
            'institutions': [{'id': 'Isynthetic', 'display_name': 'Example University'}],
            'raw_affiliation_strings': ['Department, Example University'],
            'affiliations': [{'raw_affiliation_string': 'Department, Example University',
                              'institution_ids': ['Isynthetic']}],
        })
        detail['author']['orcid'] = 'profile-orcid'
        client = SequenceClient([{'results': [item], 'meta': {'next_cursor': None}}])
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client)).collect('author-a')
        paper = result.papers[0]
        self.assertTrue(result.complete)
        self.assertTrue(paper.target_author_present)
        self.assertTrue(paper.authors_complete)
        self.assertEqual(paper.authors, ['A. Morgan'])
        self.assertEqual(paper.author_details[0]['raw_author_name'], 'A. Morgan')
        self.assertEqual(paper.author_details[0]['institutions'], detail['institutions'])
        self.assertEqual(paper.author_details[0]['affiliations'], detail['affiliations'])
        self.assertEqual(paper.author_details[0]['orcid'], 'profile-orcid')
        self.assertEqual(paper.raw_metadata, item)
        self.assertNotIn('pmid', paper.external_ids)
        requested = client.calls[0][1]['select'].split(',')
        self.assertIn('authorships', requested)
        self.assertIn('ids', requested)
        self.assertIn('is_authors_truncated', requested)
        self.assertNotIn('authors_count', requested)

    def test_openalex_missing_id_and_missing_list_are_unknown(self):
        adapter = OpenAlexPaperProvider()
        item = openalex_work('synthetic')
        del item['authorships'][0]['author']['id']
        self.assertIsNone(adapter._parse_paper(item, 'author-a').target_author_present)
        item['is_authors_truncated'] = None
        self.assertIsNone(adapter._parse_paper(item, 'author-a').authors_complete)
        item['authorships'] = []
        self.assertIsNone(adapter._parse_paper(item, 'author-a').target_author_present)

    def test_truncated_openalex_list_expanded_before_asserting_absence(self):
        partial = openalex_work('https://openalex.org/Wsynthetic', 'other-author')
        partial['is_authors_truncated'] = True
        full = openalex_work(partial['id'], 'author-a')
        full['is_authors_truncated'] = False
        client = SequenceClient([{'results': [partial], 'meta': {'next_cursor': None}}, full])
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client)).collect('author-a')
        self.assertTrue(result.complete)
        self.assertTrue(result.papers[0].target_author_present)
        self.assertEqual(client.calls[1][0], 'https://api.openalex.org/works/Wsynthetic')
        self.assertEqual(result.papers[0].raw_metadata['_list_response'], partial)
        self.assertEqual(result.papers[0].raw_metadata['_authorship_expansion'], full)

    def test_failed_author_expansion_keeps_raw_paper_and_unknown_membership(self):
        partial = openalex_work('https://openalex.org/Wsynthetic', 'other-author')
        partial['is_authors_truncated'] = True
        client = SequenceClient([{'results': [partial], 'meta': {'next_cursor': None}}, SourceError('HTTP 429')])
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client)).collect('author-a')
        self.assertTrue(result.pagination_complete)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.papers), 1)
        self.assertTrue(result.metadata_errors)
        self.assertIsNone(result.papers[0].target_author_present)
        self.assertEqual(validate(result.papers)[0].ownership_status, 'needs_review')

    def test_s2_profile_enrichment_once_and_only_on_explicit_target_id(self):
        first = s2_work()
        missing = s2_work(None)
        client = SequenceClient([
            {'offset': 0, 'data': [first, missing]},
            {'authorId': 'author-b', 'name': 'Alex Morgan', 'externalIds': {'ORCID': ['profile-orcid']},
             'affiliations': ['Current Institution']},
        ])
        result = SemanticScholarPaperProvider(SemanticScholarProvider(client=client)).collect('author-b')
        self.assertTrue(result.complete)
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(result.papers[0].target_author_present)
        self.assertIsNone(result.papers[1].target_author_present)
        target = result.papers[0].author_details[0]
        self.assertEqual(target['orcid'], 'profile-orcid')
        self.assertEqual(target['orcid_scope'], 'author_profile')
        self.assertEqual(target['profile_affiliations'], ['Current Institution'])
        self.assertNotIn('institutions', target)
        self.assertNotIn('author_profile', result.papers[0].author_details[1])
        self.assertNotIn('author_profile', result.papers[1].author_details[0])
        self.assertEqual(result.papers[0].external_ids['corpusid'], '123')
        self.assertIn('authors', client.calls[0][1]['fields'].split(','))
        self.assertNotIn('authors.externalIds', client.calls[0][1]['fields'])

    def test_s2_author_enrichment_error_does_not_drop_bibliography(self):
        client = SequenceClient([{'offset': 0, 'data': [s2_work()]}, SourceError('HTTP 429')])
        result = SemanticScholarPaperProvider(SemanticScholarProvider(client=client)).collect('author-b')
        self.assertEqual(len(result.papers), 1)
        self.assertTrue(result.pagination_complete)
        self.assertFalse(result.complete)
        self.assertTrue(result.metadata_errors)
        self.assertIsNone(result.papers[0].author_details[0]['orcid'])

    def test_s2_complete_wrong_author_id_is_false_not_unknown(self):
        client = SequenceClient([
            {'offset': 0, 'data': [s2_work('namesake')]},
            {'authorId': 'author-b', 'name': 'Alex Morgan', 'externalIds': {}},
        ])
        result = SemanticScholarPaperProvider(SemanticScholarProvider(client=client)).collect('author-b')
        self.assertFalse(result.papers[0].target_author_present)
        self.assertEqual(validate(result.papers)[0].ownership_status, 'rejected')

    def test_references_and_contributors_never_populate_paper_authors(self):
        item = s2_work('other-author')
        item['references'] = [{'authors': [{'authorId': 'author-b', 'name': 'Alex Morgan'}]}]
        item['contributors'] = [{'authorId': 'author-b', 'name': 'Alex Morgan'}]
        parsed = SemanticScholarPaperProvider()._parse_paper(item, 'author-b')
        self.assertFalse(parsed.target_author_present)
        self.assertNotIn('author-b', [detail['author_id'] for detail in parsed.author_details])


class OwnershipIdentityTests(unittest.TestCase):
    def test_saved_manual_selection_preserves_selected_candidate_school_evidence(self):
        chosen = profile()
        chosen.requested_university = 'Example University'
        chosen.matched_affiliations = ['Example University']
        chosen.university = 'Example University'
        other = profile()
        other.name = 'Competing Author'
        other.author_ids = {'openalex': 'other-id'}
        other.author_id_aliases = {}
        other.affiliations = ['Unrelated University']
        saved = {'profile': asdict(other), 'candidates': [asdict(other), asdict(chosen)],
                 'collection': {'selected_author_ids': {'openalex': ['author-a']}},
                 'collection_identity': {'name': chosen.name, 'status': 'resolved', 'orcid': chosen.orcid}}
        restored = saved_collection_profile(saved)
        self.assertEqual(restored.name, chosen.name)
        self.assertEqual(restored.affiliations, chosen.affiliations)
        self.assertEqual(restored.requested_university, chosen.requested_university)
        self.assertEqual(restored.matched_affiliations, chosen.matched_affiliations)
        self.assertEqual(restored.author_ids, {'openalex': 'author-a'})

    def test_profile_orcid_cannot_verify_or_seed_other_papers(self):
        records = [record(title='Seed one', doi='10.example/one', orcid='profile-orcid'),
                   record(title='Seed two', doi='10.example/two', orcid='profile-orcid'),
                   record(title='Candidate', doi='10.example/new')]
        for item in records[:2]:
            item.author_details[0]['orcid_scope'] = 'resolved_author'
        papers = validate(records)
        self.assertEqual({paper.ownership_status for paper in papers}, {'needs_review'})
        self.assertTrue(any('not independent' in text for text in papers[0].ownership_evidence))

    def test_author_profile_and_unknown_orcid_scopes_remain_review(self):
        for scope in ('author_profile', None, 'missing'):
            item = record(orcid='profile-orcid')
            if scope == 'missing':
                item.author_details[0].pop('orcid_scope')
            else:
                item.author_details[0]['orcid_scope'] = scope
            self.assertEqual(validate([item])[0].ownership_status, 'needs_review')

    def test_correlated_orcid_can_still_use_independent_paper_institution(self):
        item = record(orcid='profile-orcid')
        item.author_details[0].update(orcid_scope='resolved_author', affiliation_scope='paper_authorship',
                                    institutions=[{'display_name': 'Example University'}])
        self.assertEqual(validate([item])[0].ownership_status, 'verified')

    def test_unconfirmed_profile_institution_does_not_verify_same_named_author(self):
        target = profile()
        target.affiliations.append('Other University')
        target.requested_university = 'Example University'
        target.matched_affiliations = ['Example University']
        item = record()
        item.author_details[0].update(affiliation_scope='paper_authorship',
                                    institutions=[{'display_name': 'Other University'}])
        papers = deduplicate_papers([item])
        PaperOwnershipValidator().validate(papers, target)
        self.assertEqual(papers[0].ownership_status, 'needs_review')

    def test_comma_reversed_raw_byline_is_a_format_variant(self):
        item = record(orcid='profile-orcid')
        item.author_details[0]['raw_author_name'] = 'Morgan, Alex'
        self.assertEqual(validate([item])[0].ownership_status, 'verified')

    def test_comma_reversed_initials_are_not_identity_conflicts(self):
        item = record()
        item.author_details[0]['raw_author_name'] = 'Morgan, A.'
        self.assertEqual(validate([item])[0].ownership_status, 'needs_review')
        item.author_details[0]['raw_author_name'] = 'Morgan, AM'
        self.assertEqual(validate([item])[0].ownership_status, 'needs_review')

    def test_printed_byline_conflict_cannot_be_hidden_by_resolved_name_or_orcid(self):
        item = record(orcid='profile-orcid')
        item.author_details[0]['raw_author_name'] = 'Someone Else'
        self.assertEqual(validate([item])[0].ownership_status, 'rejected')

    def test_matching_target_id_byline_and_paper_institution_verify(self):
        item = record()
        item.author_details[0].update({'institutions': [{'display_name': 'Example University'}],
                                       'affiliation_scope': 'paper_authorship'})
        paper = validate([item])[0]
        self.assertEqual(paper.ownership_status, 'verified')
        self.assertTrue(any('paper-level institution' in message for message in paper.ownership_evidence))

    def test_profile_affiliations_do_not_substitute_for_paper_institutions(self):
        item = record()
        item.author_details[0]['profile_affiliations'] = ['Example University']
        self.assertEqual(validate([item])[0].ownership_status, 'needs_review')

    def test_missing_ids_cannot_upgrade_using_known_coauthors_or_other_source(self):
        seeds = [record(title='Seed one', doi='10.example/one', orcid='profile-orcid'),
                 record(title='Seed two', doi='10.example/two', orcid='profile-orcid')]
        missing = replace(record(title='Candidate', doi='10.example/candidate'),
                          author_details=[{'name': 'Alex Morgan'}])
        other = record('semantic_scholar', title='Candidate', doi='10.example/candidate')
        self.assertEqual(validate(seeds + [missing])[-1].ownership_status, 'needs_review')
        combined = validate([missing, other])[0]
        # One well-identified source does not create IDs in the incomplete source record.
        self.assertEqual(combined.records[0].ownership_status, 'needs_review')
        self.assertEqual(combined.ownership_status, 'needs_review')

    def test_large_legacy_author_lists_cannot_verify_by_coauthor_overlap(self):
        authors = ['Alex Morgan'] + ['Synthetic Author {}'.format(index) for index in range(80)]
        left = replace(record(), authors=authors, author_details=[])
        right = replace(record('semantic_scholar'), authors=authors, author_details=[])
        paper = validate([left, right])[0]
        self.assertEqual(paper.ownership_status, 'needs_review')
        self.assertTrue(any('Large author list' in message for message in paper.ownership_evidence))
        self.assertEqual(len(paper.records), 2)

    def test_large_record_does_not_seed_other_coauthor_decisions(self):
        authors = ['Alex Morgan', 'Jane Smith', 'John Green'] + ['Synthetic Author {}'.format(i) for i in range(60)]
        seeds = [replace(record(title='Seed one', doi='10.example/one', orcid='profile-orcid'), authors=authors),
                 replace(record(title='Seed two', doi='10.example/two', orcid='profile-orcid'), authors=authors)]
        papers = validate(seeds + [record(title='Candidate', doi='10.example/candidate')])
        self.assertEqual(papers[0].ownership_status, 'verified')
        self.assertEqual(papers[-1].ownership_status, 'needs_review')

    def test_large_cross_source_id_lists_need_independent_identity_evidence(self):
        authors = ['Alex Morgan'] + ['Synthetic Author {}'.format(i) for i in range(80)]
        left = replace(record(), authors=authors)
        right = replace(record('semantic_scholar'), authors=authors)
        self.assertEqual(validate([left, right])[0].ownership_status, 'needs_review')
        left.author_details[0]['orcid'] = 'profile-orcid'
        self.assertEqual(validate([left, right])[0].ownership_status, 'verified')

    def test_multiple_orcids_do_not_verify_or_seed(self):
        item = record()
        item.author_details[0]['orcids'] = ['first-orcid', 'second-orcid']
        self.assertEqual(validate([item])[0].ownership_status, 'needs_review')

    def test_nonstandard_entries_stay_review_even_with_matching_orcid(self):
        cases = [('Edited scientific volume', 'proceedings'),
                 ('Peer Review #2 of a synthetic manuscript', 'Review'),
                 ('Decision letter for a synthetic manuscript', 'article'),
                 ('A reviewer report', 'peer-review')]
        for title, publication_type in cases:
            with self.subTest(title=title):
                item = record(title=title, publication_type=publication_type, orcid='profile-orcid')
                self.assertEqual(validate([item])[0].ownership_status, 'needs_review')

    def test_target_editor_role_is_explicitly_rejected_and_preserved(self):
        item = record(orcid='profile-orcid')
        item.author_details[0]['role'] = 'editor'
        paper = validate([item])[0]
        self.assertEqual(paper.ownership_status, 'rejected')
        self.assertEqual(paper.records[0].author_details[0]['role'], 'editor')

    def test_conflicting_nonstandard_source_type_blocks_merged_verification(self):
        left = record(orcid='profile-orcid')
        right = record('semantic_scholar', publication_type='proceedings')
        paper = validate([left, right])[0]
        self.assertEqual(paper.ownership_status, 'needs_review')
        self.assertEqual({r.ownership_status for r in paper.records}, {'needs_review'})

    def test_cross_source_ids_are_verified_in_their_own_namespaces(self):
        left = replace(record(), authors=['Alex Morgan'])
        right = replace(record('semantic_scholar'), authors=['Alex Morgan'])
        paper = validate([left, right])[0]
        self.assertEqual(paper.ownership_status, 'verified')
        self.assertTrue(any('source-scoped target Author IDs' in text for text in paper.ownership_evidence))
        right.author_details[0]['author_id'] = left.source_author_id
        conflict = validate([left, right])[0]
        self.assertEqual(conflict.ownership_status, 'needs_review')
        self.assertEqual(conflict.records[1].ownership_status, 'rejected')

    def test_cross_source_unknown_orcid_conflict_cannot_be_coauthor_upgraded(self):
        target = replace(profile(), orcid=None)
        seeds = [record(title='Seed one', doi='10.example/one'), record(title='Seed two', doi='10.example/two')]
        for seed in seeds:
            seed.author_details[0]['institutions'] = [{'display_name': 'Example University'}]
        left = record(orcid='first-orcid')
        right = record('semantic_scholar', orcid='second-orcid')
        papers = deduplicate_papers(seeds + [left, right])
        PaperOwnershipValidator().validate(papers, target)
        self.assertEqual(papers[-1].ownership_status, 'needs_review')
        self.assertEqual({r.ownership_status for r in papers[-1].records}, {'needs_review'})

    def test_dblp_pid_suffixes_do_not_collide(self):
        item = replace(record(), source='dblp', source_author_id='person/target',
                       author_details=[{'author_id': 'other/target', 'name': 'Alex Morgan'}])
        target = replace(profile(), author_ids={'dblp': 'person/target'}, author_id_aliases={})
        papers = deduplicate_papers([item])
        PaperOwnershipValidator().validate(papers, target)
        self.assertEqual(papers[0].ownership_status, 'rejected')

    def test_coauthor_limit_is_configurable(self):
        item = replace(record(), authors=['Alex Morgan', 'Jane Smith', 'John Green'], author_details=[])
        validator = PaperOwnershipValidator(OwnershipConfig(maximum_coauthor_authors=2))
        self.assertFalse(validator._coauthor_eligible(item, profile()))


class AuthorshipPersistenceTests(unittest.TestCase):
    def test_merge_keeps_source_scoped_details_and_conflicting_external_ids(self):
        left = replace(record(), author_details=[], target_author_present=None, external_ids={})
        right = replace(record('semantic_scholar'), target_author_present=True,
                        external_ids={'corpusid': '123'})
        third = replace(record(), source_paper_id='another', external_ids={'corpusid': '456'})
        merged = deduplicate_papers([left, right, third])[0]
        self.assertEqual(len(merged.records), 3)
        self.assertEqual(len(merged.author_details), 2)
        self.assertEqual({detail['source'] for detail in merged.author_details}, {'openalex', 'semantic_scholar'})
        self.assertTrue(merged.target_author_present)
        self.assertNotIn('corpusid', merged.external_ids)
        self.assertEqual({row['external_ids'].get('corpusid') for row in merged.source_identifiers}, {None, '123', '456'})
        source = PaperSourceResult('openalex', 'author-a', papers=[left, third])
        output = PaperCollectionResult([merged], [source], False, {}, 'resolver_resolved').to_dict()
        restored = json.loads(json.dumps(output))
        self.assertEqual(restored['papers'][0]['records'][1]['author_details'], right.author_details)
        self.assertEqual(restored['papers'][0]['source_identifiers'], merged.source_identifiers)
        self.assertEqual(left.author_details, [])
        self.assertNotIn('source', right.author_details[0])

    def test_unconfirmed_saved_identity_does_not_bypass_refresh_gate(self):
        saved = {'name': 'Alex Morgan', 'status': 'ambiguous', 'collection': {
            'selected_author_ids': {'openalex': ['author-a']}, 'identity_confirmation': 'resolver_resolved',
        }}
        with patch('paper_author_searcher.paper_providers.OpenAlexPaperProvider.collect') as request:
            with self.assertRaises(ValueError):
                _refresh_collection(saved)
            request.assert_not_called()
