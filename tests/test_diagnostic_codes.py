import json
import tempfile
import unittest
from pathlib import Path

from paper_author_searcher.diagnostic_codes import ErrorCategory, PdfFailureReason, error_category, error_fields
from paper_author_searcher.http import SourceError
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.performance_diagnostics import pdf_failure_reasons, workflow_outcomes
from paper_author_searcher.workflow import PaperSearchWorkflow
from paper_author_searcher.workflow.stages import WorkflowStage


class ErrorCategoryTests(unittest.TestCase):
    def test_structured_fields_decide_before_the_message(self):
        self.assertEqual(
            error_category({'error': 'lookup failed', 'status_code': 429}), ErrorCategory.SOURCE_UNAVAILABLE
        )
        self.assertEqual(
            error_category({'error': 'lookup failed', 'status_code': 503}), ErrorCategory.SOURCE_UNAVAILABLE
        )
        self.assertEqual(
            error_category({'error': 'lookup failed', 'status_code': 410}), ErrorCategory.RESOURCE_UNAVAILABLE
        )
        self.assertEqual(
            error_category({'error': 'lookup failed', 'error_type': 'timeout'}), ErrorCategory.NETWORK_FAILURE
        )

    def test_errors_recorded_only_as_text_still_classify(self):
        cases = {
            'Read timed out': ErrorCategory.NETWORK_FAILURE,
            'HTTP 429 from api.openalex.org': ErrorCategory.SOURCE_UNAVAILABLE,
            'Incomplete collection or pagination': ErrorCategory.QUERY_INCOMPLETE,
            'HTTP 404 from example.org': ErrorCategory.RESOURCE_UNAVAILABLE,
            'boom': ErrorCategory.STAGE_ERROR,
        }
        for message, category in cases.items():
            with self.subTest(message=message):
                self.assertEqual(error_category({'error': message}), category)

    def test_an_unrecognised_structured_field_falls_back_to_the_message(self):
        error = {'error': 'HTTP 404 from example.org', 'status_code': 400, 'error_type': 'invalid_request'}
        self.assertEqual(error_category(error), ErrorCategory.RESOURCE_UNAVAILABLE)

    def test_error_fields_keep_type_and_status_only_when_present(self):
        self.assertEqual(
            error_fields(SourceError('HTTP 429', status_code=429)), {'error_type': 'rate_limited', 'status_code': 429}
        )
        self.assertEqual(error_fields(ValueError('boom')), {})


class CodeSerialisationTests(unittest.TestCase):
    def test_codes_serialise_as_their_plain_values(self):
        reasons = pdf_failure_reasons({'download_status': 'failed'})
        self.assertEqual(json.loads(json.dumps(reasons)), ['download_failed'])
        self.assertIs(type(json.loads(json.dumps(reasons))[0]), str)
        self.assertEqual(PdfFailureReason.NO_PUBLIC_PDF_FOUND, 'no_public_pdf_found_in_queried_sources')

    def test_outcome_counts_are_keyed_by_the_plain_category(self):
        outcomes = workflow_outcomes({'errors': [{'stage': 'S', 'error': 'x', 'status_code': 503}]})
        self.assertEqual(json.loads(json.dumps(outcomes))['error_observation_counts'], {'source_unavailable': 1})


class WorkflowErrorRecordingTests(unittest.TestCase):
    def test_a_failed_stage_keeps_the_source_errors_status_and_type(self):
        class Failing(WorkflowStage):
            name = 'Searching'

            def run(self, context):
                raise SourceError('lookup failed', status_code=503)

        with tempfile.TemporaryDirectory() as directory:
            report = PaperSearchWorkflow(stages=[Failing()]).run(
                query=ProfessorQuery(name='Ada Lovelace', university='University of London'),
                output_dir=Path(directory),
                mode='metadata',
            )
        [error] = report['errors']
        self.assertEqual(error['status_code'], 503)
        self.assertEqual(error['error_type'], 'server_error')
        self.assertEqual(error['category'], ErrorCategory.SOURCE_UNAVAILABLE)


if __name__ == '__main__':
    unittest.main()
