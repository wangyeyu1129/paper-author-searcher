"""HTTP front end (stdlib ``ThreadingHTTPServer``) and the ``paper-search-api`` command.

    paper-search-api serve --open             web page in the browser, signed in for this session
    paper-search-api serve                    API (and, with service.workers > 0, workers) in one process
    paper-search-api worker --threads 2       a separate worker process (serve with --workers 0)
    paper-search-api create-key --name agent  a key for programs; printed once
    paper-search-api refresh-indexes --every-hours 168
    paper-search-api backup --output /backups --keep 14 --every-hours 24
    paper-search-api restore /backups/paper-search-backup-....tar.gz

Configuration is ``Settings``: ``PAPER_SEARCH__SERVICE__<FIELD>`` and the other sections, or ``--settings``; the
flags below override single fields. Run it behind a TLS-terminating proxy: API keys travel in a header.
"""

import argparse
import json
import logging
import shutil
import signal
import sys
import threading
import webbrowser
from collections.abc import Sequence
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from paper_author_searcher.logging_config import add_logging_arguments, configure_from_arguments
from paper_author_searcher.service.assembly import Deployment, assemble, file_store
from paper_author_searcher.service.backup import Backups, backup_forever
from paper_author_searcher.service.indexes import IndexRefresher, refresh_forever
from paper_author_searcher.settings import Settings, add_settings_argument, load_settings

from .app import ApiError, Request, Response, SearchAPI, download_link
from .auth import ApiKeys

logger = logging.getLogger(__name__)


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
            self.send_response(response.status)
            if response.file is not None:
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.send_header('Content-Length', str(response.file.stat().st_size))
                self.end_headers()
                with response.file.open('rb') as handle:
                    shutil.copyfileobj(handle, self.wfile)
                return
            if response.text is not None:
                payload = response.text.encode('utf-8')
                headers = response.headers
            else:
                payload = (
                    json.dumps(response.body, ensure_ascii=False).encode('utf-8') if response.body is not None else b''
                )
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


def keys_path(settings: Settings) -> Path:
    service = settings.service
    return Path(service.keys_file) if service.keys_file else Path(service.data_directory) / 'api_keys.json'


def build(settings: Settings, workers: int | None = None, **overrides: Any) -> tuple[SearchAPI, Deployment]:
    """The HTTP API over a deployment with ``workers`` in-process workers (default: service.workers)."""
    count = settings.service.workers if workers is None else workers
    deployment = assemble(settings, count, download_link, **overrides)
    keys = ApiKeys.from_sources(keys_path(settings), settings.service.api_key_hashes)
    return SearchAPI(deployment.service, keys), deployment


def serve(settings: Settings, open_browser: bool = False) -> None:
    api, deployment = build(settings)
    browser_secret = None
    if open_browser:
        # A key for this session's browser tab only: kept in memory, handed over in the URL fragment (which the
        # browser never sends to the server, so it reaches no log), gone when the server stops.
        _, browser_secret = api.keys.create('browser (this session)')
    if not api.keys.keys:
        raise SystemExit(
            f'No API keys in {keys_path(settings)} or service.api_key_hashes; '
            'create one with: paper-search-api create-key, or use serve --open'
        )
    service = settings.service
    server = ThreadingHTTPServer((service.host, service.port), handler_for(api, service.maximum_body_bytes))
    deployment.start_workers()
    logger.warning(
        'Serving on http://%s:%s with %s in-process worker(s)', service.host, server.server_address[1], service.workers
    )
    if browser_secret is not None:
        address = f'http://{service.host}:{server.server_address[1]}/'
        print(f'Open {address} (this browser tab is signed in for this session)', flush=True)
        webbrowser.open(address + '#key=' + browser_secret)
    # SIGTERM (docker stop) shuts down like Ctrl-C: stop accepting requests, let running searches be requeued.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        deployment.stop(timeout=5)


