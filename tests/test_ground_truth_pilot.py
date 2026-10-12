"""Synthetic annotations exercise the workflow, never label real professors."""
import json
import io
import csv
import tempfile
import unittest
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from scripts.ground_truth.build_candidates import build_candidates
from scripts.ground_truth.export_truth import export_truth
from scripts.ground_truth.evaluate_pilot import evaluate_pilot, prepare_audit, match_publication
from scripts.ground_truth.pilot_review import select_pilot, relationship_suggestions, prepare_review
from paper_author_searcher.evaluation_matching import GroundTruth, GroundTruthMatcher
from paper_author_searcher.evaluation_cli import main as evaluation_main
from paper_author_searcher.performance_evaluation import PerformanceEvaluator
from scripts.ground_truth.export_truth import main as export_main
from scripts.ground_truth.evaluate_pilot import main as pilot_main


def dataset(count=1):
    scope = {'dataset_id': 'synthetic_pilot', 'year_start': 2020, 'year_end': 2022,
             'professors': [{'professor_id': 'test_professor', 'name': 'Pat Researcher', 'university': 'Test University',
                             'bibliography_url': 'https://university.example.edu/publications',
                             'dblp_reference_url': 'https://index.example.edu/person', 'identity_risks': []}],
             'core_publication_types': ['conference_paper', 'journal_article', 'review'],
             'extended_publication_types': ['preprint', 'workshop_paper', 'book_chapter'],
             'excluded_publication_types': ['poster', 'slides', 'other']}
    records = []
    for number in range(count):
        records.append({'reference_id': 'reference{}'.format(number), 'professor_id': 'test_professor',
                        'source_id': 'independent_directory', 'source_url': 'https://university.example.edu/publications',
                        'collection_method': 'independent_public_bibliography',
                        'metadata': {'title': 'Reliable research {}'.format(number), 'authors': ['Pat Researcher', 'Sam Scientist'],
                                     'year': 2021, 'doi': '10.1234/research{}'.format(number),
                                     'venue': 'Test Conference', 'publication_type': 'conference_paper'},
                        'extra': {'publication_identity_url': 'https://publisher.example.edu/record/{}'.format(number)}})
    return build_candidates(scope, {'source_scope': 'independent_official_references', 'records': records})


def review_rows(value):
    rows = []
    for number, paper in enumerate(value['papers']):
        rows.append({'candidate_id': paper['candidate_id'], 'professor_id': paper['professor_id'], 'title': paper['title'],
                     'authors_json': json.dumps(paper['authors']), 'review_status': 'confirmed',
                     'ownership': 'belongs', 'inclusion_decision': 'include', 'canonical_group_id': 'work{}'.format(number),
                     'reviewer': 'Synthetic human', 'reviewed_at': '2026-10-11T18:00:00+00:00',
                     'evidence_urls_json': json.dumps(['https://university.example.edu/publications', 'https://publisher.example.edu/record']),
                     'identity_checked': 'true', 'metadata_checked': 'true', 'type_checked': 'true', 'version_checked': 'true'})
    return rows


def coverage():
    return {'approved': True, 'reviewer': 'Synthetic human', 'reviewed_at': '2026-10-11T18:00:00+00:00',
            'evidence_urls': ['https://university.example.edu/publications', 'https://publisher.example.edu/directory'],
            'sources': [{'source_id': 'independent_directory', 'human_checked': True}],
            'additional_venue_audit_complete': True, 'identity_conflicts_resolved': True, 'omissions': []}


def truth(count=1):
    value = dataset(count)
    return export_truth(value, review_rows(value), coverage())


def retrieved(paper, status='verified'):
    return {key: deepcopy(paper.get(key)) for key in ('title', 'authors', 'year', 'doi', 'venue', 'publication_type')}, status


