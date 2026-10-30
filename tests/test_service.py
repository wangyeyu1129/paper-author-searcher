"""The search service core: repository, worker, facade, results, file stores, S3 signing, metrics, index refresh."""

import json
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest import mock

from paper_author_searcher.api.schema import validator
from paper_author_searcher.config import ServiceConfig
from paper_author_searcher.service.facade import SearchService
from paper_author_searcher.service.files import LocalFileStore, S3FileStore
from paper_author_searcher.service.indexes import IndexRefresher
from paper_author_searcher.service.metrics import RequestCounter, render
from paper_author_searcher.service.model import ErrorKind, IdempotencyConflict, Job, JobStatus, ServiceError
from paper_author_searcher.service.ports import SearchOutcome
from paper_author_searcher.service.results import ResultView, candidates
from paper_author_searcher.service.runner import RunnerOptions, WorkflowRunner, query_from_request
from paper_author_searcher.service.s3 import S3Location, presign
from paper_author_searcher.service.sqlite import SqliteJobRepository
from paper_author_searcher.service.worker import SearchWorker, job_key, paper_key
from paper_author_searcher.settings import Settings

REQUEST = {'name': 'Ada Lovelace', 'university': 'Example University', 'department': 'Computer Science'}
CANDIDATES = [
    {'name': 'Ada Lovelace', 'selections': ['openalex:A1'], 'match_score': 0.9, 'affiliations': [], 'evidence': []},
    {'name': 'Ada Lovelace', 'selections': ['openalex:A2'], 'match_score': 0.89, 'affiliations': [], 'evidence': []},
]
FILE = {'status': 'file', 'url': None, 'version': None, 'download': '/x'}


def result_with_files(*ids):
    papers = [
        {'id': paper_id, 'title': f'Paper {paper_id}', 'ownership_status': 'verified', 'full_text': FILE}
        for paper_id in ids
    ]
    return {
        'run_status': 'completed',
        'identity': {'name': 'A', 'status': 'resolved'},
        'papers': papers,
        'statistics': {'papers': len(ids), 'verified': len(ids), 'full_text_files': len(ids), 'full_text_links': 0},
    }


