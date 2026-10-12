"""Publication-first review and candidate revisions use synthetic references only."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from test_ground_truth_pilot import dataset, review_rows, coverage
from scripts.ground_truth.export_truth import completeness_reasons
from scripts.ground_truth.review_candidates import validate_reviews
from scripts.ground_truth.pilot_review import relationship_suggestions
from scripts.ground_truth.grouped_review import build_grouped_review, review_template, annotation_rows, write_review
from scripts.ground_truth.prepare_revision import prepare_revision, revision_coverage


def grouped(value, sources=None):
    return build_grouped_review(value, relationship_suggestions(value['papers']), sources or {})


def source_pack():
    return {'source_scope': 'independent_official_references', 'next_dataset_id': 'synthetic_pilot_v2',
            'additions': [{'omission_id': 'missing_journal', 'primary_reference': {
                'reference_id': 'new_official_record', 'professor_id': 'test_professor', 'source_id': 'publisher_journal',
                'source_url': 'https://publisher.example.edu/new-journal',
                'collection_method': 'independent_official_publisher_metadata',
                'metadata': {'title': 'New independently supported journal publication',
                             'authors': ['Pat Researcher', 'Sam Scientist'], 'year': 2021, 'doi': '10.1234/new-journal',
                             'venue': 'Test Journal', 'publication_type': 'journal_article'},
                'extra': {'publication_identity_url': 'https://publisher.example.edu/new-journal'}},
                'supporting_references': [{'reference_id': 'bibliography_secondary_claim',
                    'source_url': 'https://university.example.edu/publications',
                    'collection_method': 'independent_bibliography', 'metadata': {},
                    'extra': {'secondary_journal_claim': {'year': 2021, 'venue': 'Test Journal'}}}],
                'review_questions': ['Online and issue dates need separate human checking.']}]}


class GroupedReviewTests(unittest.TestCase):
    def test_shared_preprint_does_not_merge_formal_versions(self):
        value = dataset(3)
        value['papers'][1].update(publication_type='journal_article', year=2022)
        value['papers'][2].update(publication_type='preprint', year=2020)
        refs = [paper['reference_evidence'][0]['reference_id'] for paper in value['papers'][:2]]
        value['papers'][2]['reference_evidence'][0]['extra']['linked_from_reference_ids'] = refs
        view = grouped(value)
        self.assertEqual(len(view['groups']), 2)
        self.assertTrue(all(group['preprint_candidate_ids'] for group in view['groups']))
        self.assertTrue(all(group['related_formal_group_ids'] for group in view['groups']))
        self.assertEqual(len(view['preprint_records']), 1)

    def test_duplicate_records_remain_distinct_candidates_in_display_group(self):
        value = dataset(2)
        value['papers'][1].update(title=value['papers'][0]['title'], doi=value['papers'][0]['doi'])
        before = deepcopy(value)
        view = grouped(value)
        self.assertEqual(len(view['groups']), 1)
        self.assertEqual(len(view['groups'][0]['candidate_ids']), 2)
        self.assertEqual(value, before)
        self.assertEqual(view['human_confirmed_count'], 0)

    def test_conflicting_dois_block_display_union(self):
        value = dataset(2)
        value['papers'][1]['title'] = value['papers'][0]['title']
        self.assertEqual(len(grouped(value)['groups']), 2)

    def test_conflicting_authors_block_display_union_even_with_same_doi(self):
        value = dataset(2)
        value['papers'][1].update(title=value['papers'][0]['title'], doi=value['papers'][0]['doi'], authors=['Different Person'])
        self.assertEqual(len(grouped(value)['groups']), 2)

    def test_orphan_preprint_retained_and_reviewed_separately(self):
        value = dataset(2)
        value['papers'][1]['publication_type'] = 'preprint'
        view = grouped(value)
        self.assertEqual(len(view['standalone_preprint_ids']), 1)
        self.assertEqual(len(review_template(view)['preprint_reviews']), 1)

    def test_program_metadata_not_recommended_as_verified_formal_publication(self):
        value = dataset()
        pack = {'observations': [{'candidate_ids': [value['papers'][0]['candidate_id']],
                                'source_role': 'official_program', 'access_status': 'metadata_observed',
                                'url': 'https://conference.example.edu/program',
                                'metadata': {key: value['papers'][0][key] for key in ('title', 'authors', 'year', 'doi', 'venue', 'publication_type')}}]}
        group = grouped(value, pack)['groups'][0]
        self.assertIn('补充', group['suggested_judgment'])
        self.assertEqual(group['review_status'], 'candidate')

    def test_human_fields_remain_blank_in_template(self):
        review = review_template(grouped(dataset()))['groups'][0]['human_review']
        for field in ('reviewer', 'reviewed_at', 'canonical_group_id', 'identity_checked', 'metadata_checked', 'type_checked', 'version_checked'):
            self.assertEqual(review[field], '')
        self.assertEqual(review['review_status'], 'candidate')
        self.assertFalse(review['use_suggested_metadata'])

    def test_unreviewed_group_expansion_never_infers_decisions(self):
        value = dataset(2)
        view = grouped(value)
        rows = annotation_rows(value, view, review_template(view))
        self.assertEqual([row['review_status'] for row in rows], ['candidate', 'candidate'])
        self.assertTrue(all(not row['reviewer'] and not row['identity_checked'] for row in rows))

    def test_suggested_metadata_needs_explicit_human_approval(self):
        value = dataset()
        view = grouped(value)
        annotations = review_template(view)
        annotations['groups'][0]['human_review']['use_suggested_metadata'] = True
        with self.assertRaisesRegex(ValueError, 'explicit human'):
            annotation_rows(value, view, annotations)

    def test_group_approval_preserves_checks_and_corrects_published_venue(self):
        value = dataset()
        paper = value['papers'][0]
        corrected = {key: paper[key] for key in ('title', 'authors', 'year', 'doi', 'venue', 'publication_type')}
        corrected['venue'] = 'Checked formal venue'
        view = grouped(value, {'observations': [{'candidate_ids': [paper['candidate_id']], 'source_role': 'formal_publication',
                                               'access_status': 'metadata_observed', 'url': 'https://publisher.example.edu/checked',
                                               'metadata': corrected}]})
        annotations = review_template(view)
        review = review_rows(value)[0]
        annotations['groups'][0]['human_review'].update({key: val for key, val in review.items()
                                                        if key in annotations['groups'][0]['human_review']})
        annotations['groups'][0]['human_review'].update(use_suggested_metadata=True, identity_checked=True)
        rows = annotation_rows(value, view, annotations)
        self.assertEqual(rows[0]['identity_checked'], 'true')
        result = validate_reviews(value, rows)
        self.assertEqual(result['papers'][0]['venue'], 'Checked formal venue')
        self.assertEqual(result['papers'][0]['human_review']['reviewed_venue'], 'Checked formal venue')
        self.assertEqual(value['papers'][0]['venue'], 'Test Conference')

    def test_stale_annotations_rejected(self):
        value = dataset()
        view = grouped(value)
        annotations = review_template(view)
        annotations['grouped_review_sha256'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'stale'):
            annotation_rows(value, view, annotations)

    def test_changed_or_repeated_group_membership_rejected(self):
        value = dataset()
        view = grouped(value)
        annotations = review_template(view)
        annotations['groups'].append(deepcopy(annotations['groups'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            annotation_rows(value, view, annotations)

    def test_existing_manual_files_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            view = grouped(dataset())
            write_review(view, Path(directory))
            with self.assertRaisesRegex(ValueError, 'overwritten'):
                write_review(view, Path(directory))


class CandidateRevisionTests(unittest.TestCase):
    def test_revision_appends_without_altering_original_or_human_labels(self):
        original = dataset()
        before = deepcopy(original)
        revised = prepare_revision(original, source_pack())
        self.assertEqual(original, before)
        self.assertEqual(revised['papers'][:-1], original['papers'])
        self.assertEqual(len(revised['papers']), 2)
        self.assertFalse(revised['complete_positive_list'])
        added = revised['papers'][-1]
        self.assertEqual(added['review_status'], 'candidate')
        self.assertTrue(all(not field for field in added['human_review'].values()))

    def test_revision_requires_independent_provenance_and_support(self):
        pack = source_pack()
        pack['source_scope'] = 'agent_collection'
        with self.assertRaisesRegex(ValueError, 'independent'):
            prepare_revision(dataset(), pack)
        pack = source_pack()
        pack['additions'][0]['supporting_references'] = []
        with self.assertRaisesRegex(ValueError, 'supporting'):
            prepare_revision(dataset(), pack)

    def test_missing_publication_cannot_be_disguised_as_outside_scope(self):
        pack = source_pack()
        pack['additions'][0]['primary_reference']['metadata']['year'] = 2019
        with self.assertRaisesRegex(ValueError, 'outside'):
            prepare_revision(dataset(), pack)
        pack['additions'][0]['primary_reference']['metadata'].update(year=2021, publication_type='other')
        with self.assertRaisesRegex(ValueError, 'hide omissions'):
            prepare_revision(dataset(), pack)

    def test_existing_human_decisions_cannot_be_reset_by_revision(self):
        value = dataset()
        value['papers'][0]['review_status'] = 'confirmed'
        with self.assertRaisesRegex(ValueError, 'reset'):
            prepare_revision(value, source_pack())

    def test_added_identifier_cannot_be_duplicated(self):
        value = dataset()
        pack = source_pack()
        pack['additions'][0]['primary_reference']['metadata']['doi'] = value['papers'][0]['doi']
        with self.assertRaisesRegex(ValueError, 'already exists'):
            prepare_revision(value, pack)

    def test_new_coverage_remains_pending_and_links_to_added_candidate(self):
        pack = source_pack()
        revised = prepare_revision(dataset(), pack)
        prior = coverage()
        prior['omissions'] = [{'omission_id': 'missing_journal', 'decision': 'pending'}]
        checks = revision_coverage(prior, revised, pack)
        self.assertFalse(checks['approved'])
        self.assertEqual(checks['reviewer'], '')
        self.assertEqual(checks['omissions'][0]['decision'], 'pending')
        self.assertEqual(checks['omissions'][0]['next_revision_candidate_id'], revised['papers'][-1]['candidate_id'])

    def test_supported_in_scope_omission_cannot_be_closed_as_out_of_scope(self):
        revised = prepare_revision(dataset(), source_pack())
        adjudicated = validate_reviews(revised, review_rows(revised))
        checks = coverage()
        checks['omissions'] = [{'omission_id': 'missing_journal', 'decision': 'out_of_scope', 'human_notes': 'Excluded'}]
        reasons = completeness_reasons(adjudicated, checks, revised['review_requirements'])
        self.assertTrue(any('cannot be hidden' in reason for reason in reasons))
        checks['omissions'][0].update(decision='already_covered', covered_candidate_id=revised['papers'][-1]['candidate_id'],
                                     human_notes='Reviewed actual journal identity; included independently')
        reasons = completeness_reasons(adjudicated, checks, revised['review_requirements'])
        self.assertFalse(any('omission' in reason.lower() for reason in reasons))

    def test_supported_omission_cannot_point_at_an_unrelated_positive(self):
        revised = prepare_revision(dataset(), source_pack())
        adjudicated = validate_reviews(revised, review_rows(revised))
        checks = coverage()
        checks['omissions'] = [{'omission_id': 'missing_journal', 'decision': 'already_covered',
                               'covered_candidate_id': revised['papers'][0]['candidate_id'], 'human_notes': 'Covered'}]
        reasons = completeness_reasons(adjudicated, checks, revised['review_requirements'])
        self.assertTrue(any('actual new reference identity' in reason for reason in reasons))


if __name__ == '__main__':
    unittest.main()
