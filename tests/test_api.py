"""The HTTP adapter: schemas, keys, routes, status codes and the HTTP server, over the real service core."""

import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from paper_author_searcher.api.app import Request, SearchAPI
from paper_author_searcher.api.auth import ApiKeys
from paper_author_searcher.api.schema import SCHEMA_NAMES, SchemaError, Validator, schema, validator
from paper_author_searcher.api.server import build, handler_for
from paper_author_searcher.service.facade import SearchService
from paper_author_searcher.service.model import JobStatus, SubmissionLimits
from paper_author_searcher.service.ports import SearchOutcome, StoredFile
from paper_author_searcher.settings import Settings
from test_service import CANDIDATES, REQUEST, Core

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
            'full_text': {'status': 'file', 'url': None, 'version': None, 'download': '/v1/searches/x/papers/0/pdf'},
        }
    ],
    'statistics': {'papers': 1, 'verified': 1, 'full_text_files': 1, 'full_text_links': 0},
    'warnings': [],
}


class Harness:
    """The HTTP API over the real core; each submission is processed at once by an inline worker."""

    def __init__(self, directory, outcomes):
        self.core = Core(directory, outcomes)
        self.core.service.on_submit = self.core.worker.run_once
        self.store = self.core.repository
        self.keys = ApiKeys([])
        _, self.secret = self.keys.create('agent')
        _, self.other = self.keys.create('other')
        self.api = SearchAPI(self.core.service, self.keys)

    def succeed(self, files=('0',)):
        pdf = self.core.pdf('paper.pdf', b'%PDF-1.7 test')
        return SearchOutcome(JobStatus.SUCCEEDED, result=RESULT, files={paper_id: pdf for paper_id in files})

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


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def harness(self, *outcomes):
        return Harness(self.directory.name, list(outcomes))

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
        page = api.call('GET', '/', secret=False)
        self.assertEqual((page.status, page.headers['Content-Type']), (200, 'text/html; charset=utf-8'))
        self.assertIn('/v1/searches', page.file.read_text(encoding='utf-8'))

    def test_readiness_and_metrics(self):
        api = self.harness()
        waiting = api.call('GET', '/v1/ready', secret=False)
        self.assertEqual((waiting.status, waiting.body['checks']['workers']['ok']), (503, False))
        api.store.heartbeat('w1', None)
        self.assertEqual(api.call('GET', '/v1/ready', secret=False).status, 200)
        api.call('GET', '/v1/health', secret=False)
        metrics = api.call('GET', '/metrics', secret=False)
        self.assertTrue(metrics.headers['Content-Type'].startswith('text/plain'))
        self.assertIn('paper_search_workers_alive 1', metrics.text)
        self.assertIn('paper_search_http_requests_total{route="/v1/health",code="200"} 1', metrics.text)

    def test_invalid_bodies_are_rejected_with_every_problem(self):
        api = self.harness()
        response = api.call('POST', '/v1/searches', {'name': 'Ada', 'mode': 'all'})
        self.assertEqual((response.status, response.body['error']['code']), (422, 'invalid_request'))
        self.assertEqual(len(response.body['error']['details']), 2)
        bad = api.api.handle(Request('POST', '/v1/searches', {'authorization': 'Bearer ' + api.secret}, b'{'))
        self.assertEqual((bad.status, bad.body['error']['code']), (400, 'invalid_json'))

    def test_a_search_is_queued_run_and_polled(self):
        api = self.harness()
        api.core.runner.outcomes.append(api.succeed())
        created = api.call('POST', '/v1/searches', REQUEST)
        self.assertEqual((created.status, created.body['status']), (202, 'queued'))
        self.assertEqual(created.headers['Location'], created.body['links']['self'])
        self.assertSchema('search', created.body)
        shown = api.call('GET', created.body['links']['self'])
        self.assertEqual((shown.body['status'], shown.body['result']), ('succeeded', RESULT))
        self.assertIsNone(shown.body['progress'])
        self.assertSchema('search', shown.body)
        self.assertEqual(api.core.runner.calls[0][0].request, REQUEST)

    def test_other_callers_cannot_see_a_job(self):
        api = self.harness()
        api.core.runner.outcomes.append(api.succeed())
        created = api.call('POST', '/v1/searches', REQUEST)
        self.assertEqual(api.call('GET', created.body['links']['self'], secret=api.other).status, 404)

    def test_ambiguous_identity_waits_for_confirmation_then_continues(self):
        api = self.harness()
        checkpoint = api.core.pdf('professor.json', b'{}')
        api.core.runner.outcomes += [
            SearchOutcome(JobStatus.NEEDS_CONFIRMATION, CANDIDATES, identity_checkpoint=checkpoint),
            api.succeed(),
        ]
        created = api.call('POST', '/v1/searches', REQUEST)
        waiting = api.call('GET', created.body['links']['self'])
        self.assertEqual((waiting.body['status'], waiting.body['candidates']), ('needs_confirmation', CANDIDATES))
        self.assertSchema('search', waiting.body)
        confirmation = waiting.body['links']['confirmation']
        wrong = api.call('POST', confirmation, {'selection': 'openalex:A9'})
        self.assertEqual((wrong.status, wrong.body['error']['details']), (422, ['openalex:A1', 'openalex:A2']))
        accepted = api.call('POST', confirmation, {'selection': 'openalex:A2'})
        self.assertEqual((accepted.status, accepted.body['selection']), (202, 'openalex:A2'))
        rerun, checkpoint_text = api.core.runner.calls[1]
        self.assertEqual((rerun.selection, checkpoint_text), ('openalex:A2', '{}'))
        done = api.call('GET', created.body['links']['self'])
        self.assertEqual((done.body['status'], done.body['candidates']), ('succeeded', CANDIDATES))
        self.assertEqual(api.call('POST', confirmation, {'selection': 'openalex:A1'}).status, 409)

    def test_idempotent_retries_act_on_the_same_job(self):
        api = self.harness()
        api.core.runner.outcomes.append(api.succeed())
        first = api.call('POST', '/v1/searches', REQUEST, idempotency_key='retry-1')
        again = api.call('POST', '/v1/searches', REQUEST, idempotency_key='retry-1')
        self.assertEqual((again.status, again.body['id']), (202, first.body['id']))
        self.assertEqual((again.headers['Idempotent-Replayed'], again.body['status']), ('true', 'succeeded'))
        self.assertEqual(len(api.core.runner.calls), 1)
        changed = api.call('POST', '/v1/searches', dict(REQUEST, name='Other'), idempotency_key='retry-1')
        self.assertEqual((changed.status, changed.body['error']['code']), (422, 'idempotency_key_reused'))
        self.assertEqual(api.call('POST', '/v1/searches', REQUEST, idempotency_key='').status, 400)

    def test_checked_pdfs_and_zips_are_served_and_others_are_not(self):
        api = self.harness()
        api.core.runner.outcomes.append(api.succeed())
        created = api.call('POST', '/v1/searches', REQUEST)
        served = api.call('GET', created.body['links']['self'] + '/papers/0/pdf')
        self.assertEqual((served.status, served.headers['Content-Type']), (200, 'application/pdf'))
        self.assertEqual(served.file.read_bytes(), b'%PDF-1.7 test')
        self.assertEqual(api.call('GET', created.body['links']['self'] + '/papers/1/pdf').status, 404)
        bundle = api.call('GET', created.body['links']['self'] + '/pdfs.zip')
        self.assertEqual((bundle.status, bundle.headers['Content-Type']), (200, 'application/zip'))
        self.assertIn('attachment', bundle.headers['Content-Disposition'])

    def test_object_store_files_are_redirects(self):
        api = self.harness()

        class Redirecting(SearchService):
            def paper_file(self, client, job_id, paper_id):
                return StoredFile(url='https://store.example/signed')

        api.api.service = Redirecting(api.store, api.core.files)
        response = api.call('GET', '/v1/searches/srch_x/papers/0/pdf')
        self.assertEqual((response.status, response.headers['Location']), (302, 'https://store.example/signed'))

    def test_a_crashing_search_fails_its_job(self):
        api = self.harness(RuntimeError('boom'))
        with self.assertLogs('paper_author_searcher.service.worker', 'ERROR'):
            created = api.call('POST', '/v1/searches', REQUEST)
        failed = api.call('GET', created.body['links']['self']).body
        self.assertEqual((failed['status'], failed['error']['code']), ('failed', 'internal_error'))
        self.assertSchema('search', failed)

    def test_unexpected_errors_become_a_500_error_body(self):
        api = self.harness()

        def broken(client, job_id):
            raise KeyError('boom')

        api.api.service.job = broken
        with self.assertLogs('paper_author_searcher.api.app', 'ERROR'):
            response = api.call('GET', '/v1/searches/srch_x')
        self.assertEqual((response.status, response.body['error']['code']), (500, 'internal_error'))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.harness = Harness(self.directory.name, [])
        self.harness.core.runner.outcomes.append(self.harness.succeed())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.harness.api, 1024))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def send(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header('Authorization', 'Bearer ' + self.harness.secret)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_end_to_end_over_http(self):
        status, headers, body = self.send('POST', '/v1/searches', REQUEST)
        self.assertEqual(status, 202)
        job = json.loads(body)
        status, _, body = self.send('GET', headers['Location'])
        self.assertEqual((status, json.loads(body)['status']), (200, 'succeeded'))
        status, headers, body = self.send('GET', job['links']['self'] + '/papers/0/pdf')
        self.assertEqual((status, headers['Content-Type'], body), (200, 'application/pdf', b'%PDF-1.7 test'))
        status, headers, body = self.send('GET', '/metrics')
        self.assertEqual(status, 200)
        self.assertIn(b'paper_search_jobs{status="succeeded"} 1', body)

    def test_oversized_bodies_are_refused(self):
        status, _, body = self.send('POST', '/v1/searches', dict(REQUEST, name='x' * 2000))
        self.assertEqual((status, json.loads(body)['error']['code']), (413, 'body_too_large'))


class AssemblyTests(unittest.TestCase):
    def test_the_service_is_assembled_from_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.load(
                environ={
                    'PAPER_SEARCH__SERVICE__DATA_DIRECTORY': directory,
                    'PAPER_SEARCH__SERVICE__API_KEY_HASHES': 'key_env:agent:' + 'a' * 64,
                }
            )
            api, deployment = build(settings, workers=2)
            self.assertEqual(len(deployment.workers), 2)
            self.assertEqual([key.id for key in api.keys.keys], ['key_env'])
            self.assertTrue((Path(directory) / 'jobs.sqlite').exists())
            submitted = deployment.service.submit('key_env', REQUEST)
            self.assertEqual(submitted.job.status, JobStatus.QUEUED)


class RateLimitTests(unittest.TestCase):
    def test_refused_submissions_are_429_with_retry_after(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = Harness(directory, [])
            harness.core.service.on_submit = None
            harness.core.service.limits = SubmissionLimits(maximum_active=1, retry_after_seconds=9)
            self.assertEqual(harness.call('POST', '/v1/searches', REQUEST).status, 202)
            refused = harness.call('POST', '/v1/searches', REQUEST)
            self.assertEqual(refused.status, 429)
            self.assertEqual(refused.headers['Retry-After'], '9')
            self.assertEqual(refused.body['error']['code'], 'too_many_active_searches')


if __name__ == '__main__':
    unittest.main()
