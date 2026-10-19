"""Benchmark harness tests with synthetic people and bibliographies only; nothing touches the network."""

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from paper_author_searcher.benchmark.cli import main as benchmark_main
from paper_author_searcher.benchmark.cli import score_run
from paper_author_searcher.benchmark.gate import GateStatus, ReleaseGate
from paper_author_searcher.benchmark.metrics import benchmark_metrics
from paper_author_searcher.benchmark.review import export_review, import_review
from paper_author_searcher.benchmark.roster import Roster
from paper_author_searcher.benchmark.runner import BenchmarkRunner, WorkflowSearchSystem, window_archive
from paper_author_searcher.benchmark.scoring import PersonScorer, aggregate, truth_paths
from paper_author_searcher.benchmark.settings import Aggregation, BenchmarkSettings, GateRule, RunSettings
from paper_author_searcher.benchmark.snapshots import ARXIV_SNAPSHOT, CROSSREF_SNAPSHOT
from paper_author_searcher.benchmark.truth_builder import TruthBuilder
from paper_author_searcher.benchmark.truth_sources import (
    bibtex_entries,
    default_registry,
    latex_text,
    vancouver_name,
)
from paper_author_searcher.jsonio import write_json

DIMENSIONS = {'seniority': ['senior', 'junior'], 'name_profile': ['common', 'other']}


def config(minimum_people=1, require_review=True, rules=None):
    return {
        'benchmark_schema_version': 1,
        'dataset_id': 'synthetic_v1',
        'year_window': {'start': 2020, 'end': 2025},
        'paths': {
            'roster': 'roster.json',
            'snapshots': 'snapshots',
            'truth': 'truth',
            'runs': 'runs',
            'reports': 'reports',
        },
        'dimensions': DIMENSIONS,
        'publication_types': {
            'included': ['journal_article', 'conference_paper', 'preprint'],
            'excluded': ['poster', 'thesis'],
            'unknown_included': True,
            'non_publication_output': ['dataset'],
        },
        'reported_metrics': [
            'identity_resolution_rate',
            'identity_precision',
            'verified_paper_recall',
            'paper_discovery_recall',
            'author_match_precision',
            'actual_download_coverage',
        ],
        'gate': {
            'aggregation': 'macro',
            'minimum_people_per_stratum': minimum_people,
            'require_human_review': require_review,
            'rules': rules
            or [
                {'metric': 'verified_paper_recall', 'minimum': 0.8},
                {'metric': 'author_match_precision', 'minimum': 0.95},
            ],
        },
        'run': {'mode': 'metadata'},
    }


def person(person_id, name='Alex Morgan', seniority='senior', profile='other', sources=None, **extra):
    data = {
        'person_id': person_id,
        'search_input': {'name': name, 'university': 'Synthetic University'},
        'strata': {'seniority': seniority, 'name_profile': profile},
        'truth_sources': sources or [{'kind': 'manual', 'locator': 'list.json', 'url': 'https://example.org/'}],
    }
    data.update(extra)
    return data


def manual(entries):
    return {
        'snapshot_schema_version': 1,
        'source_url': 'https://example.org/',
        'captured_at': '2026-01-01',
        'entries': entries,
    }


def paper(title, year=2024, authors=('Alex Morgan', 'Jane Smith'), **extra):
    data = {'title': title, 'year': year, 'authors': list(authors), 'type': 'journal_article'}
    data.update(extra)
    return data


class Workspace:
    """A temporary dataset directory holding config, roster and snapshots."""

    def __init__(self, people, snapshots, **config_options):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / 'benchmark.json').write_text(json.dumps(config(**config_options)))
        (self.root / 'roster.json').write_text(
            json.dumps({'roster_schema_version': 1, 'dataset_id': 'synthetic_v1', 'people': people})
        )
        for person_id, files in snapshots.items():
            folder = self.root / 'snapshots' / person_id
            folder.mkdir(parents=True)
            for name, content in files.items():
                path = folder / name
                path.write_text(content if isinstance(content, str) else json.dumps(content), encoding='utf-8')
        self.settings = BenchmarkSettings.load(self.root / 'benchmark.json')
        self.roster = Roster.load(self.settings.paths.roster)

    def close(self):
        self.directory.cleanup()

    def build(self, person_id):
        person = self.roster.select([person_id])[0]
        return TruthBuilder(self.settings, default_registry()).build(person)


