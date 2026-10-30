"""The asynchronous search API: schemas, keys, job store, routes, result mapping and the HTTP front end."""

import json
import os
import sqlite3
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import Executor, Future
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from paper_author_searcher.api.app import Request, SearchAPI
from paper_author_searcher.api.auth import ApiKeys
from paper_author_searcher.api.backend import BackendOptions, SearchOutcome, WorkflowBackend, query_from_request
from paper_author_searcher.api.executor import JobExecutor
from paper_author_searcher.api.jobs import IdempotencyConflict, Job, JobStatus, JobStore
from paper_author_searcher.api.results import ResultView, candidates
from paper_author_searcher.api.schema import SCHEMA_NAMES, SchemaError, Validator, schema, validator
from paper_author_searcher.api.server import handler_for

REQUEST = {'name': 'Ada Lovelace', 'university': 'Example University', 'department': 'Computer Science'}
CANDIDATES = [
    {'name': 'Ada Lovelace', 'selections': ['openalex:A1'], 'match_score': 0.9, 'affiliations': [], 'evidence': []},
    {'name': 'Ada Lovelace', 'selections': ['openalex:A2'], 'match_score': 0.89, 'affiliations': [], 'evidence': []},
]
RESULT = {
    'run_status': 'completed',
    'identity': {'name': 'Ada Lovelace', 'status': 'resolved', 'orcid': None, 'author_ids': {}, 'evidence': []},
    'papers': [
        {
            'id': '0',
            'title': 'Notes',
            'authors': ['Ada Lovelace'],
            'year': 1843,
            'venue': None,
            'doi': None,
            'ownership_status': 'verified',
            'full_text': {'status': 'file', 'url': 'https://example.org/notes.pdf', 'version': None, 'download': 'x'},
        }
    ],
    'statistics': {'papers': 1, 'verified': 1, 'full_text_files': 1, 'full_text_links': 0},
    'warnings': [],
}


class Inline(Executor):
    """Runs submitted work at once, so a test sees the finished job right after submitting it."""

    def submit(self, fn, /, *args, **kwargs):
        future: Future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class FakeBackend:
    def __init__(self, outcomes, pdf=None):
        self.outcomes = list(outcomes)
        self.pdf = pdf
        self.runs = []

    def run(self, job, progress):
        self.runs.append(job)
        progress('Collecting papers', 1, 2)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def file_path(self, job, paper_id):
        return self.pdf if paper_id == '0' else None

    def archive(self, job):
        return self.pdf


class Harness:
    def __init__(self, directory, outcomes, pdf=None):
        self.store = JobStore(Path(directory) / 'jobs.sqlite')
        self.keys = ApiKeys([])
        _, self.secret = self.keys.create('agent')
        _, self.other = self.keys.create('other')
        self.backend = FakeBackend(outcomes, pdf)
        self.executor = JobExecutor(self.store, self.backend, pool=Inline(), progress_interval=0)
        self.api = SearchAPI(self.store, self.keys, self.executor.submit, self.backend)

    def call(self, method, path, body=None, secret=None, **headers):
        headers = {name.replace('_', '-').lower(): value for name, value in headers.items()}
        if secret is not False:
            headers['authorization'] = 'Bearer ' + (secret or self.secret)
        raw = json.dumps(body).encode() if body is not None else b''
        return self.api.handle(Request(method, path, headers, raw))


