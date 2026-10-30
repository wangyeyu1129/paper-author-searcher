"""HTTP adapter of the search service: routes, authentication, schema checks and status codes.

GET  /                                   web page: enter a name and school, get the PDFs (no key needed to load)
POST /v1/searches                        submit a search; 202 with the job (status ``queued``)
GET  /v1/searches/{id}                   the job: status, progress, candidates, result
POST /v1/searches/{id}/confirmation      pick a candidate of a ``needs_confirmation`` job; it runs again
GET  /v1/searches/{id}/papers/{pid}/pdf  a returned paper's checked PDF (streamed, or a redirect to storage)
GET  /v1/searches/{id}/pdfs.zip          every checked PDF of the result in one zip
GET  /v1/searches/{id}/agent             what the cold-email agent may cite, and what it must not (agent-result)
GET  /v1/schemas/{name}                  the request and response JSON Schemas (no key needed)
GET  /v1/health                          liveness: the process answers (no key needed)
GET  /v1/ready                           readiness: database, file store, workers, indexes (no key needed)
GET  /metrics                            Prometheus metrics (no key needed; keep it off the public network)

Every other call needs ``Authorization: Bearer <key>``. POSTs accept ``Idempotency-Key``: a retry with the same
key and body acts on the same job instead of creating another. All behaviour lives in ``SearchService``; this
module only translates between HTTP and it.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from paper_author_searcher.service.facade import SearchService
from paper_author_searcher.service.metrics import RequestCounter, render
from paper_author_searcher.service.model import ErrorKind, Job, JobStatus, ServiceError
from paper_author_searcher.service.ports import StoredFile
from paper_author_searcher.service.results import ResultView

from .auth import ApiKey, ApiKeys
from .schema import API_VERSION, SCHEMA_NAMES, schema, validator

logger = logging.getLogger(__name__)

MAXIMUM_IDEMPOTENCY_KEY = 255
STATUS_OF: dict[str, int] = {
    ErrorKind.INVALID: 422,
    ErrorKind.NOT_FOUND: 404,
    ErrorKind.CONFLICT: 409,
    ErrorKind.IDEMPOTENCY_REUSED: 422,
    ErrorKind.IDEMPOTENCY_IN_PROGRESS: 409,
}


def page() -> Path:
    """The web page (static/index.html): a form over this API that needs no tools beyond a browser."""
    return Path(str(resources.files(__package__).joinpath('static').joinpath('index.html')))


def download_link(job_id: str, paper_id: str) -> str:
    """Where a result tells callers to fetch a paper's checked PDF (the route below)."""
    return f'/v1/searches/{job_id}/papers/{paper_id}/pdf'


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    # Header names in lower case.
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b''


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # A file to stream, or plain text, instead of a JSON body.
    file: Path | None = None
    text: str | None = None


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or []

    def response(self) -> Response:
        error: dict[str, Any] = {'code': self.code, 'message': self.message}
        if self.details:
            error['details'] = self.details
        headers = {'WWW-Authenticate': 'Bearer'} if self.status == 401 else {}
        return Response(self.status, {'api_version': API_VERSION, 'error': error}, headers)


Route = Callable[[Request, ApiKey, dict[str, str]], Response]


