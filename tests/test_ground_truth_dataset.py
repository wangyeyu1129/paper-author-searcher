import json
import tempfile
import unittest
from pathlib import Path

from scripts.ground_truth.build_candidates import build_candidates, export_candidates
from scripts.ground_truth.review_candidates import validate_reviews
from paper_author_searcher.evaluation_matching import GroundTruth
from paper_author_searcher.performance_evaluation import PerformanceEvaluator


def scope():
    return {'dataset_id': 'synthetic_review_dataset', 'year_start': 2020, 'year_end': 2022,
            'professors': [{'professor_id': 'pat_reference', 'name': 'Pat Reference',
                            'university': 'Example University', 'identity_risks': []}],
            'core_publication_types': ['conference_paper', 'journal_article', 'review'],
            'extended_publication_types': ['preprint', 'workshop_paper', 'book_chapter'],
            'excluded_publication_types': ['slides', 'poster', 'other']}


def reference(reference_id='one', uri='https://publisher.example.edu/record/one', publication_type='conference_paper'):
    return {'reference_id': reference_id, 'professor_id': 'pat_reference', 'source_id': 'independent_index',
            'source_url': 'https://university.example.edu/publications',
            'collection_method': 'independent_public_bibliography',
            'metadata': {'title': 'Independent reference methods for evaluation', 'authors': ['Pat Reference'],
                         'year': 2021, 'doi': None, 'venue': 'Example Conference', 'publication_type': publication_type},
            'extra': {'publication_identity_url': uri}}


def dataset():
    return build_candidates(scope(), {'source_scope': 'independent_official_references', 'records': [reference()]})


def row(paper):
    return {'candidate_id': paper['candidate_id'], 'professor_id': paper['professor_id'], 'title': paper['title'],
            'authors_json': json.dumps(paper['authors']), 'review_status': 'candidate', 'ownership': 'uncertain',
            'inclusion_decision': 'pending'}


def confirmed_row(paper):
    value = row(paper)
    value.update(review_status='confirmed', ownership='belongs', inclusion_decision='include',
                 reviewer='Synthetic reviewer', reviewed_at='2026-10-11T12:00:00-04:00',
                 canonical_group_id='synthetic_publication', reviewed_publication_type='conference_paper',
                 evidence_urls_json=json.dumps(['https://university.example.edu/publications',
                                                'https://publisher.example.edu/record/one']),
                 identity_checked='true', metadata_checked='true', type_checked='true', version_checked='true')
    return value


class DatasetTests(unittest.TestCase):
    def test_all_automatic_records_remain_candidate_uncertain_even_with_source_hint(self):
        ref = reference()
        ref.update(review_status='confirmed', ownership='belongs')
        value = build_candidates(scope(), {'source_scope': 'independent_official_references', 'records': [ref]})
        self.assertEqual(value['papers'][0]['review_status'], 'candidate')
        self.assertEqual(value['papers'][0]['ownership'], 'uncertain')
        self.assertEqual(value['human_confirmed_count'], 0)
        self.assertIsNone(value['papers'][0]['doi'])

    def test_same_title_year_different_version_identifiers_are_not_merged(self):
        refs = [reference(), reference('preprint', 'https://archive.example.edu/record/one', 'preprint')]
        value = build_candidates(scope(), {'source_scope': 'independent_official_references', 'records': refs})
        self.assertEqual(len(value['papers']), 2)

    def test_same_explicit_url_groups_evidence_but_does_not_confirm(self):
        refs = [reference(), reference('second_observation')]
        value = build_candidates(scope(), {'source_scope': 'independent_official_references', 'records': refs})
        self.assertEqual(len(value['papers']), 1)
        self.assertEqual(len(value['papers'][0]['reference_evidence']), 2)
        self.assertEqual(value['papers'][0]['review_status'], 'candidate')

    def test_retrieval_result_is_not_an_independent_reference_snapshot(self):
        with self.assertRaisesRegex(ValueError, 'independent reference'):
            build_candidates(scope(), {'source_scope': 'agent_collection', 'records': [reference()]})

    def test_pending_format_compatible_and_formal_performance_refuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            export_candidates(dataset(), Path(directory))
            path = Path(directory) / 'professors/pat_reference.pending.json'
            pending = json.loads(path.read_text())
            self.assertEqual(len(GroundTruth.from_dict(pending).papers), 1)
            with self.assertRaisesRegex(ValueError, 'human_reviewed'):
                PerformanceEvaluator().evaluate({'papers': []}, pending)

    def test_export_never_overwrites_pending_or_review_files(self):
        with tempfile.TemporaryDirectory() as directory:
            export_candidates(dataset(), Path(directory))
            with self.assertRaisesRegex(ValueError, 'overwritten'):
                export_candidates(dataset(), Path(directory))