def result(value, statuses=None):
    entries = []
    for index, paper in enumerate(value['papers']):
        entry, status = retrieved(paper, (statuses or ['verified'] * len(value['papers']))[index])
        entry['ownership_status'] = status
        entries.append(entry)
    return {'papers': entries}


def annotate_audit(audit, decision='not_belongs'):
    reviewed = deepcopy(audit)
    for index, row in enumerate(reviewed['papers']):
        row.update(decision=decision, canonical_group_id='negative{}'.format(index), reviewer='Synthetic auditor',
                   reviewed_at='2026-10-11T19:00:00+00:00',
                   evidence_urls=['https://publisher.example.edu/record', 'https://university.example.edu/identity'],
                   notes='Synthetic independent byline and publication-scope check')
    return reviewed


class PilotPreparationTests(unittest.TestCase):
    def test_selection_is_bounded_and_original_unchanged(self):
        original = dataset(2)
        before = deepcopy(original)
        pilot = select_pilot(original, 'test_professor')
        self.assertEqual(len(pilot['papers']), 2)
        self.assertEqual(original, before)
        self.assertFalse(pilot['complete_positive_list'])

    def test_explicit_preprint_link_supports_changed_title_and_year_without_merge(self):
        papers = deepcopy(dataset(2)['papers'])
        papers[1].update(title='A different preprint title', year=2019, publication_type='preprint')
        papers[1]['reference_evidence'][0]['extra']['linked_from_reference_ids'] = [papers[0]['reference_evidence'][0]['reference_id']]
        suggestions = relationship_suggestions(papers)
        self.assertEqual(suggestions[0]['suggested_relation'], 'possible_preprint_of')
        self.assertEqual(suggestions[0]['review_status'], 'candidate')
        self.assertEqual(len(papers), 2)

    def test_same_title_different_formal_year_is_version_warning(self):
        papers = dataset(2)['papers']
        papers[1].update(title=papers[0]['title'], year=2022)
        suggestions = relationship_suggestions(papers)
        self.assertEqual(suggestions[0]['suggested_relation'], 'distinct_publication_versions_check')

    def test_prepare_creates_annotations_and_preserves_snapshot(self):
        value = dataset()
        inv = {'bibliography_sources': [{'source_id': 'independent_directory',
                                       'url': 'https://publisher.example.edu/directory', 'fetch_status': 'html_observed'}]}
        with tempfile.TemporaryDirectory() as temp:
            summary = prepare_review(value, 'test_professor', inv, {'possible_omissions': []}, Path(temp)/'pilot')
            self.assertEqual(summary['candidates'], 1)
            self.assertTrue((Path(temp)/'pilot/checklist.csv').exists())
            self.assertFalse(json.loads((Path(temp)/'pilot/coverage_review.template.json').read_text())['approved'])
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                prepare_review(value, 'test_professor', inv, {}, Path(temp)/'pilot')