class SettingsAndRosterTests(unittest.TestCase):
    def test_thresholds_and_paths_come_from_config(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = BenchmarkSettings.from_dict(config(), Path(directory))
        self.assertEqual(
            [(rule.metric, rule.minimum) for rule in settings.gate_rules],
            [('verified_paper_recall', 0.8), ('author_match_precision', 0.95)],
        )
        self.assertEqual(settings.paths.truth, (Path(directory) / 'truth').resolve())

    def test_gate_metric_must_be_reported(self):
        bad = config(rules=[{'metric': 'f1_unknown', 'minimum': 0.5}])
        with self.assertRaises(ValueError):
            BenchmarkSettings.from_dict(bad, Path('.'))

    def test_threshold_must_be_a_rate(self):
        with self.assertRaises(ValueError):
            BenchmarkSettings.from_dict(config(rules=[{'metric': 'verified_paper_recall', 'minimum': 80}]), Path('.'))

    def test_rule_aggregation_and_truth_floor_come_from_config(self):
        data = config(rules=[{'metric': 'verified_paper_recall', 'minimum': 0.8, 'aggregation': 'micro'}])
        data['gate']['minimum_truth_positives'] = {'verified_paper_recall': 3}
        settings = BenchmarkSettings.from_dict(data, Path('.'))
        self.assertEqual(settings.rule_aggregation(settings.gate_rules[0]).value, 'micro')
        self.assertEqual(settings.minimum_truth_positives, {'verified_paper_recall': 3})

    def test_identity_share_comes_from_config_and_is_a_rate(self):
        data = config()
        data['identity'] = {'minimum_truth_share': 0.5}
        settings = BenchmarkSettings.from_dict(data, Path('.'))
        self.assertEqual(benchmark_metrics(settings)['identity_precision'].minimum_truth_share, 0.5)
        data['identity'] = {'minimum_truth_share': 0}
        with self.assertRaises(ValueError):
            benchmark_metrics(BenchmarkSettings.from_dict(data, Path('.')))

    def test_truth_floor_must_name_reported_metrics(self):
        data = config()
        data['gate']['minimum_truth_positives'] = {'f1_unknown': 3}
        with self.assertRaises(ValueError):
            BenchmarkSettings.from_dict(data, Path('.'))

    def test_roster_rejects_unknown_stratum_and_duplicates(self):
        workspace = Workspace([person('a_one'), person('a_one')], {})
        try:
            with self.assertRaises(ValueError):
                workspace.roster.validate(workspace.settings, default_registry().kinds())
        finally:
            workspace.close()
        workspace = Workspace([person('a_one', seniority='emeritus')], {})
        try:
            with self.assertRaises(ValueError):
                workspace.roster.validate(workspace.settings, default_registry().kinds())
        finally:
            workspace.close()

    def test_small_strata_are_reported_as_warnings(self):
        workspace = Workspace([person('a_one')], {}, minimum_people=2)
        try:
            warnings = workspace.roster.validate(workspace.settings, default_registry().kinds())
        finally:
            workspace.close()
        self.assertTrue(any('seniority=senior has 1 people' in warning for warning in warnings))
        self.assertTrue(any('seniority=junior has 0 people' in warning for warning in warnings))


class SourceParsingTests(unittest.TestCase):
    def test_bibtex_fields_latex_and_arxiv(self):
        text = r"""
        @comment{ignored}
        @inproceedings{a,
          title = {Mission: {I}mpossible M\"odels},
          author = {Morgan, Alex and Jane Smith},
          booktitle = "Proceedings of ACL",
          year = 2024,
          doi = {https://doi.org/10.1001/ABC},
        }
        @misc{b, title={Preprint Study}, author={Alex Morgan}, year={2025}, eprint={2501.01234}, archivePrefix={arXiv}}
        """
        entries = bibtex_entries(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(latex_text(entries[0]['title']), 'Mission: Impossible Models')
        self.assertEqual(entries[0]['year'], '2024')
        self.assertEqual(entries[1]['eprint'], '2501.01234')

    def test_bibtex_source_types_and_identifiers(self):
        bib = (
            '@article{a, title={Journal Study}, author={Alex Morgan and Jane Smith}, journal={Nature}, year={2023},'
            ' doi={10.1001/J}}\n@article{b, title={Arxiv Study}, author={Alex Morgan}, journal={arXiv preprint '
            'arXiv:2301.00001}, year={2023}}'
        )
        workspace = Workspace(
            [person('a_one', sources=[{'kind': 'bibtex', 'locator': 'p.bib'}])], {'a_one': {'p.bib': bib}}
        )
        try:
            truth = workspace.build('a_one')
        finally:
            workspace.close()
        by_title = {entry['title']: entry for entry in truth['papers']}
        self.assertEqual(by_title['Journal Study']['dois'], ['10.1001/j'])
        self.assertEqual(by_title['Arxiv Study']['external_ids'], {'arxiv': '2301.00001'})
        self.assertEqual(by_title['Arxiv Study']['work_types'], ['preprint'])

    def test_vancouver_names(self):
        self.assertEqual(vancouver_name('Trinidad MI'), 'M. I. Trinidad')
        self.assertEqual(vancouver_name('Al-Sayyad N'), 'N. Al-Sayyad')
        self.assertEqual(vancouver_name('Huan Zhang'), 'Huan Zhang')

    def test_orcid_snapshot_uses_self_identifiers_only(self):
        work = {
            'put-code': 7,
            'title': {'title': {'value': 'Orcid Study'}},
            'type': 'journal-article',
            'publication-date': {'year': {'value': '2022'}},
            'external-ids': {
                'external-id': [
                    {
                        'external-id-type': 'doi',
                        'external-id-value': '10.1002/SELF',
                        'external-id-relationship': 'self',
                    },
                    {
                        'external-id-type': 'issn',
                        'external-id-value': '1234-5678',
                        'external-id-relationship': 'part-of',
                    },
                    {'external-id-type': 'pmid', 'external-id-value': '123', 'external-id-relationship': 'self'},
                ]
            },
            'contributors': {'contributor': [{'credit-name': {'value': 'Alex Morgan'}}]},
        }
        workspace = Workspace(
            [person('a_one', sources=[{'kind': 'orcid', 'locator': '0000-0000-0000-0001'}])],
            {'a_one': {'orcid-0000-0000-0000-0001.json': {'works': [work]}}},
        )
        try:
            truth = workspace.build('a_one')
        finally:
            workspace.close()
        entry = truth['papers'][0]
        self.assertEqual(entry['dois'], ['10.1002/self'])
        self.assertEqual(entry['external_ids'], {'pubmed': '123', 'pmid': 'https://pubmed.ncbi.nlm.nih.gov/123'})


class TruthBuilderTests(unittest.TestCase):
    def build(self, entries, extra_files=None, **person_options):
        files = {'list.json': manual(entries)}
        files.update(extra_files or {})
        workspace = Workspace([person('a_one', **person_options)], {'a_one': files})
        try:
            return workspace.build('a_one')
        finally:
            workspace.close()

    def test_window_type_and_identifier_filters(self):
        truth = self.build(
            [
                paper('Inside Window Study'),
                paper('Too Old Study', year=2015),
                paper('Poster Study', type='poster'),
                paper('Missing Year Study', year=None),
                {'title': 'Anonymous Team Report', 'year': 2024, 'authors': []},
            ]
        )
        self.assertEqual([entry['title'] for entry in truth['papers']], ['Inside Window Study'])
        self.assertEqual(
            truth['metadata']['excluded_counts'],
            {
                'outside_year_window': 1,
                'excluded_publication_type': 1,
                'missing_year': 1,
                'no_identifier_or_authors': 1,
            },
        )
        self.assertEqual(truth['metadata']['annotation_state'], 'pending_human_review')
        self.assertTrue(all(entry['ownership'] == 'uncertain' for entry in truth['papers']))

    def test_preprint_and_published_versions_group_into_one_publication(self):
        truth = self.build(
            [
                paper('A Grouped Study of Things', year=2023, type='preprint', arxiv='2301.00001'),
                paper('A grouped study of things', year=2024, doi='10.1003/pub'),
                paper('A Different Study of Things', year=2024),
            ]
        )
        self.assertEqual(len(truth['papers']), 2)
        grouped = next(entry for entry in truth['papers'] if 'dois' in entry)
        self.assertEqual(grouped['year'], 2024)
        self.assertEqual(grouped['external_ids'], {'arxiv': '2301.00001'})
        self.assertIn('grouped_2_source_records', grouped['review_flags'])

    def test_listed_venue_never_blocks_matching_and_year_aliases_exist(self):
        truth = self.build([paper('Venue Study', venue='NeurIPS 2024')])
        entry = truth['papers'][0]
        self.assertNotIn('venue', entry)
        self.assertEqual(entry['listed_venue'], 'NeurIPS 2024')
        self.assertEqual(sorted(alias['year'] for alias in entry['metadata_aliases']), [2023, 2025])

    def test_target_missing_from_byline_is_flagged(self):
        truth = self.build([paper('Someone Else Study', authors=['Jane Smith', 'Chris Lee'])])
        entry = truth['papers'][0]
        self.assertEqual(entry['proposed_ownership'], 'uncertain')
        self.assertIn('target_name_not_found_in_byline', entry['review_flags'])

    def test_initialised_byline_counts_as_target(self):
        truth = self.build([paper('Initials Study', authors=['A. Morgan', 'J. Smith'])])
        self.assertEqual(truth['papers'][0]['proposed_ownership'], 'belongs')

    def test_title_lookups_are_accepted_only_on_exact_title_and_family_name(self):
        crossref = {
            'lookups': {
                'lookup study': {
                    'candidates': [
                        {
                            'doi': '10.1009/wrong',
                            'title': 'Lookup Study Revisited',
                            'year': 2024,
                            'family_names': ['Morgan'],
                        },
                        {
                            'doi': '10.1009/right',
                            'title': 'Lookup Study',
                            'year': 2024,
                            'family_names': ['Morgan', 'Smith'],
                        },
                    ]
                },
                'other study': {
                    'candidates': [
                        {'doi': '10.1009/stranger', 'title': 'Other Study', 'year': 2024, 'family_names': ['Nobody']}
                    ]
                },
            }
        }
        arxiv = {
            'lookups': {
                'lookup study': {
                    'candidates': [
                        {'arxiv': '2401.00002', 'title': 'Lookup Study', 'year': 2023, 'authors': ['Alex Morgan']}
                    ]
                }
            }
        }
        truth = self.build(
            [paper('Lookup Study'), paper('Other Study')], {CROSSREF_SNAPSHOT: crossref, ARXIV_SNAPSHOT: arxiv}
        )
        by_title = {entry['title']: entry for entry in truth['papers']}
        self.assertEqual(by_title['Lookup Study']['dois'], ['10.1009/right'])
        self.assertEqual(by_title['Lookup Study']['external_ids'], {'arxiv': '2401.00002'})
        self.assertIn('identifier_from_title_lookup', by_title['Lookup Study']['review_flags'])
        self.assertNotIn('dois', by_title['Other Study'])

    def test_person_window_overrides_default(self):
        truth = self.build(
            [paper('Recent Study', year=2025), paper('Earlier Study', year=2021)], year_start=2024, year_end=2025
        )
        self.assertEqual([entry['title'] for entry in truth['papers']], ['Recent Study'])


class ReviewTests(unittest.TestCase):
    def setUp(self):
        workspace = Workspace(
            [person('a_one')],
            {
                'a_one': {
                    'list.json': manual(
                        [paper('Kept Study', doi='10.1004/kept'), paper('Rejected Study', doi='10.1004/rejected')]
                    )
                }
            },
        )
        self.truth = workspace.build('a_one')
        workspace.close()
        self.directory = tempfile.TemporaryDirectory()
        self.sheet = Path(self.directory.name) / 'review.csv'

    def tearDown(self):
        self.directory.cleanup()

    def decide(self, decisions, added=None):
        export_review(self.truth, self.sheet)
        with self.sheet.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            row['decision'] = decisions.get(row['title'], '')
        rows.extend(added or [])
        with self.sheet.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def test_complete_review_becomes_formal_truth_with_added_paper(self):
        added = {
            'evaluation_id': '',
            'decision': 'belongs',
            'title': 'Missed Study',
            'year': '2023',
            'authors': 'Alex Morgan; Jane Smith',
            'dois': '10.1004/missed',
            'reviewer_note': 'Found on CV',
        }
        self.decide({'Kept Study': 'belongs', 'Rejected Study': 'not_belongs'}, [added])
        reviewed = import_review(
            self.truth, self.sheet, 'Reviewer A', complete_positive_list=True, reviewed_at='2026-02-01'
        )
        labels = {entry['title']: entry['ownership'] for entry in reviewed['papers']}
        self.assertEqual(labels, {'Kept Study': 'belongs', 'Rejected Study': 'not_belongs', 'Missed Study': 'belongs'})
        self.assertEqual(reviewed['metadata']['annotation_state'], 'human_reviewed')
        self.assertTrue(reviewed['complete_positive_list'])

    def test_assistant_decisions_prefill_a_human_check(self):
        self.decide({'Kept Study': 'belongs', 'Rejected Study': 'not_belongs'})
        assistant = import_review(self.truth, self.sheet, 'Assistant', False, review_state='assistant_reviewed')
        export_review(assistant, self.sheet)
        with self.sheet.open(encoding='utf-8-sig', newline='') as handle:
            self.assertEqual({row['decision'] for row in csv.DictReader(handle)}, {''})
        export_review(assistant, self.sheet, prefill=('human_reviewed', 'assistant_reviewed'))
        with self.sheet.open(encoding='utf-8-sig', newline='') as handle:
            decisions = {row['title']: row['decision'] for row in csv.DictReader(handle)}
        self.assertEqual(decisions, {'Kept Study': 'belongs', 'Rejected Study': 'not_belongs'})
        checked = import_review(assistant, self.sheet, 'Reviewer A', False, reviewed_at='2026-02-01')
        self.assertEqual(checked['metadata']['annotation_state'], 'human_reviewed')
        self.assertEqual({entry['annotation_status'] for entry in checked['papers']}, {'human_reviewed'})

    def test_pending_rows_are_refused_unless_partial_progress_is_requested(self):
        self.decide({'Kept Study': 'belongs'})
        with self.assertRaises(ValueError):
            import_review(self.truth, self.sheet, 'Reviewer A', complete_positive_list=True)
        partial = import_review(self.truth, self.sheet, 'Reviewer A', complete_positive_list=True, allow_pending=True)
        self.assertEqual(partial['metadata']['annotation_state'], 'pending_human_review')
        self.assertFalse(partial['complete_positive_list'])

    def test_system_suggestions_are_leads_that_need_independent_evidence(self):
        from paper_author_searcher.benchmark.review import system_suggestions

        collection = {
            'collection': {
                'all_papers': [
                    {
                        'title': 'Kept Study',
                        'year': 2024,
                        'doi': '10.1004/kept',
                        'authors': ['Alex Morgan'],
                        'ownership_status': 'verified',
                    },
                    {
                        'title': 'Unlisted Study',
                        'year': 2023,
                        'doi': '10.1004/unlisted',
                        'authors': ['Alex Morgan'],
                        'ownership_status': 'verified',
                        'source': 'openalex',
                        'source_paper_id': 'W9',
                    },
                    {
                        'title': 'Old Study',
                        'year': 2001,
                        'doi': '10.1004/old',
                        'authors': ['Alex Morgan'],
                        'ownership_status': 'verified',
                    },
                    {
                        'title': 'replication_data.tab',
                        'year': 2024,
                        'doi': '10.7910/dvn/x',
                        'authors': ['Alex Morgan'],
                        'ownership_status': 'verified',
                        'paper_type': 'dataset',
                    },
                ]
            }
        }
        leads = system_suggestions(collection, self.truth, (2020, 2025), 'run1', 10, excluded_types=('dataset',))
        self.assertEqual([lead['title'] for lead in leads], ['Unlisted Study'])
        export_review(self.truth, self.sheet, leads)
        with self.sheet.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row['row_kind'] for row in rows], ['truth', 'truth', 'system_suggestion'])
        for row in rows:
            row['decision'] = 'belongs' if row['row_kind'] == 'system_suggestion' else 'belongs'
        with self.sheet.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaises(ValueError):
            import_review(self.truth, self.sheet, 'Reviewer A', complete_positive_list=False)
        rows[-1]['reviewer_note'] = 'Listed on the CV, page 3'
        with self.sheet.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        reviewed = import_review(self.truth, self.sheet, 'Reviewer A', complete_positive_list=True)
        added = [entry for entry in reviewed['papers'] if 'confirmed_system_suggestion' in entry['truth_sources']]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]['dois'], ['10.1004/unlisted'])

    def test_rejected_system_suggestion_becomes_an_explicit_negative(self):
        from paper_author_searcher.benchmark.review import system_suggestions

        collection = {
            'collection': {
                'all_papers': [
                    {
                        'title': 'Namesake Study',
                        'year': 2024,
                        'ownership_status': 'verified',
                        'doi': '10.1004/namesake',
                        'authors': ['Alex Morgan'],
                    }
                ]
            }
        }
        self.decide({'Kept Study': 'belongs', 'Rejected Study': 'belongs'})
        with self.sheet.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        lead = system_suggestions(collection, self.truth, (2020, 2025), 'run1', 10)[0]
        lead['decision'] = 'not_belongs'
        rows.append(lead)
        with self.sheet.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        reviewed = import_review(self.truth, self.sheet, 'Reviewer A', complete_positive_list=False)
        negative = [entry for entry in reviewed['papers'] if entry['ownership'] == 'not_belongs']
        self.assertEqual(negative[0]['title'], 'Namesake Study')

    def test_same_as_leads_merge_and_uncertain_leads_stay_unscored(self):
        from paper_author_searcher.benchmark.review import system_suggestions

        collection = {
            'collection': {
                'all_papers': [
                    {
                        'title': 'Kept Study preprint',
                        'year': 2023,
                        'doi': '10.1004/kept-preprint',
                        'ownership_status': 'verified',
                        'authors': ['Alex Morgan', 'Jane Smith'],
                    },
                    {
                        'title': 'Trial registration',
                        'year': 2024,
                        'doi': '10.1004/registry',
                        'ownership_status': 'verified',
                        'authors': ['Alex Morgan'],
                    },
                ]
            }
        }
        self.decide({'Kept Study': 'belongs', 'Rejected Study': 'belongs'})
        with self.sheet.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        kept_id = next(row['evaluation_id'] for row in rows if row['title'] == 'Kept Study')
        leads = system_suggestions(collection, self.truth, (2020, 2025), 'run1', 10)
        by_title = {lead['title']: lead for lead in leads}
        by_title['Kept Study preprint']['decision'] = 'same_as:' + kept_id
        by_title['Trial registration']['decision'] = 'uncertain'
        rows.extend(leads)
        with self.sheet.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        reviewed = import_review(
            self.truth, self.sheet, 'Assistant', complete_positive_list=True, review_state='assistant_reviewed'
        )
        kept = next(entry for entry in reviewed['papers'] if entry['evaluation_id'] == kept_id)
        self.assertIn('10.1004/kept-preprint', kept['dois'])
        registry = next(entry for entry in reviewed['papers'] if entry['title'] == 'Trial registration')
        self.assertEqual(registry['ownership'], 'uncertain')
        self.assertEqual(reviewed['metadata']['annotation_state'], 'assistant_reviewed')
        from paper_author_searcher.benchmark.scoring import truth_state

        self.assertEqual(truth_state(reviewed), 'assistant_reviewed')

    def test_invalid_decision_is_rejected(self):
        self.decide({'Kept Study': 'maybe'})
        with self.assertRaises(ValueError):
            import_review(self.truth, self.sheet, 'Reviewer A', complete_positive_list=False, allow_pending=True)