class FakeRunner:
    """Returns prepared outcomes; a callable outcome is called with (job, checkpoint, directory) first."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def run(self, job, checkpoint, progress):
        self.calls.append((job, checkpoint.read_text() if checkpoint else None))
        progress('Collecting papers', 1, 2)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(job) if callable(outcome) else outcome


class Core:
    """A repository, local file store and one worker over a temporary directory."""

    def __init__(self, directory, outcomes):
        self.root = Path(directory)
        self.repository = SqliteJobRepository(self.root / 'jobs.sqlite')
        self.files = LocalFileStore(self.root / 'files')
        self.runner = FakeRunner(outcomes)
        self.worker = SearchWorker(
            self.repository, self.runner, self.files, self.root / 'scratch', progress_interval=0, worker_id='w1'
        )
        self.service = SearchService(self.repository, self.files)

    def pdf(self, name, content=b'%PDF-1.7'):
        path = self.root / 'run' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = SqliteJobRepository(Path(self.directory.name) / 'jobs.sqlite')

    def tearDown(self):
        self.directory.cleanup()

    def test_jobs_round_trip_and_transitions_are_conditional(self):
        job = self.repository.create(Job('srch_1', 'key_a', REQUEST))
        self.assertEqual(self.repository.get('srch_1'), job)
        self.assertIsNone(self.repository.transition('srch_1', JobStatus.RUNNING, status=JobStatus.FAILED))
        changed = self.repository.transition('srch_1', JobStatus.QUEUED, progress={'stage': 's'})
        self.assertEqual(changed.progress, {'stage': 's'})
        with self.assertRaises(KeyError):
            self.repository.update('missing', status=JobStatus.FAILED)

    def test_the_oldest_queued_job_is_claimed_once(self):
        self.repository.create(Job('srch_b', 'k', REQUEST, created_at='2026-01-02T00:00:00+00:00'))
        self.repository.create(Job('srch_a', 'k', REQUEST, created_at='2026-01-01T00:00:00+00:00'))
        first = self.repository.claim_next('w1')
        self.assertEqual((first.id, first.status, first.worker_id, first.attempts), ('srch_a', 'running', 'w1', 1))
        self.assertEqual(self.repository.claim_next('w2').id, 'srch_b')
        self.assertIsNone(self.repository.claim_next('w3'))

    def test_concurrent_workers_never_share_a_job(self):
        for index in range(20):
            self.repository.create(Job(f'srch_{index:02}', 'k', REQUEST))
        claimed, lock = [], threading.Lock()

        def drain(worker):
            repository = SqliteJobRepository(self.repository.path)
            while (job := repository.claim_next(worker)) is not None:
                with lock:
                    claimed.append(job.id)

        threads = [threading.Thread(target=drain, args=(f'w{index}',)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(claimed), [f'srch_{index:02}' for index in range(20)])

    def test_jobs_of_silent_workers_are_queued_again(self):
        self.repository.create(Job('srch_1', 'k', REQUEST))
        self.repository.claim_next('w1')
        self.repository.heartbeat('w1', 'srch_1')
        self.assertEqual(self.repository.requeue_stale(120), [])
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec='seconds')
        with sqlite3.connect(self.repository.path) as db:
            db.execute('UPDATE workers SET heartbeat_at = ?', (past,))
        self.assertEqual(self.repository.requeue_stale(120), ['srch_1'])
        self.assertEqual(self.repository.get('srch_1').status, JobStatus.QUEUED)
        self.assertEqual(self.repository.workers(), [])

    def test_counts_cover_statuses_runs_spend_and_queue_age(self):
        self.repository.create(Job('srch_1', 'k', REQUEST))
        self.repository.create(
            Job(
                'srch_2',
                'k',
                REQUEST,
                status=JobStatus.SUCCEEDED,
                cost_usd=0.25,
                started_at='2026-01-01T00:00:00+00:00',
                finished_at='2026-01-01T00:01:00+00:00',
            )
        )
        counts = self.repository.counts()
        self.assertEqual((counts['by_status']['queued'], counts['by_status']['succeeded']), (1, 1))
        self.assertAlmostEqual(counts['finished']['succeeded']['seconds'], 60, places=3)
        self.assertEqual(counts['cost_usd'], 0.25)
        self.assertIsNotNone(counts['oldest_queued_at'])

    def test_older_databases_gain_new_columns(self):
        path = Path(self.directory.name) / 'old.sqlite'
        with sqlite3.connect(path) as db:
            db.execute(
                'CREATE TABLE jobs (id TEXT PRIMARY KEY, client TEXT, request TEXT, status TEXT,'
                ' created_at TEXT, updated_at TEXT)'
            )
        repository = SqliteJobRepository(path)
        repository.create(Job('srch_1', 'k', REQUEST, cost_usd=0.1))
        self.assertEqual(repository.get('srch_1').cost_usd, 0.1)

    def test_idempotency_keys_replay_conflict_and_expire(self):
        self.assertIsNone(self.repository.reserve('key_a', 'k1', 'fp'))
        with self.assertRaises(IdempotencyConflict) as raised:
            self.repository.reserve('key_a', 'k1', 'fp')
        self.assertEqual(raised.exception.reason, 'in_progress')
        self.repository.remember('key_a', 'k1', 1, {'job_id': 'srch_1'})
        self.assertEqual(self.repository.reserve('key_a', 'k1', 'fp').body, {'job_id': 'srch_1'})
        with self.assertRaises(IdempotencyConflict) as raised:
            self.repository.reserve('key_a', 'k1', 'other')
        self.assertEqual(raised.exception.reason, 'reused')
        self.assertIsNone(self.repository.reserve('key_b', 'k1', 'other'))  # keys are per caller
        past = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec='seconds')
        with sqlite3.connect(self.repository.path) as db:
            db.execute('UPDATE idempotency SET created_at = ?', (past,))
        self.assertIsNone(self.repository.reserve('key_a', 'k1', 'other'))  # older than the 24-hour window


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def test_a_run_stores_its_files_and_zip_before_recording_success(self):
        core = Core(self.directory.name, [])
        first, second = core.pdf('a.pdf', b'%PDF a'), core.pdf('b.pdf', b'%PDF b')
        core.runner.outcomes.append(
            SearchOutcome(
                JobStatus.SUCCEEDED,
                result=result_with_files('0', '1', '2'),
                files={'0': first, '1': second, '2': first},
                cost_usd=0.01,
            )
        )
        core.repository.create(Job('srch_1', 'k', REQUEST))
        self.assertTrue(core.worker.run_once())
        job = core.repository.get('srch_1')
        self.assertEqual((job.status, job.cost_usd, job.progress), ('succeeded', 0.01, None))
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(core.files.locate(paper_key('srch_1', '1'), 'x').path.read_bytes(), b'%PDF b')
        with zipfile.ZipFile(core.files.locate(job_key('srch_1', 'pdfs.zip'), 'x').path) as bundle:
            self.assertEqual(sorted(bundle.namelist()), ['a.pdf', 'b.pdf'])
        self.assertFalse(core.worker.run_once())

    def test_confirmation_checkpoints_travel_through_the_file_store(self):
        core = Core(self.directory.name, [])
        checkpoint = core.pdf('professor.json', b'{"profile": {}}')
        core.runner.outcomes += [
            SearchOutcome(JobStatus.NEEDS_CONFIRMATION, CANDIDATES, identity_checkpoint=checkpoint),
            SearchOutcome(JobStatus.SUCCEEDED, result=result_with_files()),
        ]
        core.repository.create(Job('srch_1', 'k', REQUEST))
        core.worker.run_once()
        waiting = core.repository.get('srch_1')
        self.assertEqual(
            (waiting.status, waiting.identity_checkpoint), ('needs_confirmation', job_key('srch_1', 'identity.json'))
        )
        core.repository.transition(
            'srch_1', JobStatus.NEEDS_CONFIRMATION, status=JobStatus.QUEUED, selection='openalex:A2'
        )
        core.worker.run_once()
        self.assertEqual(core.runner.calls[1][1], '{"profile": {}}')
        done = core.repository.get('srch_1')
        self.assertEqual((done.status, done.candidates, done.attempts), ('succeeded', CANDIDATES, 2))

    def test_a_crashing_run_fails_the_job(self):
        core = Core(self.directory.name, [RuntimeError('boom')])
        core.repository.create(Job('srch_1', 'k', REQUEST))
        with self.assertLogs('paper_author_searcher.service.worker', 'ERROR'):
            core.worker.run_once()
        job = core.repository.get('srch_1')
        self.assertEqual((job.status, job.error['code'], job.error['details']), ('failed', 'internal_error', ['boom']))

    def test_a_missing_checkpoint_fails_instead_of_resolving_again(self):
        core = Core(self.directory.name, [])
        core.repository.create(Job('srch_1', 'k', REQUEST, selection='openalex:A1', identity_checkpoint='searches/x'))
        with self.assertLogs('paper_author_searcher.service.worker', 'ERROR'):
            core.worker.run_once()
        self.assertEqual(core.repository.get('srch_1').status, JobStatus.FAILED)
        self.assertEqual(core.runner.calls, [])

    def test_run_forever_stops_and_unregisters(self):
        core = Core(self.directory.name, [SearchOutcome(JobStatus.SUCCEEDED, result=result_with_files())])
        core.worker.poll_seconds = 0.05
        core.repository.create(Job('srch_1', 'k', REQUEST))
        thread = threading.Thread(target=core.worker.run_forever)
        thread.start()
        for _ in range(100):
            if core.repository.get('srch_1').status == JobStatus.SUCCEEDED:
                break
            threading.Event().wait(0.05)
        core.worker.stop()
        thread.join(5)
        self.assertEqual(core.repository.get('srch_1').status, JobStatus.SUCCEEDED)
        self.assertEqual(core.repository.workers(), [])


class FacadeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def test_submissions_are_idempotent_and_refusals_free_the_key(self):
        core = Core(self.directory.name, [])
        first = core.service.submit('k', REQUEST, 'retry-1')
        again = core.service.submit('k', REQUEST, 'retry-1')
        self.assertEqual((again.job.id, again.replayed, first.replayed), (first.job.id, True, False))
        with self.assertRaises(ServiceError) as raised:
            core.service.submit('k', dict(REQUEST, name='Other'), 'retry-1')
        self.assertEqual(raised.exception.kind, ErrorKind.IDEMPOTENCY_REUSED)
        with self.assertRaises(ServiceError):
            core.service.confirm('k', first.job.id, 'openalex:A1', 'c1')
        self.assertIsNone(core.repository.reserve('k', 'c1', 'fresh'))

    def test_confirmation_accepts_only_offered_candidates_once(self):
        core = Core(self.directory.name, [])
        job = core.repository.create(
            Job('srch_1', 'k', REQUEST, status=JobStatus.NEEDS_CONFIRMATION, candidates=CANDIDATES)
        )
        with self.assertRaises(ServiceError) as raised:
            core.service.confirm('k', job.id, 'openalex:A9')
        self.assertEqual(
            (raised.exception.kind, raised.exception.details), (ErrorKind.INVALID, ['openalex:A1', 'openalex:A2'])
        )
        queued = core.service.confirm('k', job.id, 'openalex:A2').job
        self.assertEqual((queued.status, queued.selection), ('queued', 'openalex:A2'))
        with self.assertRaises(ServiceError) as raised:
            core.service.confirm('k', job.id, 'openalex:A1')
        self.assertEqual(raised.exception.kind, ErrorKind.CONFLICT)
        with self.assertRaises(ServiceError) as raised:
            core.service.job('someone-else', job.id)
        self.assertEqual(raised.exception.kind, ErrorKind.NOT_FOUND)

    def test_files_are_offered_only_for_results_that_list_them(self):
        core = Core(self.directory.name, [])
        core.files.put(paper_key('srch_1', '0'), core.pdf('a.pdf'), 'application/pdf')
        core.files.put(job_key('srch_1', 'pdfs.zip'), core.pdf('a.zip', b'PK'), 'application/zip')
        core.repository.create(Job('srch_1', 'k', REQUEST, status=JobStatus.SUCCEEDED, result=result_with_files('0')))
        self.assertTrue(core.service.paper_file('k', 'srch_1', '0').path.is_file())
        self.assertTrue(core.service.archive('k', 'srch_1').path.is_file())
        with self.assertRaises(ServiceError):
            core.service.paper_file('k', 'srch_1', '1')

    def test_readiness_and_snapshot(self):
        core = Core(self.directory.name, [])
        names = {check.name: check.ok for check in core.service.readiness()}
        self.assertEqual(names, {'jobs_database': True, 'file_store': True, 'workers': False})
        core.repository.heartbeat('w1', None)
        self.assertTrue(all(check.ok for check in core.service.readiness()))
        service = SearchService(core.repository, core.files, required_paths={'dblp_index': '/missing.sqlite'})
        self.assertFalse(next(c for c in service.readiness() if c.name == 'dblp_index').ok)
        snapshot = core.service.snapshot()
        self.assertEqual(snapshot['workers_alive'], 1)
        text = render(snapshot, RequestCounter())
        self.assertIn('paper_search_workers_alive 1', text)
        self.assertIn('paper_search_jobs{status="queued"} 0', text)

    def test_submissions_wake_in_process_workers(self):
        woken = []
        core = Core(self.directory.name, [])
        service = SearchService(core.repository, core.files, on_submit=lambda: woken.append(True))
        service.submit('k', REQUEST)
        self.assertEqual(woken, [True])


class MetricsTests(unittest.TestCase):
    def test_request_counters_render_per_route_and_code(self):
        counter = RequestCounter()
        counter.add('create', 202)
        counter.add('create', 202)
        snapshot = {
            'by_status': {'queued': 1},
            'finished': {'failed': {'count': 2, 'seconds': 3.5}},
            'oldest_queued_seconds': 4.0,
            'workers_alive': 0,
            'cost_usd': 0.5,
        }
        text = render(snapshot, counter)
        self.assertIn('paper_search_http_requests_total{route="create",code="202"} 2', text)
        self.assertIn('paper_search_job_runs_finished_total{status="failed"} 2', text)
        self.assertIn('paper_search_cost_usd_total 0.5', text)
        self.assertIn('# TYPE paper_search_queue_oldest_seconds gauge', text)


class S3Tests(unittest.TestCase):
    def test_presigned_urls_match_the_aws_documentation_example(self):
        # https://docs.aws.amazon.com/AmazonS3/latest/API/sigv4-query-string-auth.html
        url = presign(
            S3Location('https://s3.amazonaws.com', 'examplebucket', 'us-east-1', 'virtual'),
            'GET',
            'test.txt',
            'AKIAIOSFODNN7EXAMPLE',
            'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
            86400,
            datetime(2013, 5, 24, tzinfo=timezone.utc),
        )
        self.assertTrue(url.startswith('https://examplebucket.s3.amazonaws.com/test.txt?X-Amz-Algorithm='))
        self.assertTrue(
            url.endswith('X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404')
        )

    def test_path_style_urls_and_expiry_limits(self):
        location = S3Location('http://minio:9000', 'papers', 'us-east-1')
        url = presign(location, 'PUT', 'a b/c.pdf', 'id', 'secret', 60)
        self.assertTrue(url.startswith('http://minio:9000/papers/a%20b/c.pdf?'))
        with self.assertRaises(ValueError):
            presign(location, 'GET', 'k', 'id', 'secret', 8 * 24 * 3600)

    def test_the_store_talks_to_an_s3_compatible_server(self):
        objects = {}

        class FakeS3(BaseHTTPRequestHandler):
            def do_PUT(self):
                objects[self.path.split('?')[0]] = self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def do_GET(self):
                data = objects.get(self.path.split('?')[0])
                self.send_response(200 if data is not None else 404)
                self.send_header('Content-Length', str(len(data or b'')))
                self.end_headers()
                self.wfile.write(data or b'')

            def do_HEAD(self):
                path = self.path.split('?')[0]
                self.send_response(200 if path in objects or path == '/papers' else 404)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), FakeS3)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        location = S3Location(f'http://127.0.0.1:{server.server_address[1]}', 'papers', 'us-east-1')
        store = S3FileStore(location, 'id', 'secret', prefix='ps/')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'a.pdf'
            source.write_bytes(b'%PDF s3')
            store.check()
            store.put('searches/s/papers/0.pdf', source, 'application/pdf')
            self.assertEqual(objects, {'/papers/ps/searches/s/papers/0.pdf': b'%PDF s3'})
            self.assertTrue(store.exists('searches/s/papers/0.pdf'))
            self.assertFalse(store.exists('searches/s/papers/1.pdf'))
            copy = Path(directory) / 'copy.pdf'
            self.assertTrue(store.fetch('searches/s/papers/0.pdf', copy))
            self.assertEqual(copy.read_bytes(), b'%PDF s3')
            self.assertFalse(store.fetch('searches/s/missing.json', copy))
            located = store.locate('searches/s/papers/0.pdf', 'Paper 0.pdf')
            self.assertIn('response-content-disposition=attachment', located.url)
            public = S3FileStore(location, 'id', 'secret', prefix='ps/', public_endpoint='https://files.example')
            self.assertTrue(
                public.locate('searches/s/papers/0.pdf', 'x').url.startswith('https://files.example/papers/ps/')
            )
            self.assertIsNone(store.locate('searches/s/papers/1.pdf', 'x'))

    def test_the_store_needs_credentials(self):
        with self.assertRaises(ValueError):
            S3FileStore(S3Location('http://x', 'b', 'r'), '', '')


class LocalFileStoreTests(unittest.TestCase):
    def test_keys_cannot_escape_the_root(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalFileStore(directory)
            for key in ('../x', '/etc/passwd', 'a//b', 'a/./b', ''):
                with self.assertRaises(ValueError):
                    store.exists(key)
            store.check()


class ConfigTests(unittest.TestCase):
    def test_service_settings_come_from_the_environment(self):
        settings = Settings.load(
            environ={
                'PAPER_SEARCH__SERVICE__PORT': '9000',
                'PAPER_SEARCH__SERVICE__WORKERS': '0',
                'PAPER_SEARCH__SERVICE__API_KEY_HASHES': 'k1:agent:abc',
                'AWS_ACCESS_KEY_ID': 'id',
            }
        )
        self.assertEqual((settings.service.port, settings.service.workers), (9000, 0))
        self.assertEqual(settings.service.api_key_hashes, ('k1:agent:abc',))
        self.assertEqual(settings.credentials.describe()['s3_access_key_id'], 'set')
        self.assertNotIn('id', repr(settings.credentials))

    def test_invalid_service_settings_are_refused(self):
        for changes in (
            {'file_store': 'ftp'},
            {'file_store': 's3'},
            {'stale_after_seconds': 1.0},
            {'api_key_hashes': ('no-colons',)},
            {'port': 0},
        ):
            with self.assertRaises(ValueError):
                ServiceConfig(**changes)


class IndexRefreshTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.settings = Settings.load(environ={'PAPER_SEARCH__DBLP_DUMP__INDEX_PATH': str(self.root / 'dblp.sqlite')})

    def tearDown(self):
        self.directory.cleanup()

    def test_an_unchanged_dump_is_not_rebuilt(self):
        import urllib.error

        (self.root / 'dblp.sqlite').write_text('index')
        downloads = self.root / 'downloads'
        downloads.mkdir()
        (downloads / 'dblp.xml.gz').write_text('dump')
        (downloads / 'dblp.xml.gz.meta.json').write_text(json.dumps({'last_modified': 'Mon, 01 Jan 2026 00:00:00 GMT'}))
        seen = []

        def opener(request, timeout):
            seen.append(request.headers)
            raise urllib.error.HTTPError(request.full_url, 304, 'Not Modified', {}, None)

        report = IndexRefresher(self.settings, downloads, opener=opener).refresh_dblp()
        self.assertFalse(report.changed)
        self.assertEqual(seen[0]['If-modified-since'], 'Mon, 01 Jan 2026 00:00:00 GMT')

    def test_a_new_dump_is_indexed_beside_the_live_index_and_swapped_in(self):
        (self.root / 'dblp.sqlite').write_text('old index')

        class Body:
            headers: ClassVar[dict[str, str]] = {'Last-Modified': 'Tue, 02 Jan 2026 00:00:00 GMT'}

            def __init__(self):
                self.data = [b'new dump', b'']

            def read(self, size=-1):
                return self.data.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def build(dump, target):
            self.assertEqual(Path(dump).read_bytes(), b'new dump')
            self.assertTrue(str(target).endswith('.building'))
            Path(target).write_text('new index')
            return mock.Mock(records=1)

        with (
            mock.patch('paper_author_searcher.service.indexes.DblpDumpIndexer') as indexer,
            mock.patch('paper_author_searcher.service.indexes.asdict', return_value={'records': 1}),
        ):
            indexer.return_value.build.side_effect = build
            report = IndexRefresher(
                self.settings, self.root / 'downloads', opener=lambda request, timeout: Body()
            ).refresh_dblp()
        self.assertTrue(report.changed)
        self.assertEqual((self.root / 'dblp.sqlite').read_text(), 'new index')
        meta = json.loads((self.root / 'downloads' / 'dblp.xml.gz.meta.json').read_text())
        self.assertEqual(meta['last_modified'], 'Tue, 02 Jan 2026 00:00:00 GMT')

    def test_unconfigured_indexes_are_skipped(self):
        report = IndexRefresher(Settings(), self.root).refresh(('dblp', 'catalogs'))
        self.assertEqual([item.changed for item in report], [False, False])


class RunnerOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.runner = WorkflowRunner(Settings(), RunnerOptions(Path('/tmp/unused')), lambda job, paper: f'/{paper}')
        self.job = Job('srch_1', 'key', REQUEST)

    def test_confirmation_required_with_and_without_candidates(self):
        identity = {'candidates': [{'name': 'A', 'author_ids': {'openalex': 'A1'}}]}
        report = {
            'run_status': 'identity_confirmation_required',
            'output_directory': '/runs/1',
            'identity': identity,
            'artifacts': {'professor': 'professor.json'},
        }
        waiting = self.runner.outcome(self.job, report)
        self.assertEqual(
            (waiting.status, waiting.identity_checkpoint), ('needs_confirmation', Path('/runs/1/professor.json'))
        )
        missing = self.runner.outcome(self.job, dict(report, identity={}))
        self.assertEqual((missing.status, missing.error['code']), ('failed', 'identity_not_found'))
        rejected = self.runner.outcome(Job('s', 'k', REQUEST, selection='openalex:A1'), report)
        self.assertEqual(rejected.error['code'], 'selection_rejected')

    def test_failed_runs_fail_the_job(self):
        outcome = self.runner.outcome(self.job, {'run_status': 'failed', 'errors': [{'error': 'source down'}]})
        self.assertEqual((outcome.status, outcome.error['details']), ('failed', ['source down']))

    def test_successful_runs_list_their_checked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / 'papers').mkdir()
            (run / 'papers' / 'a.pdf').write_bytes(b'%PDF')
            checked = {'format_status': 'valid', 'identity_status': 'verified', 'validation_status': 'verified'}
            (run / 'papers.json').write_text(
                json.dumps(
                    {
                        'collection': {
                            'papers': [
                                {'title': 'A', 'ownership_status': 'verified'},
                                {'title': 'B', 'ownership_status': 'verified'},
                            ]
                        }
                    }
                )
            )
            (run / 'manifest.json').write_text(json.dumps({'papers': [dict(checked, local_filename='a.pdf'), {}]}))
            report = {
                'run_status': 'completed',
                'output_directory': str(run),
                'artifacts': {'papers': 'papers.json', 'manifest': 'manifest.json'},
            }
            outcome = self.runner.outcome(self.job, report, cost_usd=0.02)
        self.assertEqual((outcome.status, list(outcome.files), outcome.cost_usd), ('succeeded', ['0'], 0.02))
        self.assertEqual(outcome.result['papers'][0]['full_text']['download'], '/0')

    def test_requests_become_queries(self):
        query = query_from_request(dict(REQUEST, hints={'orcid': '0000-0002-1825-0097', 'profile_urls': ['https://p']}))
        self.assertEqual(
            (query.department, query.orcid, query.profile_urls),
            ('Computer Science', '0000-0002-1825-0097', ['https://p']),
        )


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


if __name__ == '__main__':
    unittest.main()