class SearchAPI:
    def __init__(self, service: SearchService, keys: ApiKeys, requests: RequestCounter | None = None) -> None:
        self.service = service
        self.keys = keys
        self.requests = requests or RequestCounter()
        self.public: dict[str, Callable[[], Response]] = {
            '/': lambda: Response(200, None, {'Content-Type': 'text/html; charset=utf-8'}, file=page()),
            '/v1/health': lambda: Response(200, {'api_version': API_VERSION, 'status': 'ok'}),
            '/v1/ready': self._ready,
            '/metrics': self._metrics,
        }
        self.routes: list[tuple[str, str, re.Pattern[str], Route]] = [
            ('POST', 'create', re.compile(r'^/v1/searches$'), self._create),
            ('GET', 'show', re.compile(r'^/v1/searches/(?P<id>[\w-]+)$'), self._show),
            ('POST', 'confirm', re.compile(r'^/v1/searches/(?P<id>[\w-]+)/confirmation$'), self._confirm),
            ('GET', 'pdf', re.compile(r'^/v1/searches/(?P<id>[\w-]+)/papers/(?P<paper>\d+)/pdf$'), self._pdf),
            ('GET', 'zip', re.compile(r'^/v1/searches/(?P<id>[\w-]+)/pdfs\.zip$'), self._archive),
            ('GET', 'agent', re.compile(r'^/v1/searches/(?P<id>[\w-]+)/agent$'), self._agent),
        ]

    def handle(self, request: Request) -> Response:
        route = 'other'
        try:
            route, response = self._dispatch(request)
        except ApiError as error:
            response = error.response()
        except ServiceError as error:
            response = ApiError(STATUS_OF.get(error.kind, 400), error.code, error.message, error.details).response()
        except Exception:
            logger.exception('Unhandled error for %s %s', request.method, request.path.split('?', 1)[0])
            response = ApiError(500, 'internal_error', 'The server could not answer this request').response()
        self.requests.add(route, response.status)
        return response

    def _dispatch(self, request: Request) -> tuple[str, Response]:
        path = request.path.split('?', 1)[0].rstrip('/') or '/'
        if request.method == 'GET' and path == '/index.html':
            path = '/'
        if request.method == 'GET' and path in self.public:
            return path, self.public[path]()
        if request.method == 'GET' and path.startswith('/v1/schemas/'):
            return 'schema', self._schema(path.removeprefix('/v1/schemas/').removesuffix('.json'))
        allowed = []
        for method, name, pattern, route in self.routes:
            match = pattern.match(path)
            if not match:
                continue
            if method != request.method:
                allowed.append(method)
                continue
            key = self.keys.authenticate(request.headers)
            if key is None:
                raise ApiError(401, 'unauthorized', 'A valid API key is required (Authorization: Bearer <key>)')
            return name, route(request, key, match.groupdict())
        if allowed:
            raise ApiError(405, 'method_not_allowed', f'Use {" or ".join(allowed)} here')
        raise ApiError(404, 'not_found', 'No such endpoint')

    # Public routes

    def _schema(self, name: str) -> Response:
        if name not in SCHEMA_NAMES:
            raise ApiError(404, 'not_found', f'Schemas: {", ".join(SCHEMA_NAMES)}')
        return Response(200, schema(name), {'Content-Type': 'application/schema+json'})

    def _ready(self) -> Response:
        checks = self.service.readiness()
        ready = all(check.ok for check in checks)
        body = {
            'api_version': API_VERSION,
            'status': 'ready' if ready else 'not_ready',
            'checks': {check.name: {'ok': check.ok, 'detail': check.detail} for check in checks},
        }
        return Response(200 if ready else 503, body)

    def _metrics(self) -> Response:
        return Response(
            200,
            None,
            {'Content-Type': 'text/plain; version=0.0.4; charset=utf-8'},
            text=render(self.service.snapshot(), self.requests),
        )

    # Search routes

    def _create(self, request: Request, key: ApiKey, _: dict[str, str]) -> Response:
        body = self._body(request, 'search-request')
        submission = self.service.submit(key.id, body, self._idempotency_key(request))
        return self._accepted(submission.job, submission.replayed)

    def _show(self, _: Request, key: ApiKey, params: dict[str, str]) -> Response:
        return Response(200, resource(self.service.job(key.id, params['id'])))

    def _confirm(self, request: Request, key: ApiKey, params: dict[str, str]) -> Response:
        body = self._body(request, 'confirmation-request')
        submission = self.service.confirm(key.id, params['id'], body['selection'], self._idempotency_key(request))
        return self._accepted(submission.job, submission.replayed)

    def _agent(self, _: Request, key: ApiKey, params: dict[str, str]) -> Response:
        return Response(200, self.service.agent_view(key.id, params['id']))

    def _pdf(self, _: Request, key: ApiKey, params: dict[str, str]) -> Response:
        return self._stored(self.service.paper_file(key.id, params['id'], params['paper']), 'application/pdf', None)

    def _archive(self, _: Request, key: ApiKey, params: dict[str, str]) -> Response:
        stored = self.service.archive(key.id, params['id'])
        return self._stored(stored, 'application/zip', f'attachment; filename="{params["id"]}-pdfs.zip"')

    # Helpers

    @staticmethod
    def _stored(stored: StoredFile, content_type: str, disposition: str | None) -> Response:
        if stored.url is not None:
            return Response(302, None, {'Location': stored.url, 'Cache-Control': 'no-store'})
        headers = {'Content-Type': content_type, **({'Content-Disposition': disposition} if disposition else {})}
        return Response(200, None, headers, file=stored.path)

    @staticmethod
    def _accepted(job: Job, replayed: bool) -> Response:
        headers = {'Location': link(job)}
        if replayed:
            headers['Idempotent-Replayed'] = 'true'
        return Response(202, resource(job), headers)

    @staticmethod
    def _idempotency_key(request: Request) -> str | None:
        token = request.headers.get('idempotency-key')
        if token is not None and not 0 < len(token) <= MAXIMUM_IDEMPOTENCY_KEY:
            raise ApiError(
                400, 'invalid_idempotency_key', f'Idempotency-Key must be 1-{MAXIMUM_IDEMPOTENCY_KEY} characters'
            )
        return token

    @staticmethod
    def _body(request: Request, schema_name: str) -> dict[str, Any]:
        try:
            body = json.loads(request.body.decode('utf-8') or 'null')
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, 'invalid_json', 'The body must be JSON', [str(exc)]) from exc
        errors = validator(schema_name).errors(body)
        if errors:
            raise ApiError(422, 'invalid_request', f'The body does not match the {schema_name} schema', errors)
        return body


def link(job: Job) -> str:
    return f'/v1/searches/{job.id}'


def resource(job: Job) -> dict[str, Any]:
    """The job as schema v1 ``search``."""
    body: dict[str, Any] = {
        'api_version': API_VERSION,
        'id': job.id,
        'status': job.status,
        'created_at': job.created_at,
        'updated_at': job.updated_at,
        'request': job.request,
        'selection': job.selection,
        'progress': job.progress if job.status in (JobStatus.QUEUED, JobStatus.RUNNING) else None,
        'links': {'self': link(job)},
    }
    if job.candidates:
        body['candidates'] = job.candidates
    if job.status == JobStatus.NEEDS_CONFIRMATION:
        body['links']['confirmation'] = link(job) + '/confirmation'
    if job.status == JobStatus.SUCCEEDED and job.result is not None:
        body['result'] = ResultView.visible(job.result, bool(job.request.get('include_needs_review')))
    if job.status == JobStatus.FAILED and job.error is not None:
        body['error'] = job.error
    return body