class FakeSystem:
    """Writes a minimal workflow directory with the papers it is told to 'find'."""

    name = 'fake'

    def __init__(self, found, confirmations=None):
        self.found = found
        self.confirmations = confirmations or {}
        self.calls = []

    def search(self, person, output_dir):
        self.calls.append(person.person_id)
        if person.person_id not in self.found:
            raise RuntimeError('search crashed')
        root = Path(output_dir) / 'search_fake'
        root.mkdir(parents=True)
        papers = [dict(record, records=[dict(record)]) for record in self.found[person.person_id]]
        write_json(
            root / 'papers.json',
            {
                'collection': {
                    'all_papers': papers,
                    'sources': [],
                    'complete': True,
                    'identity_confirmation': self.confirmations.get(person.person_id, 'resolver_resolved'),
                }
            },
        )
        report = {
            'run_status': 'completed',
            'output_directory': str(root),
            'errors': [],
            'artifacts': {'papers': 'papers.json'},
        }
        write_json(root / 'report.json', report)
        return report


def found(title, doi, status='verified'):
    return {
        'title': title,
        'year': 2024,
        'authors': ['Alex Morgan', 'Jane Smith'],
        'doi': doi,
        'source': 'synthetic',
        'source_paper_id': doi,
        'ownership_status': status,
    }