class HumanExportTests(unittest.TestCase):
    def test_pending_candidates_export_zero_labels_and_block_formal_metrics(self):
        value = dataset()
        pending = export_truth(value, [], {'approved': False})
        self.assertEqual(pending['papers'], [])
        self.assertFalse(pending['complete_positive_list'])
        report = evaluate_pilot({'papers': []}, pending)
        self.assertIsNone(report['formal_metrics'])

    def test_partial_confirmation_not_complete(self):
        value = dataset(2)
        pending = export_truth(value, review_rows(value)[:1], {'approved': False})
        self.assertEqual(len(pending['papers']), 1)
        self.assertFalse(pending['metadata']['formal_evaluation_ready'])

    def test_completeness_cannot_be_approved_with_pending_candidate(self):
        value = dataset(2)
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            export_truth(value, review_rows(value)[:1], coverage())

    def test_omitted_source_or_omission_checks_block_completeness(self):
        value = dataset()
        for requirements in ({'source_ids': ['another_source']}, {'omission_ids': ['missing_work']}):
            value['review_requirements'] = requirements
            with self.assertRaisesRegex(ValueError, 'omitted'):
                export_truth(value, review_rows(value), coverage())

    def test_possible_real_omission_blocks_completion(self):
        value, checks = dataset(), coverage()
        checks['omissions'] = [{'omission_id': 'missing', 'decision': 'pending'}]
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            export_truth(value, review_rows(value), checks)

    def test_canonical_duplicates_group_only_after_human_confirmation(self):
        value = dataset(2)
        value['papers'][1]['doi'] = value['papers'][0]['doi']
        rows = review_rows(value)
        rows[1]['canonical_group_id'] = rows[0]['canonical_group_id']
        output = export_truth(value, rows, coverage())
        self.assertEqual(len(output['papers']), 1)
        self.assertEqual(len(output['papers'][0]['source_records']), 2)
        self.assertEqual(len(output['papers'][0]['candidate_ids']), 2)

    def test_conflicting_dois_cannot_be_merged(self):
        value = dataset(2)
        rows = review_rows(value)
        rows[1]['canonical_group_id'] = rows[0]['canonical_group_id']
        with self.assertRaisesRegex(ValueError, 'Conflicting DOI'):
            export_truth(value, rows, coverage())

    def test_owned_core_publication_cannot_silently_disappear(self):
        value = dataset()
        rows = review_rows(value)
        rows[0]['inclusion_decision'] = 'exclude'
        with self.assertRaisesRegex(ValueError, 'excluded'):
            export_truth(value, rows, coverage())

    def test_excluded_duplicate_retains_source_id_in_canonical_identity(self):
        value = dataset(2)
        value['papers'][1]['doi'] = value['papers'][0]['doi']
        rows = review_rows(value)
        rows[1].update(canonical_group_id=rows[0]['canonical_group_id'], inclusion_decision='exclude')
        output = export_truth(value, rows, coverage())
        self.assertEqual(len(output['papers']), 1)
        self.assertEqual(len(output['papers'][0]['source_records']), 2)

    def test_relationship_csv_translates_only_explicit_human_preprint_approval(self):
        value = dataset(2)
        value['papers'][1].update(publication_type='preprint', year=2019, doi=None)
        rows = review_rows(value)
        rows[1]['inclusion_decision'] = 'exclude'
        relation = {'candidate_ids': json.dumps([paper['candidate_id'] for paper in value['papers']]),
                    'human_relation': 'preprint_of', 'reviewer': coverage()['reviewer'],
                    'reviewed_at': coverage()['reviewed_at'], 'evidence_urls': json.dumps(coverage()['evidence_urls'])}
        output = export_truth(value, rows, coverage(), relationships=[relation])
        self.assertEqual(len(output['preprint_relations']), 1)
        relation['human_relation'] = ''
        self.assertEqual(export_truth(value, rows, coverage(), relationships=[relation])['preprint_relations'], [])

    def test_alias_requires_explicit_human_identity_and_year_explanation(self):
        value = dataset()
        entry = {'evaluation_id': 'test_professor:work0', 'metadata': {'title': 'Alternate title', 'authors': ['Pat Researcher', 'Sam Scientist'], 'year': 2020},
                 'reviewer': 'Synthetic human', 'reviewed_at': '2026-10-11T18:00:00Z',
                 'evidence_urls': coverage()['evidence_urls'], 'same_publication': True}
        with self.assertRaisesRegex(ValueError, 'year alias'):
            export_truth(value, review_rows(value), coverage(), {'metadata_aliases': [entry]})
        entry['year_difference_reason'] = 'Checked online versus issue publication dates'
        output = export_truth(value, review_rows(value), coverage(), {'metadata_aliases': [entry]})
        match = GroundTruthMatcher(GroundTruth.from_dict(output)).match(entry['metadata'])
        self.assertEqual(match.evaluation_id, 'test_professor:work0')


