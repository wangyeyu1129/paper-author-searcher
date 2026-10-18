"""Synthetic annotations only; no real professor ground truth is invented."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from paper_author_searcher.evaluation import Evaluator
from paper_author_searcher.evaluation_cli import main
from paper_author_searcher.evaluation_matching import GroundTruth, GroundTruthMatcher


def truth_paper(evaluation_id, label='belongs', doi=None):
    return {
        'evaluation_id': evaluation_id,
        'ownership': label,
        'doi': doi or '10.example/' + evaluation_id,
        'title': 'Synthetic study ' + evaluation_id,
        'year': 2024,
        'authors': ['Alex Morgan', 'Jane Smith'],
        'annotation_evidence': ['Synthetic fixture; not real human annotation'],
    }


def truth(papers, complete=False, professor=None):
    data = {'schema_version': 1, 'complete_positive_list': complete, 'papers': papers}
    if professor:
        data['professor'] = professor
    return data


def record(evaluation_id, source='openalex'):
    return {
        'doi': '10.example/' + evaluation_id,
        'title': 'Synthetic study ' + evaluation_id,
        'year': 2024,
        'authors': ['Alex Morgan', 'Jane Smith'],
        'source': source,
        'source_paper_id': source + ':' + evaluation_id,
        'source_author_id': 'synthetic-author',
        'ownership_evidence': ['Synthetic source evidence'],
    }


def entry(evaluation_id, status='verified', records=None):
    paper = record(evaluation_id)
    paper['ownership_status'] = status
    paper['records'] = records if records is not None else [record(evaluation_id)]
    return paper


def saved(papers):
    return {'collection': {'papers': papers, 'complete': True, 'sources': []}}


class OwnershipMetricTests(unittest.TestCase):
    def test_known_precision_recall_f1_and_candidate_recall(self):
        annotations = truth([truth_paper('a'), truth_paper('b'), truth_paper('c'), truth_paper('n', 'not_belongs')])
        result = saved([entry('a'), entry('b', 'needs_review'), entry('n')])
        report = Evaluator().evaluate(result, annotations)
        self.assertEqual(report['ownership']['true_positives'], 1)
        self.assertEqual(report['ownership']['false_positives'], 1)
        self.assertAlmostEqual(report['ownership']['precision'], 0.5)
        self.assertAlmostEqual(report['ownership']['recall'], 1 / 3)
        self.assertAlmostEqual(report['ownership']['f1'], 0.4)
        self.assertAlmostEqual(report['all_candidates']['recall'], 2 / 3)
        self.assertEqual(len(report['false_positives']), 1)
        self.assertEqual(len(report['false_negatives']['all_candidates']), 1)
        self.assertEqual(len(report['false_negatives']['verified']), 2)
        fn = next(item for item in report['false_negatives']['verified'] if item['evaluation_id'] == 'b')
        self.assertEqual(fn['reason'], 'Retrieved but not classified verified')
        self.assertEqual(fn['retrieved_entries'][0]['ownership_status'], 'needs_review')

    def test_status_statistics_and_correct_rejection(self):
        annotations = truth([truth_paper('a'), truth_paper('b'), truth_paper('n', 'not_belongs')])
        report = Evaluator().evaluate(
            saved([entry('a', 'needs_review'), entry('b', 'rejected'), entry('n', 'rejected')]), annotations
        )
        self.assertEqual(report['by_status']['rejected']['entry_count'], 2)
        self.assertEqual(report['rejection_precision'], 0.5)
        self.assertEqual(report['ownership']['recall'], 0)
        self.assertEqual(report['all_candidates']['recall'], 1)

    def test_incomplete_list_unmatched_is_unjudged_not_false_positive(self):
        report = Evaluator().evaluate(saved([entry('unknown')]), truth([truth_paper('a')]))
        self.assertEqual(report['ownership']['false_positives'], 0)
        self.assertIsNone(report['ownership']['precision'])
        self.assertEqual(report['judgment_coverage']['unjudged_entries'], 1)
        self.assertEqual(len(report['manual_confirmation']), 1)

    def test_complete_positive_list_counts_identifiable_unmatched_negative(self):
        report = Evaluator().evaluate(saved([entry('a'), entry('unknown')]), truth([truth_paper('a')], complete=True))
        self.assertEqual(report['ownership']['false_positives'], 1)
        self.assertEqual(report['ownership']['precision'], 0.5)
        self.assertEqual(len(report['false_positives']), 1)

    def test_uncertain_ground_truth_is_not_scored(self):
        report = Evaluator().evaluate(saved([entry('a')]), truth([truth_paper('a', 'uncertain')], complete=True))
        self.assertEqual(report['ownership']['false_positives'], 0)
        self.assertEqual(report['judgment_coverage']['unjudged_entries'], 1)

    def test_duplicate_entries_do_not_inflate_recall_or_precision(self):
        report = Evaluator().evaluate(saved([entry('a'), entry('a')]), truth([truth_paper('a')]))
        self.assertEqual(report['ownership']['true_positives'], 1)
        self.assertEqual(report['ownership']['recall'], 1)
        self.assertEqual(report['by_status']['verified']['entry_count'], 2)

    def test_empty_denominators_are_null(self):
        report = Evaluator().evaluate(saved([]), truth([]))
        for metric in ('precision', 'recall', 'f1'):
            self.assertIsNone(report['ownership'][metric])
        self.assertIsNone(report['deduplication']['pairwise_precision'])

    def test_filtered_output_evaluates_archive(self):
        result = saved([entry('a')])
        result['collection']['all_papers'] = [entry('a'), entry('b', 'needs_review')]
        report = Evaluator().evaluate(result, truth([truth_paper('a'), truth_paper('b')]))
        self.assertEqual(report['all_candidates']['recall'], 1)
        self.assertEqual(report['ownership']['recall'], 0.5)

    def test_conflicting_statuses_are_explicitly_reported(self):
        report = Evaluator().evaluate(saved([entry('a'), entry('a', 'rejected')]), truth([truth_paper('a')]))
        self.assertEqual(report['ownership']['true_positives'], 1)
        self.assertEqual(len(report['status_conflicts']), 1)


class EvaluationMatchingTests(unittest.TestCase):
    def matcher(self, papers):
        return GroundTruthMatcher(GroundTruth.from_dict(truth(papers)))

    def test_doi_case_and_url_normalization(self):
        candidate = record('a')
        candidate['doi'] = 'https://doi.org/10.example/A'
        self.assertEqual(self.matcher([truth_paper('a')]).match(candidate).evaluation_id, 'a')

    def test_missing_doi_matches_exact_metadata(self):
        candidate = record('a')
        candidate['doi'] = None
        match = self.matcher([truth_paper('a')]).match(candidate)
        self.assertEqual(match.evaluation_id, 'a')
        self.assertEqual(match.decision, 'metadata')

    def test_human_supplied_name_alias_is_supported_without_fuzzy_rules(self):
        annotation = truth_paper('a')
        annotation['metadata_aliases'] = [
            {'title': annotation['title'], 'year': 2024, 'authors': ['A. Morgan', 'Jane Smith']}
        ]
        candidate = record('a')
        candidate['doi'] = None
        candidate['authors'] = ['A. Morgan', 'Jane Smith']
        self.assertEqual(self.matcher([annotation]).match(candidate).evaluation_id, 'a')

    def test_external_and_native_source_identifiers(self):
        annotation = truth_paper('a')
        annotation['doi'] = None
        annotation['source_records'] = [{'source': 'dblp', 'paper_id': 'journal/a'}]
        candidate = record('a')
        candidate['doi'] = None
        candidate['title'] = 'Different supplied title'
        candidate['external_ids'] = {'DBLP': 'journal/a'}
        self.assertEqual(self.matcher([annotation]).match(candidate).evaluation_id, 'a')

    def test_different_versions_with_identical_metadata_need_confirmation(self):
        first = truth_paper('a')
        second = truth_paper('b')
        second['title'] = first['title']
        candidate = record('a')
        candidate['doi'] = None
        match = self.matcher([first, second]).match(candidate)
        self.assertIsNone(match.evaluation_id)
        self.assertEqual(match.decision, 'ambiguous')

    def test_explicit_venue_conflict_is_not_a_metadata_match(self):
        annotation = truth_paper('a')
        annotation['venue'] = 'Journal One'
        candidate = record('a')
        candidate.update({'doi': None, 'venue': 'Journal Two'})
        self.assertIsNone(self.matcher([annotation]).match(candidate).evaluation_id)
        annotation['metadata_aliases'] = [
            {
                'title': annotation['title'],
                'year': 2024,
                'authors': annotation['authors'],
                'venue': 'Journal Two',
            }
        ]
        self.assertEqual(self.matcher([annotation]).match(candidate).evaluation_id, 'a')

    def test_conflicting_doi_is_not_resolved_by_title(self):
        candidate = record('a')
        candidate['doi'] = '10.example/other-version'
        match = self.matcher([truth_paper('a')]).match(candidate)
        self.assertEqual(match.decision, 'identifier_conflict')
        self.assertIsNone(match.evaluation_id)

    def test_stable_identifiers_pointing_to_different_truths_are_ambiguous(self):
        annotation = truth_paper('b')
        annotation['external_ids'] = {'arxiv': 'synthetic-b'}
        candidate = record('a')
        candidate['external_ids'] = {'arxiv': 'synthetic-b'}
        self.assertEqual(self.matcher([truth_paper('a'), annotation]).match(candidate).decision, 'ambiguous')

    def test_invalid_annotations_rejected(self):
        cases = [
            truth([truth_paper('a'), truth_paper('a')]),
            truth([dict(truth_paper('a'), ownership='automatic')]),
            truth([dict(truth_paper('a'), dois='not-a-list')]),
            truth([dict(truth_paper('a'), authors='not-a-list')]),
            truth([], professor={'author_ids': ['invalid']}),
            dict(truth([]), metadata=[]),
            [],
        ]
        for annotation in cases:
            with self.subTest(annotation=annotation):
                with self.assertRaises(ValueError):
                    GroundTruth.from_dict(annotation)

    def test_source_ids_bridge_external_ids_for_any_provider(self):
        annotation = truth_paper('a')
        annotation['source_records'] = [{'source': 'new_provider', 'paper_id': 'work-123'}]
        candidate = {'external_ids': {'new_provider': 'work-123'}}
        self.assertEqual(self.matcher([annotation]).match(candidate).evaluation_id, 'a')

    def test_empty_template_contains_no_annotations(self):
        template = Path(__file__).resolve().parents[1] / 'schemas' / 'ground_truth.template.json'
        annotations = GroundTruth.from_dict(json.loads(template.read_text(encoding='utf-8')))
        self.assertEqual(annotations.papers, [])
        self.assertFalse(annotations.complete_positive_list)


class DeduplicationMetricTests(unittest.TestCase):
    def test_pairwise_counts_for_wrong_merge_and_missed_duplicate(self):
        predictions = [
            entry('a', records=[record('a'), record('a', 'semantic_scholar')]),
            entry('a', records=[record('a', 'dblp'), record('b')]),
        ]
        report = Evaluator().evaluate(saved(predictions), truth([truth_paper('a'), truth_paper('b')]))
        dedup = report['deduplication']
        self.assertEqual(dedup['true_duplicate_pairs_merged'], 1)
        self.assertEqual(dedup['different_publication_pairs_merged'], 1)
        self.assertEqual(dedup['true_duplicate_pairs_missed'], 2)
        self.assertEqual(dedup['pairwise_precision'], 0.5)
        self.assertAlmostEqual(dedup['pairwise_recall'], 1 / 3)
        self.assertAlmostEqual(dedup['pairwise_f1'], 0.4)
        self.assertEqual(len(dedup['incorrect_merges']), 1)
        self.assertEqual(len(dedup['missed_duplicates']), 1)

    def test_unknown_records_not_assumed_correctly_merged(self):
        report = Evaluator().evaluate(
            saved([entry('a', records=[record('a'), record('unknown')])]), truth([truth_paper('a')])
        )
        self.assertEqual(report['deduplication']['annotation_coverage'], 0.5)
        self.assertEqual(report['deduplication']['true_duplicate_pairs_merged'], 0)


class ResolverAndCLITests(unittest.TestCase):
    def test_resolver_only_does_not_claim_publication_false_negatives(self):
        result = {
            'status': 'resolved',
            'author_ids': {'openalex': 'a'},
            'candidates': [{'author_ids': {'openalex': 'a'}}, {'author_ids': {'openalex': 'b'}}],
        }
        annotation = truth(
            [truth_paper('a')],
            professor={
                'author_ids': {'openalex': ['a']},
                'author_ids_complete': True,
            },
        )
        report = Evaluator().evaluate(result, annotation)
        self.assertEqual(report['professor_resolver']['author_id_precision'], 1)
        self.assertEqual(report['professor_resolver']['candidate_author_id_recall'], 1)
        self.assertTrue(report['professor_resolver']['auto_accepted_correct'])
        self.assertIsNone(report['ownership'])
        self.assertIsNone(report['all_candidates'])
        self.assertFalse(report['retrieval']['available'])

    def test_partial_professor_annotations_do_not_mark_extra_id_wrong(self):
        result = {'status': 'resolved', 'author_ids': {'openalex': 'a', 'new_provider': 'unknown'}}
        report = Evaluator().evaluate(result, truth([], professor={'author_ids': {'openalex': ['a']}}))
        identity = report['professor_resolver']
        self.assertEqual(identity['author_id_recall'], 1)
        self.assertIsNone(identity['author_id_precision'])
        self.assertIsNone(identity['top_identity_confirmed'])

    def test_source_failure_and_unparsed_records_are_retained(self):
        result = saved([entry('a')])
        result['collection'].update(
            {
                'complete': False,
                'sources': [
                    {
                        'source': 'new_provider',
                        'complete': False,
                        'pages_fetched': 1,
                        'error': 'Synthetic pagination failure',
                        'rejected_records': [{'raw_metadata': {'id': 'unparsed-work'}}],
                    }
                ],
            }
        )
        report = Evaluator().evaluate(result, truth([truth_paper('a'), truth_paper('b')]))
        self.assertEqual(report['all_candidates']['recall'], 0.5)
        self.assertFalse(report['retrieval']['complete'])
        self.assertEqual(report['retrieval']['unparsed_records'], 1)
        self.assertEqual(report['retrieval']['sources'], result['collection']['sources'])

    def test_professor_id_and_orcid_conflict(self):
        result = saved([])
        result.update({'status': 'resolved', 'author_ids': {'openalex': 'a'}, 'orcid': 'wrong'})
        annotation = truth(
            [], professor={'author_ids': {'openalex': ['a']}, 'author_ids_complete': True, 'orcid': 'expected'}
        )
        report = Evaluator().evaluate(result, annotation)['professor_resolver']
        self.assertEqual(report['author_id_precision'], 1)
        self.assertFalse(report['top_identity_confirmed'])
        self.assertFalse(report['auto_accepted_correct'])

    def test_cli_uses_only_files_and_saves_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / 'result.json'
            gold = root / 'truth.json'
            output = root / 'evaluation.json'
            result.write_text(json.dumps(saved([entry('a')])), encoding='utf-8')
            gold.write_text(json.dumps(truth([truth_paper('a')])), encoding='utf-8')
            with patch('paper_author_searcher.http.urlopen', side_effect=AssertionError('Must remain offline')):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        main(['--result', str(result), '--ground-truth', str(gold), '--output', str(output)]), 0
                    )
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['ownership']['precision'], 1)
            self.assertEqual(report['ownership']['recall'], 1)
            self.assertIn('false_negatives', report)
