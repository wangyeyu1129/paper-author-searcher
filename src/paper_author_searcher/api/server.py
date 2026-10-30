"""HTTP front end (stdlib ``ThreadingHTTPServer``) and the ``paper-search-api`` command.

    paper-search-api serve --open                           # web page in the browser, no key to copy
    paper-search-api create-key --name cold-email-agent     # a key for programs; printed once
    paper-search-api serve --port 8080                      # API only; needs at least one key

Run it behind a TLS-terminating proxy: API keys travel in a header.
"""

import argparse
import json
import logging
import shutil
import sys
import webbrowser
from collections.abc import Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from paper_author_searcher.logging_config import configure_logging

from .app import ApiError, Request, Response, SearchAPI
from .auth import ApiKeys
from .backend import BackendOptions, WorkflowBackend
from .executor import JobExecutor
from .jobs import JobStore

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIRECTORY = 'output/api'


@dataclass(frozen=True)
class ServerOptions:
    host: str = '127.0.0.1'
    port: int = 8080
    data_directory: Path = Path(DEFAULT_DATA_DIRECTORY)
    keys_file: Path | None = None
    workers: int = 1
    maximum_body_bytes: int = 64 * 1024
    maximum_cost_usd: float = 0.5
    idempotency_hours: float = 24

    @property
    def keys_path(self) -> Path:
        return self.keys_file or self.data_directory / 'api_keys.json'


def handler_for(api: SearchAPI, maximum_body_bytes: int) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = 'paper-search-api'
        protocol_version = 'HTTP/1.1'

        def do_GET(self) -> None:
            self._serve()

        def do_POST(self) -> None:
            self._serve()

        def do_PUT(self) -> None:
            self._serve()

        def do_DELETE(self) -> None:
            self._serve()

        def _serve(self) -> None:
            length = int(self.headers.get('Content-Length') or 0)
            if length > maximum_body_bytes:
                self._send(
                    ApiError(413, 'body_too_large', f'Bodies are limited to {maximum_body_bytes} bytes').response()
                )
                self.close_connection = True
                return
            body = self.rfile.read(length) if length else b''
            headers = {name.lower(): value for name, value in self.headers.items()}
            self._send(api.handle(Request(self.command, self.path, headers, body)))

        def _send(self, response: Response) -> None:
            if response.file is not None:
                self.send_response(response.status)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.send_header('Content-Length', str(response.file.stat().st_size))
                self.end_headers()
                with response.file.open('rb') as handle:
                    shutil.copyfileobj(handle, self.wfile)
                return
            payload = (
                json.dumps(response.body, ensure_ascii=False).encode('utf-8') if response.body is not None else b''
            )
            self.send_response(response.status)
            headers = {'Content-Type': 'application/json; charset=utf-8', **response.headers}
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            # Paths and statuses only: request bodies and the Authorization header are never logged.
            logger.info('%s %s', self.address_string(), format % args)

    return Handler


def build(options: ServerOptions, backend: Any = None) -> tuple[SearchAPI, JobExecutor]:
    """The API and its executor over ``options.data_directory``; ``backend`` defaults to the real workflow."""
    store = JobStore(options.data_directory / 'jobs.sqlite', options.idempotency_hours)
    backend = backend or WorkflowBackend(
        BackendOptions(options.data_directory / 'runs', maximum_cost_usd=options.maximum_cost_usd)
    )
    executor = JobExecutor(store, backend, workers=options.workers)
    api = SearchAPI(store, ApiKeys.load(options.keys_path), executor.submit, backend)
    return api, executor


def serve(options: ServerOptions, open_browser: bool = False) -> None:
    api, executor = build(options)
    browser_secret = None
    if open_browser:
        # A key for this session's browser tab only: kept in memory, handed over in the URL fragment (which the
        # browser never sends to the server, so it reaches no log), gone when the server stops.
        _, browser_secret = api.keys.create('browser (this session)')
    if not api.keys.keys:
        raise SystemExit(
            f'No API keys in {options.keys_path}; create one with: paper-search-api create-key, or serve --open'
        )
    recovered = executor.recover()
    server = ThreadingHTTPServer((options.host, options.port), handler_for(api, options.maximum_body_bytes))
    if browser_secret is not None:
        address = f'http://{options.host}:{server.server_address[1]}/'
        print(f'Open {address} (this browser tab is signed in for this session)', flush=True)
        webbrowser.open(address + '#key=' + browser_secret)
    logger.warning(
        'Serving on http://%s:%s with %s worker(s); %s unfinished search(es) queued again',
        options.host,
        server.server_address[1],
        options.workers,
        recovered,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        executor.shutdown(wait=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Asynchronous paper-search API')
    parser.add_argument('--data-dir', default=DEFAULT_DATA_DIRECTORY, help='Jobs database, runs and keys')
    parser.add_argument('--keys-file', help='API keys file (default: DATA_DIR/api_keys.json)')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('serve', help='Run the HTTP server')
    run.add_argument('--host', default='127.0.0.1')
    run.add_argument('--port', type=int, default=8080)
    run.add_argument('--workers', type=int, default=1, help='Searches run at once (each paces its own requests)')
    run.add_argument('--max-cost-usd', type=float, default=0.5, help='Metered-API spend cap per search')
    run.add_argument('--open', action='store_true', help='Open the web page in a browser, signed in for this session')
    run.add_argument('--verbose', action='store_true')
    create = commands.add_parser('create-key', help='Create an API key and print it once')
    create.add_argument('--name', required=True)
    revoke = commands.add_parser('revoke-key', help='Revoke an API key by its ID')
    revoke.add_argument('key_id')
    args = parser.parse_args(argv)
    keys_file = Path(args.keys_file) if args.keys_file else None
    options = ServerOptions(data_directory=Path(args.data_dir), keys_file=keys_file)
    if args.command == 'create-key':
        keys = ApiKeys.load(options.keys_path)
        key, secret = keys.create(args.name)
        keys.save(options.keys_path)
        print(f'Created {key.id} ({key.name}). Send it as "Authorization: Bearer <key>"; it is not shown again:')
        print(secret)
        return 0
    if args.command == 'revoke-key':
        keys = ApiKeys.load(options.keys_path)
        if not keys.revoke(args.key_id):
            print(f'No key {args.key_id}', file=sys.stderr)
            return 1
        keys.save(options.keys_path)
        print(f'Revoked {args.key_id}; restart the server for it to take effect')
        return 0
    if args.workers < 1 or args.max_cost_usd <= 0:
        parser.error('--workers and --max-cost-usd must be positive')
    configure_logging('INFO' if args.verbose else None)
    serve(
        ServerOptions(
            host=args.host,
            port=args.port,
            data_directory=options.data_directory,
            keys_file=keys_file,
            workers=args.workers,
            maximum_cost_usd=args.max_cost_usd,
        ),
        open_browser=args.open,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