class PilotEvaluationTests(unittest.TestCase):
    def test_legacy_performance_does_not_bypass_independent_result_audit(self):
        reference = truth()
        with self.assertRaisesRegex(ValueError, 'independent result audit'):
            PerformanceEvaluator().evaluate(result(reference), reference)

    def test_legacy_cli_does_not_bypass_pilot_review_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'truth.json').write_text(json.dumps(export_truth(dataset(), [], {'approved': False})), encoding='utf-8')
            (root/'result.json').write_text(json.dumps({'papers': []}), encoding='utf-8')
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                evaluation_main(['--result', str(root/'result.json'), '--ground-truth', str(root/'truth.json')])

    def test_offline_export_and_evaluation_cli_round_trip(self):
        value = dataset()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'candidates.json').write_text(json.dumps(value), encoding='utf-8')
            rows = review_rows(value)
            with (root/'annotations.csv').open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            (root/'coverage.json').write_text(json.dumps(coverage()), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                export_main(['--candidates', str(root/'candidates.json'), '--annotations', str(root/'annotations.csv'),
                             '--coverage', str(root/'coverage.json'), '--output', str(root/'truth.json')])
                reference = json.loads((root/'truth.json').read_text())
                (root/'result.json').write_text(json.dumps(result(reference)), encoding='utf-8')
                pilot_main(['--result', str(root/'result.json'), '--truth', str(root/'truth.json'),
                            '--audit-output', str(root/'audit.json'), '--output', str(root/'evaluation.json')])
            report = json.loads((root/'evaluation.json').read_text())
            self.assertEqual(report['formal_metrics']['ownership_f1'], 1)
            self.assertEqual(json.loads((root/'audit.json').read_text())['papers'], [])

    def test_metrics_separate_discovery_and_verified_recall(self):
        reference = truth(3)
        saved = result(reference, ['verified', 'needs_review', 'rejected'])
        report = evaluate_pilot(saved, reference)
        metrics = report['formal_metrics']
        self.assertEqual(metrics['paper_discovery_recall'], 1)
        self.assertEqual(metrics['ownership_precision'], 1)
        self.assertAlmostEqual(metrics['ownership_recall'], 1/3)
        self.assertAlmostEqual(metrics['ownership_f1'], 0.5)

    def test_duplicates_do_not_inflate_true_positives(self):
        reference = truth()
        saved = result(reference)
        saved['papers'].append(deepcopy(saved['papers'][0]))
        self.assertEqual(evaluate_pilot(saved, reference)['formal_metrics']['verified_true_positives'], 1)

    def test_unmatched_records_are_not_automatic_false_positives(self):
        reference = truth()
        saved = {'papers': [{'title': 'An unjudged record', 'year': 2021, 'authors': ['Other Author'], 'ownership_status': 'verified'}]}
        report = evaluate_pilot(saved, reference)
        self.assertIsNone(report['formal_metrics'])
        audit = prepare_audit(saved, reference)
        self.assertNotIn('ownership_status', audit['papers'][0]['paper'])
        self.assertEqual(audit['papers'][0]['decision'], 'uncertain')

    def test_independent_negative_audit_yields_correct_precision_f1(self):
        reference = truth(2)
        saved = result(reference, ['verified', 'needs_review'])
        saved['papers'].append({'title': 'A different identity', 'year': 2021, 'authors': ['Other Author'], 'ownership_status': 'verified'})
        audit = annotate_audit(prepare_audit(saved, reference))
        metrics = evaluate_pilot(saved, reference, audit)['formal_metrics']
        self.assertEqual(metrics['paper_discovery_recall'], 1)
        self.assertEqual(metrics['ownership_precision'], .5)
        self.assertEqual(metrics['ownership_recall'], .5)
        self.assertEqual(metrics['ownership_f1'], .5)

    def test_stale_or_mutated_audit_rejected(self):
        reference = truth()
        saved = {'papers': [{'title': 'Unmatched', 'year': 2021}]}
        audit = prepare_audit(saved, reference)
        audit['papers'][0]['paper']['title'] = 'Changed'
        with self.assertRaisesRegex(ValueError, 'immutable'):
            evaluate_pilot(saved, reference, audit)
        audit = prepare_audit(saved, reference)
        audit['truth_sha256'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'stale'):
            evaluate_pilot(saved, reference, audit)

    def test_new_true_paper_requires_independent_reference_revision(self):
        reference = truth()
        saved = {'papers': [{'title': 'Missing positive', 'year': 2021}]}
        audit = annotate_audit(prepare_audit(saved, reference), 'belongs')
        with self.assertRaisesRegex(ValueError, 'missing from Ground Truth'):
            evaluate_pilot(saved, reference, audit)

    def test_preprint_same_title_year_doi_is_not_formal_discovery(self):
        reference = truth()
        saved = result(reference)
        saved['papers'][0]['paper_type'] = 'preprint'
        audit = annotate_audit(prepare_audit(saved, reference), 'out_of_scope')
        metrics = evaluate_pilot(saved, reference, audit)['formal_metrics']
        self.assertEqual(metrics['paper_discovery_recall'], 0)
        self.assertIsNone(metrics['ownership_precision'])

    def test_preprint_cannot_be_approved_as_formal_true_positive(self):
        reference = truth()
        saved = result(reference)
        saved['papers'][0]['paper_type'] = 'preprint'
        audit = annotate_audit(prepare_audit(saved, reference), 'belongs')
        audit['papers'][0]['evaluation_id'] = reference['papers'][0]['evaluation_id']
        with self.assertRaisesRegex(ValueError, 'formal publication discovery'):
            evaluate_pilot(saved, reference, audit)

    def test_conflicting_doi_stays_unresolved(self):
        reference = truth()
        saved = result(reference)
        saved['papers'][0]['doi'] = '10.1234/conflicting_version'
        audit = annotate_audit(prepare_audit(saved, reference), 'belongs')
        audit['papers'][0]['evaluation_id'] = reference['papers'][0]['evaluation_id']
        with self.assertRaisesRegex(ValueError, 'Identifier/version conflict'):
            evaluate_pilot(saved, reference, audit)

    def test_metadata_without_version_type_or_venue_requires_review(self):
        reference = truth()
        saved = result(reference)
        for field in ('doi', 'venue', 'publication_type'):
            saved['papers'][0].pop(field)
        audit = prepare_audit(saved, reference)
        self.assertEqual(audit['papers'][0]['matching_evidence'][0]['decision'], 'version_unresolved')
        self.assertIsNone(evaluate_pilot(saved, reference)['formal_metrics'])

    def test_approved_preprint_relation_keeps_distinct_identity(self):
        value = dataset(2)
        preprint = value['papers'][1]
        preprint.update(publication_type='preprint', year=2019, doi=None, external_ids={'arxiv': 'synthetic-preprint'})
        rows = review_rows(value)
        rows[1]['inclusion_decision'] = 'exclude'
        relation = {'formal_evaluation_id': 'test_professor:work0', 'preprint_candidate_id': preprint['candidate_id'],
                    'reviewer': coverage()['reviewer'], 'reviewed_at': coverage()['reviewed_at'], 'evidence_urls': coverage()['evidence_urls']}
        reference = export_truth(value, rows, coverage(), {'preprint_relations': [relation]})
        matcher = GroundTruthMatcher(GroundTruth.from_dict(reference))
        match = match_publication({'source': 'arxiv', 'external_ids': {'arxiv': 'synthetic-preprint'}}, reference, matcher)
        self.assertEqual(match.decision, 'related_preprint')
        self.assertIsNone(match.evaluation_id)
        self.assertEqual(len(reference['papers']), 1)


if __name__ == '__main__':
    unittest.main()
