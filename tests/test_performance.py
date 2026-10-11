"""Synthetic benchmark contracts, never fabricated real professor labels."""
import hashlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
from dataclasses import replace
import xml.etree.ElementTree as ET

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.evaluation_matching import GroundTruth, GroundTruthMatcher
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.models import ProfessorProfile, PaperRecord, PaperSourceResult
from paper_author_searcher.candidate_diagnostics import collect_candidate
from paper_author_searcher.performance_diagnostics import ownership_diagnostics, pdf_failure_reasons
from paper_author_searcher.performance_evaluation import PerformanceEvaluator, compare_performance
from paper_author_searcher.pdf_catalogs import JournalCatalogPDFProvider, CatalogLinks
from paper_author_searcher.pdf_identity import metadata_identity
from paper_author_searcher.pdf_providers import OpenAlexPDFProvider, SemanticScholarPDFProvider, ArxivPDFProvider
from paper_author_searcher.reference_import import prepare_reference
from paper_author_searcher.http import URLProbe, SourceError
from test_evaluation import truth, truth_paper, entry, saved
from test_pdf_providers import paper, openalex_work


def html(url, content):
    return URLProbe(url, 200, 'text/html', {}, content.encode('utf-8'))


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.labels = truth([truth_paper('a'), truth_paper('b'), truth_paper('c'), truth_paper('negative', 'not_belongs')])
        self.labels['metadata'] = {'annotation_state': 'human_reviewed', 'reviewer': 'Synthetic fixture reviewer', 'reviewed_at': '2026-10-11'}
        self.collection = saved([entry('a'), entry('b', 'needs_review'), entry('negative')])
        self.evaluator = PerformanceEvaluator()

    def test_discovery_author_metrics_and_missing_stages(self):
        report = self.evaluator.evaluate(self.collection, self.labels)
        metrics = report['metrics']
        self.assertAlmostEqual(metrics['paper_discovery_recall']['rate'], 2 / 3)
        self.assertAlmostEqual(metrics['verified_paper_recall']['rate'], 1 / 3)
        self.assertEqual(metrics['author_match_precision']['rate'], .5)
        self.assertEqual(metrics['author_decision_accuracy']['rate'], .5)
        self.assertAlmostEqual(metrics['author_decision_coverage']['rate'], 2 / 3)
        self.assertIsNone(metrics['validated_pdf_url_coverage']['rate'])
        self.assertIsNone(metrics['actual_download_coverage']['rate'])
        self.assertFalse(report['recall_target']['formally_met'])

    def test_url_is_not_download_and_file_hash_required(self):
        url_paper = dict(entry('a'), pdf_candidates=[{'validation_status': 'verified', 'identity_status': 'verified'}],
                         validation_status='verified')
        raw = b'%PDF-synthetic-already-validated-fixture'
        (self.root / 'papers').mkdir()
        (self.root / 'papers' / 'a.pdf').write_bytes(raw)
        download = dict(entry('a'), download_status='downloaded', format_status='valid',
                        identity_status='verified', validation_status='verified', local_filename='a.pdf',
                        sha256=hashlib.sha256(raw).hexdigest(), file_size=len(raw))
        manifest = {'papers': [download], 'file_root': 'papers'}
        report = self.evaluator.evaluate(self.collection, self.labels, {'papers': [url_paper]}, manifest,
                                         self.root / 'manifest.json')
        self.assertAlmostEqual(report['metrics']['validated_pdf_url_coverage']['rate'], 1 / 3)
        self.assertAlmostEqual(report['metrics']['actual_download_coverage']['rate'], 1 / 3)
        self.assertEqual(report['metrics']['actual_download_success_rate']['rate'], 1)
        (self.root / 'papers' / 'a.pdf').write_bytes(b'altered')
        report = self.evaluator.evaluate(self.collection, self.labels, {'papers': [url_paper]}, manifest,
                                         self.root / 'manifest.json')
        self.assertEqual(report['metrics']['actual_download_coverage']['rate'], 0)
        self.assertEqual(len(report['unchecked_local_files']), 1)

    def test_valid_format_with_review_identity_is_not_success(self):
        raw = b'synthetic'
        path = self.root / 'a.pdf'; path.write_bytes(raw)
        item = dict(entry('a'), download_status='downloaded', format_status='valid', identity_status='needs_review',
                    validation_status='needs_review', local_filename='a.pdf', file_size=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest())
        report = self.evaluator.evaluate(self.collection, self.labels, manifest={'papers': [item]},
                                         manifest_path=self.root / 'manifest.json')
        self.assertEqual(report['metrics']['download_format_success_rate']['rate'], 1)
        self.assertEqual(report['metrics']['actual_download_success_rate']['rate'], 0)

    def test_unjudged_download_review_is_reported_without_scoring_it(self):
        row = dict(entry('unjudged'), download_status='downloaded', format_status='valid',
                   identity_status='needs_review', validation_status='needs_review')
        report = self.evaluator.evaluate(self.collection, self.labels, manifest={'papers': [row]},
                                         manifest_path=self.root / 'manifest.json')
        diagnostics = report['unavailable_or_unconfirmed_papers']
        self.assertEqual(len(diagnostics), 1)
        self.assertIn('downloaded_paper_identity_unconfirmed', diagnostics[0]['reasons'])
        self.assertEqual(report['metrics']['actual_download_success_rate']['denominator'], 0)

    def test_provisional_reference_cannot_meet_formal_target(self):
        reference = truth([dict(truth_paper('a'), ownership='uncertain', proposed_ownership='belongs')])
        reference['metadata'] = {'annotation_state': 'pending_human_review'}
        with self.assertRaisesRegex(ValueError, 'human_reviewed'):
            self.evaluator.evaluate(saved([entry('a')]), reference)
        report = self.evaluator.evaluate(saved([entry('a')]), reference, reference_only=True)
        self.assertEqual(report['metrics']['paper_discovery_recall']['rate'], 1)
        self.assertFalse(report['formal_metrics'])
        self.assertFalse(report['recall_target']['formally_met'])
        self.assertEqual(reference['papers'][0]['ownership'], 'uncertain')

    def test_pending_rows_and_blank_reviewers_do_not_establish_human_ground_truth(self):
        pending = deepcopy(self.labels)
        pending['papers'][0]['annotation_status'] = 'pending_human_review'
        with self.assertRaisesRegex(ValueError, 'pending_human_review'):
            self.evaluator.evaluate(self.collection, pending)
        pending = deepcopy(self.labels)
        pending['metadata']['reviewer'] = '  '
        with self.assertRaisesRegex(ValueError, 'human_reviewed'):
            self.evaluator.evaluate(self.collection, pending)

    def test_comparison_requires_same_reference_and_reports_change(self):
        before = self.evaluator.evaluate(self.collection, self.labels)
        after = self.evaluator.evaluate(saved([entry('a'), entry('b'), entry('c')]), self.labels)
        delta = compare_performance(before, after)
        self.assertAlmostEqual(delta['metrics']['paper_discovery_recall']['absolute_rate_change'], 1 / 3)
        self.assertTrue(delta['recall_target']['formally_met'])
        after['ground_truth_fingerprint'] = 'other'
        with self.assertRaises(ValueError):
            compare_performance(before, after)

    def test_comparison_identifies_processing_scope_and_matching_rule_changes(self):
        before = self.evaluator.evaluate(self.collection, self.labels, {'papers': [entry('a')]})
        after = self.evaluator.evaluate(self.collection, self.labels, {'papers': [entry('a'), entry('b')]})
        self.assertFalse(compare_performance(before, after)['same_pdf_processing_scope'])
        after['matching_rules_version'] = 999
        with self.assertRaisesRegex(ValueError, 'Matching rules differ'):
            compare_performance(before, after)

    def test_malformed_pdf_report_is_not_treated_as_zero_coverage(self):
        with self.assertRaisesRegex(ValueError, 'papers list'):
            self.evaluator.evaluate(self.collection, self.labels, pdf_report={'statistics': {}})

    def test_skipped_limits_are_not_download_attempts(self):
        manifest = {'papers': [dict(entry('a'), download_status='skipped_limit'),
                               dict(entry('b'), download_status='skipped_ownership')]}
        report = self.evaluator.evaluate(self.collection, self.labels, manifest=manifest,
                                         manifest_path=self.root / 'manifest.json')
        self.assertIsNone(report['metrics']['actual_download_success_rate']['rate'])
        self.assertEqual(report['metrics']['actual_download_success_rate']['denominator'], 0)

    def test_conflicting_merged_gt_records_do_not_double_count_pdf_coverage(self):
        combined = entry('a', records=[entry('a'), entry('b')])
        pdf = dict(combined, validation_status='verified', pdf_candidates=[
            {'validation_status': 'verified', 'identity_status': 'verified'}])
        report = self.evaluator.evaluate(saved([combined]), self.labels, {'papers': [pdf]})
        self.assertEqual(report['metrics']['validated_pdf_url_coverage']['numerator'], 0)
        self.assertEqual(report['metrics']['paper_discovery_recall']['numerator'], 2)

    def test_complete_positive_list_includes_inferred_negative_in_author_metrics(self):
        labels = deepcopy(self.labels); labels['complete_positive_list'] = True
        report = self.evaluator.evaluate(saved([entry('a'), entry('unlisted')]), labels)
        self.assertEqual(report['metrics']['author_match_precision']['rate'], .5)
        self.assertEqual(report['metrics']['author_decision_accuracy']['rate'], .5)

    def test_conflicting_ownership_statuses_are_excluded_from_decision_accuracy(self):
        report = self.evaluator.evaluate(saved([entry('a'), entry('a', 'rejected')]), self.labels)
        self.assertIsNone(report['metrics']['author_decision_accuracy']['rate'])
        self.assertEqual(report['metrics']['author_decision_coverage']['rate'], 0)

    def test_uncertain_truth_status_conflicts_do_not_produce_negative_denominators(self):
        labels = deepcopy(self.labels); labels['papers'][0]['ownership'] = 'uncertain'
        report = self.evaluator.evaluate(saved([entry('a'), entry('a', 'rejected')]), labels)
        self.assertEqual(report['metrics']['author_decision_accuracy']['denominator'], 0)
        self.assertIsNone(report['metrics']['author_decision_accuracy']['rate'])

    def test_manifest_path_traversal_does_not_confirm_file(self):
        row = dict(entry('a'), download_status='downloaded', local_filename='../outside.pdf', sha256='hash')
        report = self.evaluator.evaluate(self.collection, self.labels, manifest={'papers': [row]},
                                         manifest_path=self.root / 'manifest.json')
        self.assertIn('Unsafe', report['unchecked_local_files'][0]['reason'])

    def test_symlink_storage_does_not_confirm_file(self):
        outside = self.root / 'other'
        outside.mkdir()
        (self.root / 'papers').symlink_to(outside, target_is_directory=True)
        (outside / 'a.pdf').write_bytes(b'synthetic')
        row = dict(entry('a'), download_status='downloaded', local_filename='a.pdf', sha256='hash')
        report = self.evaluator.evaluate(self.collection, self.labels,
                                         manifest={'papers': [row], 'file_root': 'papers'},
                                         manifest_path=self.root / 'manifest.json')
        self.assertIn('Unsafe', report['unchecked_local_files'][0]['reason'])

    def test_bibliographic_comma_order_normalizes_without_fuzzy_matching(self):
        label = truth_paper('a'); label.pop('doi'); label['authors'] = ['Morgan, Alex', 'Smith, Jane']
        candidate = entry('a'); candidate.pop('doi')
        match = GroundTruthMatcher(GroundTruth.from_dict(truth([label]))).match(candidate)
        self.assertEqual(match.evaluation_id, 'a')

    def test_complete_signature_match_accepts_initials_but_blocks_conflicting_middle_names(self):
        label = truth_paper('a'); label.pop('doi'); label['authors'] = ['Alex Morgan', 'Jane Smith']
        candidate = entry('a'); candidate.pop('doi'); candidate['authors'] = ['A. M. Morgan', 'J. Smith']
        matcher = GroundTruthMatcher(GroundTruth.from_dict(truth([label])))
        self.assertEqual(matcher.match(candidate).evaluation_id, 'a')
        label['authors'] = ['Alex N. Morgan', 'Jane Smith']
        matcher = GroundTruthMatcher(GroundTruth.from_dict(truth([label])))
        self.assertIsNone(matcher.match(candidate).evaluation_id)

    def test_bibliographic_match_abstains_when_initials_fit_competing_truth_papers(self):
        first = truth_paper('a'); first.pop('doi'); first['authors'] = ['Alex Morgan', 'Jane Smith']
        second = dict(first, evaluation_id='b', authors=['Adam Morgan', 'Jane Smith'])
        candidate = entry('a'); candidate.pop('doi'); candidate['authors'] = ['A. Morgan', 'Jane Smith']
        matcher = GroundTruthMatcher(GroundTruth.from_dict(truth([first, second])))
        self.assertEqual(matcher.match(candidate).decision, 'ambiguous')