class RunScoreGateTests(unittest.TestCase):
    def setUp(self):
        people = [
            person('senior_good', seniority='senior'),
            person('junior_poor', seniority='junior', profile='common'),
        ]
        listing = manual(
            [
                paper('Study One', doi='10.1005/one'),
                paper('Study Two', doi='10.1005/two'),
                paper('Study Three', doi='10.1005/three'),
                paper('Study Four', doi='10.1005/four'),
            ]
        )
        self.workspace = Workspace(
            people, {'senior_good': {'list.json': listing}, 'junior_poor': {'list.json': listing}}
        )
        self.workspace.settings.paths.truth.mkdir(parents=True)
        for person_id in ('senior_good', 'junior_poor'):
            write_json(truth_paths(self.workspace.settings, person_id)[1], self.workspace.build(person_id))
        self.system = FakeSystem(
            {
                'senior_good': [
                    found('Study One', '10.1005/one'),
                    found('Study Two', '10.1005/two'),
                    found('Study Three', '10.1005/three'),
                    found('Study Four', '10.1005/four'),
                ],
                'junior_poor': [
                    found('Study One', '10.1005/one'),
                    found('Study Two', '10.1005/two', 'needs_review'),
                    found('Unrelated Study', '10.1005/unrelated'),
                ],
            }
        )

    def tearDown(self):
        self.workspace.close()

    def run_benchmark(self, system=None):
        runner = BenchmarkRunner(self.workspace.settings, system or self.system)
        return runner.run(self.workspace.roster.people, 'run1')

    def test_per_person_metrics_and_strata(self):
        self.run_benchmark()
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', [])
        people = {entry['person_id']: entry for entry in scored['people']}
        self.assertEqual(people['senior_good']['metrics']['verified_paper_recall']['rate'], 1.0)
        poor = people['junior_poor']['metrics']
        self.assertEqual(poor['verified_paper_recall']['rate'], 0.25)
        self.assertEqual(poor['paper_discovery_recall']['rate'], 0.5)
        # Provisional closed world: the unrelated verified paper is a false positive.
        self.assertEqual(poor['author_match_precision']['rate'], 0.5)
        self.assertFalse(poor['actual_download_coverage']['available'])
        strata = {entry['stratum']: entry for entry in scored['strata']}
        overall = strata['overall=all']['metrics']['verified_paper_recall']
        self.assertAlmostEqual(overall['macro'], 0.625)
        self.assertAlmostEqual(overall['micro'], 5 / 8)
        self.assertEqual(overall['minimum'], 0.25)

    def test_gate_fails_on_weak_stratum(self):
        self.run_benchmark()
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', [])
        self.assertEqual(scored['gate']['status'], GateStatus.FAILED.value)
        failed = {(check['stratum'], check['metric']) for check in scored['gate']['failed_checks']}
        self.assertIn(('seniority=junior', 'verified_paper_recall'), failed)
        self.assertNotIn(('seniority=senior', 'verified_paper_recall'), failed)

    def test_passing_provisional_benchmark_is_not_certifiable(self):
        good = FakeSystem({pid: self.system.found['senior_good'] for pid in ('senior_good', 'junior_poor')})
        self.run_benchmark(good)
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', [])
        self.assertEqual(scored['gate']['status'], GateStatus.NOT_CERTIFIABLE.value)
        self.assertEqual(scored['gate']['exit_code'], 3)

    def test_human_reviewed_truth_can_pass(self):
        good = FakeSystem({pid: self.system.found['senior_good'] for pid in ('senior_good', 'junior_poor')})
        for person_id in ('senior_good', 'junior_poor'):
            reviewed, pending = truth_paths(self.workspace.settings, person_id)
            truth = json.loads(pending.read_text())
            for entry in truth['papers']:
                entry.update(ownership='belongs', annotation_status='human_reviewed')
            truth['complete_positive_list'] = True
            truth['metadata'].update(annotation_state='human_reviewed', reviewer='Reviewer A', reviewed_at='2026-02-01')
            write_json(reviewed, truth)
        self.run_benchmark(good)
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', [])
        self.assertEqual(scored['gate']['status'], GateStatus.PASSED.value)
        self.assertTrue(all(entry['truth_state'] == 'human_reviewed' for entry in scored['people']))

    def test_failed_search_is_recorded_and_counts_as_zero_recall(self):
        crashing = FakeSystem({'senior_good': self.system.found['senior_good']})
        index = self.run_benchmark(crashing)
        self.assertEqual(index['people']['junior_poor']['status'], 'failed')
        self.assertIn('search crashed', index['people']['junior_poor']['error'])
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', ['junior_poor'])
        metrics = scored['people'][0]['metrics']['verified_paper_recall']
        self.assertEqual((metrics['numerator'], metrics['denominator'], metrics['rate']), (0, 4, 0.0))

    def test_runner_resumes_finished_people(self):
        self.run_benchmark()
        self.assertEqual(self.system.calls, ['senior_good', 'junior_poor'])
        self.run_benchmark()
        self.assertEqual(self.system.calls, ['senior_good', 'junior_poor'])

    def test_too_few_people_fails_the_stratum(self):
        settings = self.workspace.settings
        strict = replace(settings, minimum_people_per_stratum=2)
        scores = [
            PersonScorer(strict).score(person, settings.paths.runs / 'none', None)
            for person in self.workspace.roster.people
        ]
        result = ReleaseGate(strict).evaluate(aggregate(scores, strict))
        reasons = {check['reason'] for check in result.checks if not check['passed']}
        self.assertTrue(any(reason.startswith('only 1 people') for reason in reasons))

    def test_rule_aggregation_overrides_the_gate_default(self):
        self.run_benchmark()
        settings = self.workspace.settings
        micro = replace(settings, gate_rules=(GateRule('verified_paper_recall', 0.5, aggregation=Aggregation.MICRO),))
        scores = score_run(micro, self.workspace.roster, 'run1', [])
        overall = next(check for check in scores['gate']['checks'] if check['stratum'] == 'overall=all')
        stratum = next(entry for entry in scores['strata'] if entry['stratum'] == 'overall=all')
        self.assertEqual(overall['aggregation'], 'micro')
        self.assertEqual(overall['observed'], stratum['metrics']['verified_paper_recall']['micro'])

    def test_people_below_the_truth_floor_are_left_out_of_that_metric(self):
        self.run_benchmark()
        strict = replace(self.workspace.settings, minimum_truth_positives={'verified_paper_recall': 5})
        scores = score_run(strict, self.workspace.roster, 'run1', [])
        overall = next(entry for entry in scores['strata'] if entry['stratum'] == 'overall=all')
        recall = overall['metrics']['verified_paper_recall']
        self.assertEqual((recall['people_with_rate'], recall['below_truth_minimum']), (0, 2))
        self.assertEqual(overall['metrics']['author_match_precision']['below_truth_minimum'], 0)
        failed = {
            check['reason'] for check in scores['gate']['failed_checks'] if check['metric'] == 'verified_paper_recall'
        }
        self.assertEqual(failed, {'metric unavailable'})

    def test_identity_resolution_rate_counts_only_confirmed_identities(self):
        system = FakeSystem(self.system.found, {'junior_poor': 'ambiguous_contenders'})
        self.run_benchmark(system)
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', [])
        rates = {entry['person_id']: entry['metrics']['identity_resolution_rate']['rate'] for entry in scored['people']}
        self.assertEqual(rates, {'senior_good': 1.0, 'junior_poor': 0.0})
        overall = next(entry for entry in scored['strata'] if entry['stratum'] == 'overall=all')
        self.assertEqual(overall['metrics']['identity_resolution_rate']['macro'], 0.5)

    def test_identity_precision_flags_a_resolved_namesake(self):
        namesake = [found('Unrelated Study', '10.1005/unrelated'), found('Other Study', '10.1005/other')]
        system = FakeSystem(
            {'senior_good': self.system.found['senior_good'], 'junior_poor': namesake},
        )
        self.run_benchmark(system)
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', [])
        rates = {entry['person_id']: entry['metrics']['identity_precision']['rate'] for entry in scored['people']}
        self.assertEqual(rates, {'senior_good': 1.0, 'junior_poor': 0.0})

    def test_identity_precision_has_no_rate_for_unresolved_people(self):
        system = FakeSystem(self.system.found, {'junior_poor': 'ambiguous_contenders'})
        self.run_benchmark(system)
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', ['junior_poor'])
        metric = scored['people'][0]['metrics']['identity_precision']
        self.assertEqual((metric['denominator'], metric['rate']), (0, None))

    def test_verified_papers_outside_the_window_are_not_false_positives(self):
        old = dict(found('Old Unlisted Study', '10.1005/old'), year=2012)
        system = FakeSystem({pid: self.system.found['senior_good'] + [old] for pid in ('senior_good', 'junior_poor')})
        self.run_benchmark(system)
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', ['senior_good'])
        self.assertEqual(scored['people'][0]['metrics']['author_match_precision']['rate'], 1.0)

    def test_non_publication_output_is_neither_a_hit_nor_a_false_positive(self):
        files = [
            dict(found(f'replication_{index}.tab', f'10.7910/dvn/{index}'), paper_type='dataset') for index in range(3)
        ]
        system = FakeSystem({pid: self.system.found['senior_good'] + files for pid in ('senior_good', 'junior_poor')})
        self.run_benchmark(system)
        scored = score_run(self.workspace.settings, self.workspace.roster, 'run1', ['senior_good'])
        self.assertEqual(scored['people'][0]['metrics']['author_match_precision']['rate'], 1.0)
        unscoped = replace(
            self.workspace.settings, type_policy=replace(self.workspace.settings.type_policy, non_publication_output=())
        )
        unscoped_scores = score_run(unscoped, self.workspace.roster, 'run1', ['senior_good'])
        self.assertLess(unscoped_scores['people'][0]['metrics']['author_match_precision']['rate'], 1.0)

    def test_cli_score_writes_reports_and_enforces_gate(self):
        self.run_benchmark()
        config_path = str(self.workspace.root / 'benchmark.json')
        with redirect_stdout(io.StringIO()):
            code = benchmark_main(['--config', config_path, 'score', '--run-id', 'run1', '--enforce-gate'])
        self.assertEqual(code, 1)
        reports = self.workspace.settings.paths.reports
        self.assertTrue((reports / 'run1.scores.json').exists())
        self.assertIn('| overall=all |', (reports / 'run1.md').read_text())