class SchemaTests(unittest.TestCase):
    def test_every_shipped_schema_loads_with_supported_keywords_only(self):
        for name in SCHEMA_NAMES:
            self.assertIsInstance(validator(name), Validator)
            self.assertTrue(schema(name)['$id'].endswith(f'/v1/{name}.json'))

    def test_unsupported_keywords_are_refused_not_ignored(self):
        with self.assertRaises(SchemaError):
            Validator({'type': 'string', 'format': 'email'})
        with self.assertRaises(SchemaError):
            Validator({'$ref': 'https://elsewhere.example/schema.json'})

    def test_search_requests_are_checked(self):
        check = validator('search-request').errors
        self.assertEqual(check(REQUEST), [])
        full = dict(REQUEST, hints={'orcid': '0000-0002-1825-0097', 'homepage': 'https://ada.example'}, mode='metadata')
        self.assertEqual(check(full), [])
        self.assertIn('$.university: required', check({'name': 'Ada'}))
        self.assertIn('$.colour: unknown field', check(dict(REQUEST, colour='blue')))
        self.assertTrue(check(dict(REQUEST, mode='everything')))
        self.assertTrue(check(dict(REQUEST, hints={'orcid': '1234'})))
        self.assertTrue(check(dict(REQUEST, hints={'homepage': 'ftp://ada.example'})))
        self.assertTrue(check(dict(REQUEST, limits={'max_downloads': 0})))
        self.assertEqual(check(dict(REQUEST, name='')), ['$.name: at least 1 characters'])

    def test_one_of_and_nullable_types(self):
        check = validator('search').errors
        job = {
            'api_version': 'v1',
            'id': 'srch_1',
            'status': 'running',
            'created_at': 'now',
            'updated_at': 'now',
            'request': REQUEST,
            'links': {'self': '/v1/searches/srch_1'},
        }
        self.assertEqual(check(dict(job, progress=None)), [])
        self.assertEqual(check(dict(job, progress={'stage': 'x', 'completed': None, 'total': 3})), [])
        self.assertTrue(check(dict(job, progress={'completed': 1})))
        self.assertEqual(check(dict(job, status='succeeded', result=RESULT)), [])
        self.assertTrue(check(dict(job, api_version='v2')))


