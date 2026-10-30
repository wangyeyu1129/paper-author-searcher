"""The search API's routes, independent of any HTTP server: ``SearchAPI.handle(request) -> response``.

POST /v1/searches                        submit a search; 202 with the job (status ``queued``)
GET  /v1/searches/{id}                   the job: status, progress, candidates, result
POST /v1/searches/{id}/confirmation      pick a candidate of a ``needs_confirmation`` job; it runs again
GET  /v1/searches/{id}/papers/{pid}/pdf  a returned paper's checked PDF
GET  /v1/schemas/{name}                  the request and response JSON Schemas (no key needed)
GET  /v1/health                          liveness (no key needed)

Every other call needs ``Authorization: Bearer <key>``. POSTs accept ``Idempotency-Key``: a retry with the same
key and body gets the first response again instead of a second job.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .auth import ApiKey, ApiKeys
from .backend import SearchBackend
from .jobs import IdempotencyConflict, Job, JobStatus, JobStore
from .results import selections
from .schema import API_VERSION, SCHEMA_NAMES, schema, validator

MAXIMUM_IDEMPOTENCY_KEY = 255


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
    # A file to stream instead of a JSON body.
    file: Path | None = None


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
    def __init__(self, store: JobStore, keys: ApiKeys, submit: Callable[[str], None], backend: SearchBackend):
        self.store = store
        self.keys = keys
        # Queues a job for the executor (JobExecutor.submit).
        self.submit = submit
        self.backend = backend
        self.routes: list[tuple[str, re.Pattern[str], Route]] = [
            ('POST', re.compile(r'^/v1/searches$'), self._create),
            ('GET', re.compile(r'^/v1/searches/(?P<id>[\w-]+)$'), self._show),
            ('POST', re.compile(r'^/v1/searches/(?P<id>[\w-]+)/confirmation$'), self._confirm),
            ('GET', re.compile(r'^/v1/searches/(?P<id>[\w-]+)/papers/(?P<paper>\d+)/pdf$'), self._pdf),
        ]

    def handle(self, request: Request) -> Response:
        try:
            return self._dispatch(request)
        except ApiError as error:
            return error.response()

    def _dispatch(self, request: Request) -> Response:
        path = request.path.split('?', 1)[0].rstrip('/') or '/'
        if request.method == 'GET' and path == '/v1/health':
            return Response(200, {'api_version': API_VERSION, 'status': 'ok'})
        if request.method == 'GET' and path.startswith('/v1/schemas/'):
            return self._schema(path.removeprefix('/v1/schemas/').removesuffix('.json'))
        allowed = []
        for method, pattern, route in self.routes:
            match = pattern.match(path)
            if not match:
                continue
            if method != request.method:
                allowed.append(method)
                continue
            key = self.keys.authenticate(request.headers)
            if key is None:
                raise ApiError(401, 'unauthorized', 'A valid API key is required (Authorization: Bearer <key>)')
            return route(request, key, match.groupdict())
        if allowed:
            raise ApiError(405, 'method_not_allowed', f'Use {" or ".join(allowed)} here')
        raise ApiError(404, 'not_found', 'No such endpoint')

    def _schema(self, name: str) -> Response:
        if name not in SCHEMA_NAMES:
            raise ApiError(404, 'not_found', f'Schemas: {", ".join(SCHEMA_NAMES)}')
        return Response(200, schema(name), {'Content-Type': 'application/schema+json'})

    def _create(self, request: Request, key: ApiKey, _: dict[str, str]) -> Response:
        body = self._body(request, 'search-request')

        def create() -> Response:
            job = self.store.create(Job(JobStore.new_id(), key.id, body))
            self.submit(job.id)
            return Response(202, self.resource(job), {'Location': self._link(job)})

        return self._idempotent(request, key, create)

    def _show(self, _: Request, key: ApiKey, params: dict[str, str]) -> Response:
        return Response(200, self.resource(self._job(key, params['id'])))

    def _confirm(self, request: Request, key: ApiKey, params: dict[str, str]) -> Response:
        body = self._body(request, 'confirmation-request')

        def confirm() -> Response:
            job = self._job(key, params['id'])
            if job.status != JobStatus.NEEDS_CONFIRMATION:
                raise ApiError(409, 'not_awaiting_confirmation', f'The search is {job.status}')
            if body['selection'] not in selections(job.candidates or []):
                raise ApiError(
                    422,
                    'unknown_selection',
                    "Pick one of the candidates' selections",
                    sorted(selections(job.candidates or [])),
                )
            queued = self.store.transition(
                job.id, JobStatus.NEEDS_CONFIRMATION, status=JobStatus.QUEUED, selection=body['selection'], error=None
            )
            if queued is None:
                raise ApiError(409, 'not_awaiting_confirmation', 'The search was confirmed by another request')
            job = queued
            self.submit(job.id)
            return Response(202, self.resource(job), {'Location': self._link(job)})

        return self._idempotent(request, key, confirm)

    def _pdf(self, _: Request, key: ApiKey, params: dict[str, str]) -> Response:
        job = self._job(key, params['id'])
        path = self.backend.file_path(job, params['paper']) if job.status == JobStatus.SUCCEEDED else None
        if path is None:
            raise ApiError(404, 'not_found', 'This search has no checked PDF for that paper')
        return Response(200, None, {'Content-Type': 'application/pdf'}, file=path)

    def _job(self, key: ApiKey, job_id: str) -> Job:
        job = self.store.get(job_id)
        # Another caller's job is reported as missing, not as forbidden: its existence is not theirs to know.
        if job is None or job.client != key.id:
            raise ApiError(404, 'not_found', 'No such search')
        return job

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

    def _idempotent(self, request: Request, key: ApiKey, action: Callable[[], Response]) -> Response:
        token = request.headers.get('idempotency-key')
        if token is None:
            return action()
        if not 0 < len(token) <= MAXIMUM_IDEMPOTENCY_KEY:
            raise ApiError(
                400, 'invalid_idempotency_key', f'Idempotency-Key must be 1-{MAXIMUM_IDEMPOTENCY_KEY} characters'
            )
        fingerprint = hashlib.sha256(f'{request.method} {request.path}\n'.encode() + request.body).hexdigest()
        try:
            stored = self.store.reserve(key.id, token, fingerprint)
        except IdempotencyConflict as conflict:
            if conflict.reason == 'reused':
                raise ApiError(
                    422, 'idempotency_key_reused', 'This Idempotency-Key was used for a different request'
                ) from conflict
            raise ApiError(
                409, 'idempotency_in_progress', 'The first request with this key is still running'
            ) from conflict
        if stored is not None:
            return Response(stored.status or 200, stored.body, {'Idempotent-Replayed': 'true'})
        try:
            response = action()
        except ApiError as error:
            # Refused before any state changed: a corrected retry may reuse the key.
            self.store.release(key.id, token)
            raise error
        self.store.remember(key.id, token, response.status, response.body or {})
        return response

    def resource(self, job: Job) -> dict[str, Any]:
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
            'links': {'self': self._link(job)},
        }
        if job.candidates:
            body['candidates'] = job.candidates
        if job.status == JobStatus.NEEDS_CONFIRMATION:
            body['links']['confirmation'] = self._link(job) + '/confirmation'
        if job.status == JobStatus.SUCCEEDED and job.result is not None:
            body['result'] = job.result
        if job.status == JobStatus.FAILED and job.error is not None:
            body['error'] = job.error
        return body

    @staticmethod
    def _link(job: Job) -> str:
        return f'/v1/searches/{job.id}'