class FakeWorkflow:
    def __init__(self, calls, root):
        self.calls, self.root = calls, root

    def run(self, output_dir, mode, saved=None, query=None, **limits):
        folder = Path(output_dir) / ('pass' + str(len(self.calls)))
        folder.mkdir(parents=True)
        self.calls.append({'mode': mode, 'saved': saved, 'query': query})
        archive = saved or {
            'collection': {
                'all_papers': [
                    {'title': 'Recent', 'year': 2024},
                    {'title': 'Ancient', 'year': 2001},
                    {'title': 'Undated', 'year': None},
                ]
            }
        }
        write_json(folder / 'papers.json', archive)
        return {'run_status': 'completed', 'output_directory': str(folder), 'artifacts': {'papers': 'papers.json'}}


class WindowedFullTextTests(unittest.TestCase):
    def test_window_archive_keeps_window_and_undated_papers(self):
        archive = {'collection': {'all_papers': [{'year': 2024}, {'year': 2001}, {'year': None}], 'papers': []}}
        kept = window_archive(archive, (2020, 2025))['collection']
        self.assertEqual([paper['year'] for paper in kept['all_papers']], [2024, None])
        self.assertEqual(len(archive['collection']['all_papers']), 3)

    def test_full_mode_runs_metadata_then_windowed_pdf_pass(self):
        workspace = Workspace([person('a_one', year_start=2024, year_end=2025)], {})
        calls = []
        try:
            system = WorkflowSearchSystem(
                RunSettings(mode='full'),
                workspace.settings,
                workflow_factory=lambda run: FakeWorkflow(calls, workspace.root),
            )
            report = system.search(workspace.roster.people[0], workspace.root / 'out')
        finally:
            workspace.close()
        self.assertEqual([call['mode'] for call in calls], ['metadata', 'full'])
        self.assertIsNotNone(calls[0]['query'])
        titles = [paper['title'] for paper in calls[1]['saved']['collection']['all_papers']]
        self.assertEqual(titles, ['Recent', 'Undated'])
        self.assertTrue(report['fulltext_report'].endswith('pass1'))