def work(settings: Settings, threads: int) -> None:
    """Run ``threads`` workers in this process until SIGTERM or Ctrl-C (a running search is let finish)."""
    _, deployment = build(settings, workers=threads)
    stopped = threading.Event()

    def stop(*_: Any) -> None:
        stopped.set()
        for worker in deployment.workers:
            worker.stop()

    signal.signal(signal.SIGTERM, stop)
    deployment.start_workers()
    logger.warning('%s worker thread(s) running', threads)
    try:
        while not stopped.wait(1):
            if not any(thread.is_alive() for thread in deployment.threads):
                break
    except KeyboardInterrupt:
        stop()
    deployment.stop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Asynchronous paper-search service')
    add_settings_argument(parser)
    add_logging_arguments(parser)
    parser.add_argument('--data-dir', help='service.data_directory: jobs database, files, runs, keys')
    parser.add_argument('--keys-file', help='service.keys_file (default: DATA_DIR/api_keys.json)')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('serve', help='Run the HTTP API (and in-process workers)')
    run.add_argument('--host')
    run.add_argument('--port', type=int)
    run.add_argument('--workers', type=int, help='In-process worker threads; 0 = separate worker processes only')
    run.add_argument('--max-cost-usd', type=float, help='Metered-API spend cap per search')
    run.add_argument('--open', action='store_true', help='Open the web page in a browser, signed in for this session')
    worker = commands.add_parser('worker', help='Run searches from the queue (scale by running more of these)')
    worker.add_argument('--threads', type=int, default=1)
    create = commands.add_parser('create-key', help='Create an API key and print it once')
    create.add_argument('--name', required=True)
    revoke = commands.add_parser('revoke-key', help='Revoke an API key by its ID')
    revoke.add_argument('key_id')
    commands.add_parser('create-bucket', help='Create the S3 bucket of service.file_store = s3 (if missing)')
    refresh = commands.add_parser('refresh-indexes', help='Download and rebuild the DBLP index and catalogs')
    refresh.add_argument('--only', choices=('dblp', 'catalogs'), action='append', help='Refresh only this index')
    refresh.add_argument('--every-hours', type=float, help='Keep running and refresh this often')
    backup = commands.add_parser('backup', help='Archive the jobs database, API keys and local files')
    backup.add_argument('--output', required=True, type=Path, help='Directory for the archives')
    backup.add_argument('--keep', type=int, default=0, help='Keep only this many newest archives (0: all)')
    backup.add_argument('--every-hours', type=float, help='Keep running and back up this often')
    restore = commands.add_parser('restore', help='Replace the data with an archive; stop the service first')
    restore.add_argument('archive', type=Path)
    restore.add_argument('--force', action='store_true', help='Replace a data directory that already holds jobs')
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    settings = load_settings(args)
    overrides = {
        'data_directory': args.data_dir,
        'keys_file': args.keys_file,
        'host': getattr(args, 'host', None),
        'port': getattr(args, 'port', None),
        'workers': getattr(args, 'workers', None),
        'maximum_cost_usd': getattr(args, 'max_cost_usd', None),
    }
    chosen = {name: value for name, value in overrides.items() if value is not None}
    settings = replace(settings, service=replace(settings.service, **chosen))
    if args.command == 'create-key':
        keys = ApiKeys.load(keys_path(settings))
        key, secret = keys.create(args.name)
        keys.save(keys_path(settings))
        print(f'Created {key.id} ({key.name}). Send it as "Authorization: Bearer <key>"; it is not shown again:')
        print(secret)
        print(f'For PAPER_SEARCH__SERVICE__API_KEY_HASHES: {key.id}:{key.name}:{key.sha256}', file=sys.stderr)
        return 0
    if args.command == 'revoke-key':
        keys = ApiKeys.load(keys_path(settings))
        if not keys.revoke(args.key_id):
            print(f'No key {args.key_id}', file=sys.stderr)
            return 1
        keys.save(keys_path(settings))
        print(f'Revoked {args.key_id}; restart the server for it to take effect')
        return 0
    if args.command == 'create-bucket':
        created = file_store(settings).create_bucket()
        print('Bucket created' if created else 'Nothing to create (bucket exists, or the store is local)')
        return 0
    if args.command == 'refresh-indexes':
        which = tuple(args.only or ('dblp', 'catalogs'))
        refresher = IndexRefresher(settings, Path(settings.service.data_directory) / 'downloads')
        if args.every_hours:
            refresh_forever(refresher, args.every_hours, which)
            return 0
        print(json.dumps([vars(report) for report in refresher.refresh(which)], indent=2, default=str))
        return 0
    if args.command in ('backup', 'restore'):
        backups = Backups(
            Path(settings.service.data_directory), keys_path(settings), settings.service.file_store == 'local'
        )
        if args.command == 'restore':
            print(f'Restored {backups.restore(args.archive, args.force)} files from {args.archive}')
            return 0
        if args.every_hours:
            backup_forever(backups, args.output, args.keep, args.every_hours)
            return 0
        print(backups.create(args.output, args.keep))
        return 0
    if args.command == 'worker':
        if args.threads < 1:
            parser.error('--threads must be positive')
        work(settings, args.threads)
        return 0
    serve(settings, open_browser=args.open)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