class DiagnosticsTests(unittest.TestCase):
    def test_candidate_collection_does_not_confirm_identity_or_paper_ownership(self):
        profile = ProfessorProfile(name='Alex Example', status='resolved', author_ids={'openalex': 'A1'},
                                   affiliations=['Example University'], identity_confidence=.9, evidence=[])
        first = Mock(name='provider'); first.name = 'openalex'
        first.collect.return_value = PaperSourceResult('openalex', 'A1', papers=[
            PaperRecord('Synthetic research', ['Alex Example'], source='openalex', source_paper_id='W1',
                  source_author_id='A1', ownership_status='verified', year=2020, doi=None,
                  venue=None, abstract=None, paper_url=None)], complete=True, records_received=1)
        result = collect_candidate(profile, [first])
        self.assertEqual(result['profile']['status'], 'ambiguous')
        self.assertEqual(result['collection']['professor_identity_status'], 'ambiguous')
        self.assertTrue(all(paper['ownership_status'] == 'needs_review' for paper in result['collection']['papers']))
        self.assertEqual(profile.status, 'resolved')

    def test_missing_evidence_and_nonstandard_types_are_separate(self):
        rows = [dict(entry('a', 'needs_review'), author_details=[], authors_complete=False),
                dict(entry('b', 'needs_review'), paper_type='peer_review', author_details=[])]
        report = ownership_diagnostics(rows)
        self.assertEqual(report['reason_counts']['missing_paper_author_details'], 2)
        self.assertEqual(report['reason_counts']['incomplete_author_list'], 1)
        self.assertEqual(report['reason_counts']['nonstandard_or_conflicting_publication_type'], 1)

    def test_no_link_does_not_claim_no_public_source_when_source_failed(self):
        paper = {'validation_status': 'not_found', 'providers': [{'status': 'error'}], 'pdf_candidates': []}
        self.assertIn('search_incomplete', pdf_failure_reasons(paper))
        self.assertNotIn('no_public_pdf_found_in_queried_sources', pdf_failure_reasons(paper))
        paper['providers'] = [{'status': 'ok'}]
        self.assertIn('no_public_pdf_found_in_queried_sources', pdf_failure_reasons(paper))

    def test_access_limits_html_and_identity_mismatch_have_distinct_codes(self):
        paper = {'validation_status': 'not_found', 'pdf_candidates': [
            {'http_status': 403}, {'http_status': 429},
            {'content_type': 'text/html'}, {'identity_status': 'conflicting'}]}
        codes = pdf_failure_reasons(paper)
        for code in ('access_restricted', 'rate_limited', 'not_pdf_response', 'source_identity_mismatch'):
            self.assertIn(code, codes)