if __name__ == '__main__':
    unittest.main()


class IdentityDiagnosisTests(unittest.TestCase):
    def setUp(self):
        from paper_author_searcher.benchmark.identity_diagnosis import Diagnosis, IdentityDiagnoser
        from paper_author_searcher.models import ProfessorProfile

        self.Diagnosis, self.Diagnoser, self.Profile = Diagnosis, IdentityDiagnoser, ProfessorProfile
        self.truth = {
            'papers': [
                {
                    'evaluation_id': 'p1',
                    'ownership': 'uncertain',
                    'proposed_ownership': 'belongs',
                    'dois': ['10.1000/a'],
                },
                {
                    'evaluation_id': 'p2',
                    'ownership': 'uncertain',
                    'proposed_ownership': 'belongs',
                    'title': 'Second Study',
                },
            ]
        }
        workspace = Workspace([person('a_one')], {})
        self.person = workspace.roster.people[0]
        workspace.close()

    def profile(self, author_id, score, status='ambiguous'):
        return self.Profile(
            'Alex Morgan',
            [],
            {'openalex': author_id},
            score,
            status,
            [],
            match_score=score,
            university_status='matched',
            author_id_aliases={'openalex': [author_id]},
        )

    def diagnose(self, works, errors=None, selected=None):
        class Lister:
            def works(self, source, author_id, start, end):
                return works.get(author_id, [])

        profiles = [self.profile('A1', 0.9), self.profile('A2', 0.88), self.profile('B1', 0.87)]
        diagnoser = self.Diagnoser(lambda query: (profiles, [], errors or {}), Lister())
        return diagnoser.diagnose(self.person, self.truth, (2020, 2025), selected, 'identity_confirmation_required')

    def test_two_truth_bearing_contenders_are_split_profiles(self):
        result = self.diagnose({'A1': [{'doi': '10.1000/A'}], 'A2': [{'title': 'Second study'}]})
        self.assertEqual(result['diagnosis'], self.Diagnosis.STUCK_SPLIT_PROFILES.value)
        self.assertEqual(result['truth_found_in_any_candidate'], 2)

    def test_chosen_ids_without_truth_are_the_wrong_person(self):
        result = self.diagnose({'A2': [{'doi': '10.1000/a'}]}, selected={'openalex': ['B1']})
        self.assertEqual(result['diagnosis'], self.Diagnosis.RESOLVED_WRONG_PERSON.value)

    def test_nothing_found_after_source_errors_is_inconclusive(self):
        self.assertEqual(
            self.diagnose({}, errors={'openalex': 'HTTP 429'})['diagnosis'], self.Diagnosis.INCONCLUSIVE.value
        )
        self.assertEqual(self.diagnose({})['diagnosis'], self.Diagnosis.TARGET_NOT_RETRIEVED.value)