class ReviewTests(unittest.TestCase):
    def test_empty_annotations_never_promote_candidates(self):
        value = dataset()
        result = validate_reviews(value, [row(value['papers'][0])])
        self.assertEqual(result['review_status_counts'], {'candidate': 1})
        self.assertFalse(result['complete_positive_list'])

    def test_confirmed_requires_explicit_human_checklist(self):
        value = dataset()
        annotation = confirmed_row(value['papers'][0])
        annotation['identity_checked'] = ''
        with self.assertRaisesRegex(ValueError, 'explicitly checked'):
            validate_reviews(value, [annotation])

    def test_checked_human_annotation_retains_original_evidence_without_metrics(self):
        value = dataset()
        result = validate_reviews(value, [confirmed_row(value['papers'][0])])
        self.assertEqual(result['review_status_counts'], {'confirmed': 1})
        self.assertEqual(result['papers'][0]['reference_evidence'], value['papers'][0]['reference_evidence'])
        self.assertNotIn('recall', result)

    def test_preprints_and_slides_cannot_be_core_positives(self):
        value = dataset()
        for publication_type in ('preprint', 'slides'):
            annotation = confirmed_row(value['papers'][0])
            annotation['reviewed_publication_type'] = publication_type
            with self.assertRaisesRegex(ValueError, 'core publication type'):
                validate_reviews(value, [annotation])

    def test_duplicates_and_missing_reviewer_are_rejected(self):
        value = dataset()
        annotation = confirmed_row(value['papers'][0])
        with self.assertRaisesRegex(ValueError, 'repeated candidate_id'):
            validate_reviews(value, [annotation, annotation])
        annotation['reviewer'] = ''
        with self.assertRaisesRegex(ValueError, 'human reviewer'):
            validate_reviews(value, [annotation])

    def test_candidates_cannot_receive_positive_labels_without_review(self):
        value = dataset()
        annotation = row(value['papers'][0])
        annotation['ownership'] = 'belongs'
        with self.assertRaisesRegex(ValueError, 'uncertain/pending'):
            validate_reviews(value, [annotation])

    def test_reviewed_year_correction_updates_scope_without_altering_source(self):
        value = dataset()
        annotation = confirmed_row(value['papers'][0])
        annotation.update(reviewed_year='2023', inclusion_decision='exclude')
        result = validate_reviews(value, [annotation])
        self.assertFalse(result['papers'][0]['in_scope'])
        self.assertEqual(result['papers'][0]['reference_evidence'][0]['metadata']['year'], 2021)

    def test_review_timestamp_requires_timezone(self):
        value = dataset()
        annotation = confirmed_row(value['papers'][0])
        annotation['reviewed_at'] = '2026-10-11T12:00:00'
        with self.assertRaisesRegex(ValueError, 'timezone'):
            validate_reviews(value, [annotation])

    def test_conflicting_publication_versions_cannot_share_one_group(self):
        refs = [reference(), reference('preprint', 'https://archive.example.edu/one', 'preprint')]
        value = build_candidates(scope(), {'source_scope': 'independent_official_references', 'records': refs})
        annotations = [confirmed_row(paper) for paper in value['papers']]
        annotations[1].update(reviewed_publication_type='preprint', inclusion_decision='exclude')
        with self.assertRaisesRegex(ValueError, 'conflicting ownership, types'):
            validate_reviews(value, annotations)