class KeyTests(unittest.TestCase):
    def test_keys_are_stored_as_hashes_and_checked_by_bearer_header(self):
        keys = ApiKeys([])
        record, secret = keys.create('agent')
        self.assertNotIn(secret, json.dumps(vars(record)))
        self.assertEqual(keys.authenticate({'authorization': 'Bearer ' + secret}), record)
        self.assertIsNone(keys.authenticate({'authorization': 'Bearer wrong'}))
        self.assertIsNone(keys.authenticate({'authorization': 'Basic ' + secret}))
        self.assertIsNone(keys.authenticate({}))
        self.assertTrue(keys.revoke(record.id))
        self.assertIsNone(keys.authenticate({'authorization': 'Bearer ' + secret}))

    def test_keys_file_round_trips_and_is_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'keys.json'
            keys = ApiKeys([])
            record, secret = keys.create('agent')
            keys.save(path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertNotIn(secret, path.read_text())
            self.assertEqual(ApiKeys.load(path).authenticate({'authorization': 'Bearer ' + secret}), record)
            self.assertEqual(ApiKeys.load(Path(directory) / 'missing.json').keys, [])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.directory.name) / 'jobs.sqlite')

    def tearDown(self):
        self.directory.cleanup()

    def test_jobs_round_trip_and_transitions_are_conditional(self):
        job = self.store.create(Job('srch_1', 'key_a', REQUEST))
        self.assertEqual(self.store.get('srch_1'), job)
        self.assertIsNone(self.store.transition('srch_1', JobStatus.RUNNING, status=JobStatus.FAILED))
        running = self.store.transition('srch_1', JobStatus.QUEUED, status=JobStatus.RUNNING, progress={'stage': 's'})
        self.assertEqual((running.status, running.progress), (JobStatus.RUNNING, {'stage': 's'}))
        self.assertEqual([item.id for item in self.store.with_status(JobStatus.RUNNING)], ['srch_1'])
        with self.assertRaises(KeyError):
            self.store.update('missing', status=JobStatus.FAILED)

    def test_idempotency_keys_replay_conflict_and_expire(self):
        self.assertIsNone(self.store.reserve('key_a', 'k1', 'fp'))
        with self.assertRaises(IdempotencyConflict) as raised:
            self.store.reserve('key_a', 'k1', 'fp')
        self.assertEqual(raised.exception.reason, 'in_progress')
        self.store.remember('key_a', 'k1', 202, {'id': 'srch_1'})
        self.assertEqual(self.store.reserve('key_a', 'k1', 'fp').body, {'id': 'srch_1'})
        with self.assertRaises(IdempotencyConflict) as raised:
            self.store.reserve('key_a', 'k1', 'other')
        self.assertEqual(raised.exception.reason, 'reused')
        self.assertIsNone(self.store.reserve('key_b', 'k1', 'other'))  # keys are per caller
        past = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec='seconds')
        with sqlite3.connect(self.store.path) as db:
            db.execute('UPDATE idempotency SET created_at = ?', (past,))
        self.assertIsNone(self.store.reserve('key_a', 'k1', 'other'))  # older than the 24-hour window


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def harness(self, *outcomes, pdf=None):
        return Harness(self.directory.name, outcomes, pdf)

    def assertSchema(self, name, body):
        self.assertEqual(validator(name).errors(body), [])

    def test_public_routes_and_errors(self):
        api = self.harness()
        self.assertEqual(api.call('GET', '/v1/health', secret=False).status, 200)
        response = api.call('GET', '/v1/schemas/search-request.json', secret=False)
        self.assertEqual((response.status, response.body['title']), (200, 'Search request (v1)'))
        unauthorised = api.call('POST', '/v1/searches', REQUEST, secret=False)
        self.assertEqual((unauthorised.status, unauthorised.headers['WWW-Authenticate']), (401, 'Bearer'))
        self.assertSchema('error', unauthorised.body)
        self.assertEqual(api.call('GET', '/v1/searches/srch_x').status, 404)
        self.assertEqual(api.call('GET', '/v2/searches').status, 404)
        self.assertEqual(api.call('DELETE', '/v1/searches/srch_x').status, 405)

    def test_invalid_bodies_are_rejected_with_every_problem(self):
        api = self.harness()
        response = api.call('POST', '/v1/searches', {'name': 'Ada', 'mode': 'all'})
        self.assertEqual((response.status, response.body['error']['code']), (422, 'invalid_request'))
        self.assertEqual(len(response.body['error']['details']), 2)
        bad = api.api.handle(Request('POST', '/v1/searches', {'authorization': 'Bearer ' + api.secret}, b'{'))
        self.assertEqual((bad.status, bad.body['error']['code']), (400, 'invalid_json'))

    def test_a_search_runs_in_the_background_and_its_result_is_polled(self):
        api = self.harness(SearchOutcome(JobStatus.SUCCEEDED, '/runs/x', result=RESULT))
        created = api.call('POST', '/v1/searches', REQUEST)
        self.assertEqual((created.status, created.body['status']), (202, 'queued'))
        self.assertEqual(created.headers['Location'], created.body['links']['self'])
        self.assertSchema('search', created.body)
        shown = api.call('GET', created.body['links']['self'])
        self.assertEqual((shown.body['status'], shown.body['result']), ('succeeded', RESULT))
        self.assertIsNone(shown.body['progress'])
        self.assertSchema('search', shown.body)
        self.assertEqual(api.backend.runs[0].request, REQUEST)

    def test_other_callers_cannot_see_a_job(self):
        api = self.harness(SearchOutcome(JobStatus.SUCCEEDED, result=RESULT))
        created = api.call('POST', '/v1/searches', REQUEST)
        self.assertEqual(api.call('GET', created.body['links']['self'], secret=api.other).status, 404)

    def test_ambiguous_identity_waits_for_confirmation_then_continues(self):
        api = self.harness(
            SearchOutcome(JobStatus.NEEDS_CONFIRMATION, '/runs/1', '/runs/1/professor.json', CANDIDATES),
            SearchOutcome(JobStatus.SUCCEEDED, '/runs/2', result=RESULT),
        )
        created = api.call('POST', '/v1/searches', REQUEST)
        waiting = api.call('GET', created.body['links']['self'])
        self.assertEqual(waiting.body['status'], 'needs_confirmation')
        self.assertEqual(waiting.body['candidates'], CANDIDATES)
        self.assertSchema('search', waiting.body)
        confirmation = waiting.body['links']['confirmation']
        wrong = api.call('POST', confirmation, {'selection': 'openalex:A9'})
        self.assertEqual((wrong.status, wrong.body['error']['details']), (422, ['openalex:A1', 'openalex:A2']))
        accepted = api.call('POST', confirmation, {'selection': 'openalex:A2'})
        self.assertEqual((accepted.status, accepted.body['selection']), (202, 'openalex:A2'))
        rerun = api.backend.runs[1]
        self.assertEqual((rerun.selection, rerun.identity_checkpoint), ('openalex:A2', '/runs/1/professor.json'))
        done = api.call('GET', created.body['links']['self'])
        self.assertEqual((done.body['status'], done.body['candidates']), ('succeeded', CANDIDATES))
        self.assertEqual(api.call('POST', confirmation, {'selection': 'openalex:A1'}).status, 409)

    def test_idempotent_retries_return_the_first_response(self):
        api = self.harness(SearchOutcome(JobStatus.SUCCEEDED, result=RESULT))
        first = api.call('POST', '/v1/searches', REQUEST, idempotency_key='retry-1')
        again = api.call('POST', '/v1/searches', REQUEST, idempotency_key='retry-1')
        self.assertEqual((again.status, again.body['id']), (202, first.body['id']))
        self.assertEqual(again.headers['Idempotent-Replayed'], 'true')
        self.assertEqual(len(api.backend.runs), 1)
        changed = api.call('POST', '/v1/searches', dict(REQUEST, name='Other'), idempotency_key='retry-1')
        self.assertEqual((changed.status, changed.body['error']['code']), (422, 'idempotency_key_reused'))
        self.assertEqual(api.call('POST', '/v1/searches', REQUEST, idempotency_key='').status, 400)

    def test_a_refused_request_frees_its_idempotency_key(self):
        api = self.harness(SearchOutcome(JobStatus.SUCCEEDED, result=RESULT))
        created = api.call('POST', '/v1/searches', REQUEST)
        path = created.body['links']['self'] + '/confirmation'
        self.assertEqual(api.call('POST', path, {'selection': 'openalex:A1'}, idempotency_key='c').status, 409)
        self.assertIsNone(api.store.reserve(api.keys.keys[0].id, 'c', 'fresh'))

    def test_checked_pdfs_are_served_and_others_are_not(self):
        pdf = Path(self.directory.name) / 'paper.pdf'
        pdf.write_bytes(b'%PDF-1.7')
        api = self.harness(SearchOutcome(JobStatus.SUCCEEDED, result=RESULT), pdf=pdf)
        created = api.call('POST', '/v1/searches', REQUEST)
        served = api.call('GET', created.body['links']['self'] + '/papers/0/pdf')
        self.assertEqual((served.status, served.file, served.headers['Content-Type']), (200, pdf, 'application/pdf'))
        self.assertEqual(api.call('GET', created.body['links']['self'] + '/papers/1/pdf').status, 404)

    def test_a_crashing_search_fails_its_job_and_restarts_are_recovered(self):
        api = self.harness(RuntimeError('boom'))
        created = api.call('POST', '/v1/searches', REQUEST)
        failed = api.call('GET', created.body['links']['self']).body
        self.assertEqual((failed['status'], failed['error']['code']), ('failed', 'internal_error'))
        self.assertSchema('search', failed)
        api.store.create(Job('srch_left', api.keys.keys[0].id, REQUEST, status=JobStatus.RUNNING))
        api.backend.outcomes.append(SearchOutcome(JobStatus.SUCCEEDED, result=RESULT))
        self.assertEqual(api.executor.recover(), 1)
        self.assertEqual(api.store.get('srch_left').status, JobStatus.SUCCEEDED)


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / 'papers').mkdir()
        (self.root / 'papers' / 'a.pdf').write_bytes(b'%PDF-1.7')
        papers = [
            {'title': 'Filed', 'authors': ['A'], 'year': 2020, 'ownership_status': 'verified', 'doi': '10.1/a'},
            {'title': 'Linked', 'authors': ['A'], 'year': 2021, 'ownership_status': 'verified'},
            {'title': 'Unsure', 'authors': ['A'], 'year': 2022, 'ownership_status': 'needs_review'},
            {'title': 'Namesake', 'authors': ['A'], 'year': 2023, 'ownership_status': 'rejected'},
        ]
        links = [
            {'validation_status': 'verified', 'best_pdf_url': 'https://x/a.pdf', 'version_type': 'publishedVersion'},
            {'validation_status': 'verified', 'best_pdf_url': 'https://x/b.pdf', 'version_type': 'preprint'},
            {'validation_status': 'unverified'},
            {},
        ]
        checked = {'format_status': 'valid', 'identity_status': 'verified', 'validation_status': 'verified'}
        files = [
            dict(checked, local_filename='a.pdf', final_url='https://x/a.pdf', version_type='publishedVersion'),
            dict(checked, local_filename='../escape.pdf'),
            {},
            {},
        ]
        for name, data in (
            ('papers.json', {'collection': {'all_papers': papers}}),
            ('pdf_urls.json', {'papers': links}),
            ('manifest.json', {'papers': files}),
        ):
            (self.root / name).write_text(json.dumps(data))
        self.report = {
            'output_directory': str(self.root),
            'run_status': 'partial',
            'artifacts': {'papers': 'papers.json', 'pdf_urls': 'pdf_urls.json', 'manifest': 'manifest.json'},
            'identity': {'profile': {'name': 'A', 'status': 'resolved', 'author_ids': {'openalex': 'A1'}}},
            'warnings': [{'stage': 'x', 'warning': 'careful'}],
        }
        self.view = ResultView(lambda paper_id: f'/pdf/{paper_id}')

    def tearDown(self):
        self.directory.cleanup()

    def test_papers_join_their_links_and_checked_files(self):
        result = self.view.build(self.report, include_needs_review=False)
        self.assertEqual(validator('search').errors({'result': result} | self.envelope()), [])
        self.assertEqual([paper['title'] for paper in result['papers']], ['Filed', 'Linked'])
        filed, linked = result['papers']
        self.assertEqual(
            filed['full_text'],
            {'status': 'file', 'url': 'https://x/a.pdf', 'version': 'publishedVersion', 'download': '/pdf/0'},
        )
        # A file outside the run's folder is never served, but the paper keeps its validated link.
        self.assertEqual(linked['full_text']['status'], 'link')
        self.assertEqual(result['statistics'], {'papers': 2, 'verified': 2, 'full_text_files': 1, 'full_text_links': 1})
        self.assertEqual((result['run_status'], result['warnings']), ('partial', ['careful']))
        self.assertEqual(self.view.file_path(self.report, '0'), (self.root / 'papers' / 'a.pdf').resolve())
        self.assertIsNone(self.view.file_path(self.report, '1'))
        self.assertIsNone(self.view.file_path(self.report, '9'))

    def test_needs_review_papers_only_when_asked(self):
        result = self.view.build(self.report, include_needs_review=True)
        self.assertEqual(result['papers'][-1]['ownership_status'], 'needs_review')
        self.assertEqual(result['papers'][-1]['full_text']['status'], 'none')

    def test_candidates_carry_every_selection(self):
        identity = {
            'candidates': [
                {'name': 'A', 'author_ids': {'openalex': 'A1'}, 'author_id_aliases': {'openalex': ['A1', 'A0']}},
                {'name': 'B', 'author_ids': {}},
            ]
        }
        self.assertEqual(candidates(identity)[0]['selections'], ['openalex:A0', 'openalex:A1'])
        self.assertEqual(len(candidates(identity)), 1)

    @staticmethod
    def envelope():
        return {
            'api_version': 'v1',
            'id': 's',
            'status': 'succeeded',
            'created_at': 'n',
            'updated_at': 'n',
            'request': {},
            'links': {'self': '/v1/searches/s'},
        }


class BackendOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.backend = WorkflowBackend(BackendOptions(Path('/tmp/unused')), results=ResultView(str))
        self.job = Job('srch_1', 'key', REQUEST)

    def test_confirmation_required_with_and_without_candidates(self):
        identity = {'candidates': [{'name': 'A', 'author_ids': {'openalex': 'A1'}}]}
        report = {
            'run_status': 'identity_confirmation_required',
            'output_directory': '/runs/1',
            'identity': identity,
            'artifacts': {'professor': 'professor.json'},
        }
        waiting = self.backend.outcome(self.job, report)
        self.assertEqual(
            (waiting.status, waiting.identity_checkpoint), ('needs_confirmation', '/runs/1/professor.json')
        )
        missing = self.backend.outcome(self.job, dict(report, identity={}))
        self.assertEqual((missing.status, missing.error['code']), ('failed', 'identity_not_found'))
        rejected = self.backend.outcome(Job('s', 'k', REQUEST, selection='openalex:A1'), report)
        self.assertEqual(rejected.error['code'], 'selection_rejected')

    def test_failed_runs_fail_the_job(self):
        outcome = self.backend.outcome(self.job, {'run_status': 'failed', 'errors': [{'error': 'source down'}]})
        self.assertEqual((outcome.status, outcome.error['details']), ('failed', ['source down']))

    def test_requests_become_queries(self):
        query = query_from_request(dict(REQUEST, hints={'orcid': '0000-0002-1825-0097', 'profile_urls': ['https://p']}))
        self.assertEqual(
            (query.department, query.orcid, query.profile_urls),
            ('Computer Science', '0000-0002-1825-0097', ['https://p']),
        )


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        pdf = Path(self.directory.name) / 'paper.pdf'
        pdf.write_bytes(b'%PDF-1.7 test')
        self.harness = Harness(self.directory.name, [SearchOutcome(JobStatus.SUCCEEDED, result=RESULT)], pdf)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.harness.api, 1024))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def send(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        request.add_header('Authorization', 'Bearer ' + self.harness.secret)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_end_to_end_over_http(self):
        status, headers, body = self.send('POST', '/v1/searches', REQUEST, {'Content-Type': 'application/json'})
        self.assertEqual(status, 202)
        job = json.loads(body)
        status, _, body = self.send('GET', headers['Location'])
        self.assertEqual((status, json.loads(body)['status']), (200, 'succeeded'))
        status, headers, body = self.send('GET', job['links']['self'] + '/papers/0/pdf')
        self.assertEqual((status, headers['Content-Type'], body), (200, 'application/pdf', b'%PDF-1.7 test'))

    def test_oversized_bodies_are_refused(self):
        status, _, body = self.send('POST', '/v1/searches', dict(REQUEST, name='x' * 2000))
        self.assertEqual((status, json.loads(body)['error']['code']), (413, 'body_too_large'))


if __name__ == '__main__':
    unittest.main()


class EditionTests(unittest.TestCase):
    """Editions the collection links as related versions come back as one work."""

    def test_linked_editions_become_one_work_led_by_the_published_one(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / 'papers').mkdir()
        (root / 'papers' / 'p.pdf').write_bytes(b'%PDF')
        arxiv = {'source': 'arxiv', 'paper_id': '2401.1', 'doi': '10.48550/arxiv.2401.1'}
        papers = [
            {
                'title': 'Work',
                'ownership_status': 'verified',
                'doi': '10.48550/arxiv.2401.1',
                'source': 'arxiv',
                'source_paper_id': '2401.1',
                'related_versions': [],
            },
            {'title': 'Other', 'ownership_status': 'verified', 'source': 'dblp', 'source_paper_id': 'conf/x/2'},
            {
                'title': 'Work',
                'ownership_status': 'verified',
                'doi': '10.18653/v1/acl.1',
                'venue': 'ACL',
                'source': 'dblp',
                'source_paper_id': 'conf/acl/1',
                'related_versions': [{'identifiers': [arxiv]}],
            },
        ]
        checked = {'format_status': 'valid', 'identity_status': 'verified', 'validation_status': 'verified'}
        files = [dict(checked, local_filename='p.pdf', final_url='https://arxiv.org/pdf/2401.1'), {}, {}]
        (root / 'papers.json').write_text(json.dumps({'collection': {'papers': papers}}))
        (root / 'manifest.json').write_text(json.dumps({'papers': files}))
        report = {
            'output_directory': str(root),
            'run_status': 'completed',
            'artifacts': {'papers': 'papers.json', 'manifest': 'manifest.json'},
        }
        result = ResultView(lambda paper_id: f'/pdf/{paper_id}').build(report, include_needs_review=False)
        self.assertEqual([paper['title'] for paper in result['papers']], ['Work', 'Other'])
        work = result['papers'][0]
        # The edition holding the checked file leads, so the work's download is that file.
        self.assertEqual((work['id'], work['full_text']['status']), ('0', 'file'))
        self.assertEqual([(edition['id'], edition['venue']) for edition in work['editions']], [('2', 'ACL')])
        self.assertEqual(result['statistics']['papers'], 2)

    def test_candidates_are_capped(self):
        identity = {'candidates': [{'name': f'N{i}', 'author_ids': {'openalex': f'A{i}'}} for i in range(30)]}
        self.assertEqual(len(candidates(identity)), 10)
        self.assertEqual(len(candidates(identity, limit=3)), 3)


class PageAndArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_the_web_page_needs_no_key(self):
        api = Harness(self.directory.name, [])
        page = api.call('GET', '/', secret=False)
        self.assertEqual((page.status, page.headers['Content-Type']), (200, 'text/html; charset=utf-8'))
        self.assertIn('/v1/searches', page.file.read_text(encoding='utf-8'))

    def test_the_zip_route_serves_the_archive(self):
        bundle = self.root / 'x.zip'
        bundle.write_bytes(b'PK')
        api = Harness(self.directory.name, [SearchOutcome(JobStatus.SUCCEEDED, result=RESULT)], pdf=bundle)
        created = api.call('POST', '/v1/searches', REQUEST)
        served = api.call('GET', created.body['links']['self'] + '/pdfs.zip')
        self.assertEqual((served.status, served.file, served.headers['Content-Type']), (200, bundle, 'application/zip'))
        self.assertIn('attachment', served.headers['Content-Disposition'])

    def test_the_archive_bundles_every_checked_file_once(self):
        run = self.root / 'run'
        (run / 'papers').mkdir(parents=True)
        for name in ('a.pdf', 'b.pdf'):
            (run / 'papers' / name).write_bytes(b'%PDF ' + name.encode())
        checked = {'format_status': 'valid', 'identity_status': 'verified', 'validation_status': 'verified'}
        (run / 'manifest.json').write_text(
            json.dumps({'papers': [dict(checked, local_filename='a.pdf'), dict(checked, local_filename='b.pdf'), {}]})
        )
        (run / 'report.json').write_text(
            json.dumps({'output_directory': str(run), 'artifacts': {'manifest': 'manifest.json'}})
        )
        file = {'status': 'file', 'url': None, 'version': None, 'download': 'x'}
        none = {'status': 'none', 'url': None, 'version': None, 'download': None}
        result = dict(
            RESULT,
            papers=[
                dict(
                    RESULT['papers'][0], id='0', full_text=file, editions=[{'id': '1', 'title': 'v', 'full_text': file}]
                ),
                dict(RESULT['papers'][0], id='2', full_text=none),
            ],
        )
        backend = WorkflowBackend(BackendOptions(self.root))
        job = Job('srch_1', 'k', REQUEST, status=JobStatus.SUCCEEDED, run_directory=str(run), result=result)
        archive = backend.archive(job)
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(sorted(bundle.namelist()), ['a.pdf', 'b.pdf'])
        self.assertEqual(backend.archive(job), archive)  # built once
        self.assertIsNone(backend.archive(Job('srch_2', 'k', REQUEST)))


class FailureTests(unittest.TestCase):
    def test_unexpected_errors_become_a_500_error_body(self):
        with tempfile.TemporaryDirectory() as directory:
            api = Harness(directory, [SearchOutcome(JobStatus.SUCCEEDED, result=RESULT)])
            created = api.call('POST', '/v1/searches', REQUEST)

            def broken(job, paper_id):
                raise KeyError('output_directory')

            api.backend.file_path = broken
            with self.assertLogs('paper_author_searcher.api.app', 'ERROR'):
                response = api.call('GET', created.body['links']['self'] + '/papers/0/pdf')
            self.assertEqual((response.status, response.body['error']['code']), (500, 'internal_error'))

    def test_files_are_found_from_a_real_run_report(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / 'papers').mkdir()
            (run / 'papers' / 'a.pdf').write_bytes(b'%PDF')
            checked = {'format_status': 'valid', 'identity_status': 'verified', 'validation_status': 'verified'}
            (run / 'manifest.json').write_text(json.dumps({'papers': [dict(checked, local_filename='a.pdf')]}))
            report = {
                'report_type': 'paper_search_workflow',
                'output_directory': str(run),
                'artifacts': {'manifest': 'manifest.json'},
                'identity': {'profile': {'name': 'A'}},
            }
            (run / 'report.json').write_text(json.dumps(report))
            job = Job('srch_1', 'k', REQUEST, status=JobStatus.SUCCEEDED, run_directory=str(run))
            self.assertEqual(
                WorkflowBackend(BackendOptions(run)).file_path(job, '0'), (run / 'papers' / 'a.pdf').resolve()
            )