class OptimizedPDFTests(unittest.TestCase):
    def test_complete_openalex_locations_are_reused_without_api_lookup(self):
        saved_paper = paper(); work = openalex_work(); work['locations'] = []
        saved_paper['records'][0]['raw_metadata'] = work
        client = Mock()
        result = OpenAlexPDFProvider(client).find(saved_paper)
        self.assertTrue(result.candidates)
        client.get.assert_not_called()

    def test_cached_work_with_different_id_requires_a_fresh_scoped_lookup(self):
        saved_paper = paper(); wrong = openalex_work()
        wrong.update(id='https://openalex.org/W999', locations=[], title='Unrelated work', doi='10.9999/other')
        saved_paper['records'][0]['raw_metadata'] = wrong
        client = Mock(); client.get.return_value = openalex_work()
        result = OpenAlexPDFProvider(client).find(saved_paper)
        client.get.assert_called_once()
        self.assertTrue(all(candidate.identity_status == 'verified' for candidate in result.candidates))

    def test_semantic_null_oa_field_is_real_cached_metadata_not_api_failure(self):
        saved_paper = paper(); saved_paper['records'] = [{
            'source': 'semantic_scholar', 'source_paper_id': 'P1',
            'raw_metadata': {'paperId': 'P1', 'title': saved_paper['title'], 'openAccessPdf': None}}]
        client = Mock()
        result = SemanticScholarPDFProvider(client).find(saved_paper)
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.candidates, [])
        client.get.assert_not_called()

    def test_catalog_title_in_dt_selects_explicit_abstract_link_and_caches_volumes(self):
        config = replace(DEFAULT_PDF, journal_catalogs=(('Example Journal', 'https://journal.example/catalog'),))
        saved_paper = paper(); saved_paper['records'][0]['venue'] = 'Example Journal'
        client = Mock()
        index = '<a href="/v1/">Volume 1</a> (January 2020 - December 2020)'
        volume = '<dt>Evidence for a general method</dt><dd><a href="/v1/method.html">abs</a><a href="/files.pdf">pdf</a></dd>'
        abstract = '<meta name="citation_title" content="Evidence for a general method"><meta name="citation_doi" content="10.1234/example"><meta name="citation_pdf_url" content="/files.pdf">'
        responses = {'https://journal.example/catalog': index, 'https://journal.example/v1/': volume,
                     'https://journal.example/v1/method.html': abstract}
        client.probe.side_effect = lambda url, *args: html(url, responses[url])
        provider = JournalCatalogPDFProvider(client, config)
        result = provider.find(saved_paper)
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertEqual(result.candidates[0].url, 'https://journal.example/files.pdf')
        provider.find(saved_paper)
        self.assertEqual(client.probe.call_count, 4)

    def test_catalog_omitted_dt_end_does_not_include_byline_or_next_title(self):
        parser = CatalogLinks()
        parser.feed('<dt>First study<dd>Alex Example; 2020. <a href="first.html">abs</a>'
                    '<dt>Second study<dd>Other Author; 2021. <a href="second.html">abs</a>')
        self.assertEqual([link['heading'] for link in parser.links], ['First study', 'Second study'])

    def test_catalog_relative_article_links_use_redirected_directory_url(self):
        config = replace(DEFAULT_PDF, journal_catalogs=(('Example Journal', 'https://journal.example/catalog'),))
        saved_paper = paper()
        saved_paper['records'][0]['venue'] = 'Example Journal'
        client = Mock()
        client.probe.side_effect = [
            html('https://journal.example/catalog', '<a href="/v1">Volume 1</a> (2020)'),
            html('https://journal.example/v1/', '<dt>Evidence for a general method<dd>Alex Example; 2020. '
                 '<a href="method.html">abs</a>'),
            html('https://journal.example/v1/method.html',
                 '<meta name="citation_title" content="Evidence for a general method">'
                 '<meta name="citation_doi" content="10.1234/example">'
                 '<meta name="citation_pdf_url" content="/files.pdf">'),
        ]
        result = JournalCatalogPDFProvider(client, config).find(saved_paper)
        self.assertEqual(result.candidates[0].url, 'https://journal.example/files.pdf')
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertEqual(client.probe.call_args_list[2].args[0], 'https://journal.example/v1/method.html')

    def test_secondary_openalex_location_routes_catalog_without_filling_missing_venue(self):
        config = replace(DEFAULT_PDF, journal_catalogs=(('Example Journal', 'https://journal.example/catalog'),))
        saved_paper = paper()
        saved_paper['records'][0]['venue'] = None
        saved_paper['records'][0]['raw_metadata'] = {
            'locations': [{'source': None}, {'source': {'display_name': 'Example Journal'}}],
        }
        client = Mock()
        client.probe.return_value = html('https://journal.example/catalog', '<a href="/v1">Volume 1</a> (2010)')
        result = JournalCatalogPDFProvider(client, config).find(saved_paper)
        self.assertEqual(result.status, 'ok')
        client.probe.assert_called_once()
        self.assertIsNone(saved_paper['records'][0]['venue'])

    def test_arxiv_title_search_does_not_verify_title_alone_or_forge_known_id(self):
        client = Mock()
        client.get.return_value = ET.fromstring('<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            '<id>https://arxiv.org/abs/2001.00001</id><title>Evidence for a general method</title>'
            '<published>2020-01-01</published><author><name>Different Author</name></author>'
            '<link type="application/pdf" href="https://arxiv.org/pdf/2001.00001"/></entry></feed>')
        result = ArxivPDFProvider(client).find(paper())
        self.assertIn('search_query', client.get.call_args.kwargs['params'])
        self.assertEqual(result.candidates[0].identity_status, 'unverified')

    def test_reference_import_keeps_all_human_labels_uncertain(self):
        client = Mock(); url = 'https://journal.example/study'
        client.probe.return_value = html(url, '<meta name="citation_title" content="Synthetic study">'
            '<meta name="citation_author" content="Example, Alex"><meta name="citation_date" content="2020">')
        data = prepare_reference([{'url': url}], 'Alex Example', client)
        self.assertEqual(data['papers'][0]['ownership'], 'uncertain')
        self.assertEqual(data['papers'][0]['proposed_ownership'], 'belongs')
        self.assertEqual(data['papers'][0]['authors'], ['Alex Example'])
        self.assertEqual(data['metadata']['reviewer'], '')

    def test_canonical_author_details_can_support_url_identity_with_raw_byline_preserved(self):
        target = paper(); target['doi'] = None; target['authors'] = ['Morgan, Alex', 'Smith, Jane']
        target['records'][0]['authors_complete'] = True
        target['records'][0]['author_details'] = [{'author_id': 'a', 'name': 'Alex Morgan'},
                                                {'author_id': 'b', 'name': 'Jane Smith'}]
        identity = metadata_identity(target, 'landing_page', title=target['title'], year=2020,
                                     authors=['Alex Morgan', 'Jane Smith'])
        self.assertEqual(identity[0], 'verified')
        self.assertEqual(target['authors'], ['Morgan, Alex', 'Smith, Jane'])


if __name__ == '__main__':
    unittest.main()
