"""Stands in for every paced source during the load test and records when each request arrived.

Answers empty but valid responses. Once the source named by ``THROTTLE_HOST`` has served ``THROTTLE_AFTER`` requests it
answers one 429 with ``Retry-After: THROTTLE_SECONDS``, so the log shows whether every worker then backed off.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get('REQUEST_LOG', '/out/requests.jsonl')
THROTTLE_HOST = os.environ.get('THROTTLE_HOST', '')
THROTTLE_AFTER = int(os.environ.get('THROTTLE_AFTER', '0'))
THROTTLE_SECONDS = int(os.environ.get('THROTTLE_SECONDS', '20'))
# Metered sources must answer slowly enough to overlap, as the real ones do.
ANSWER_SECONDS = float(os.environ.get('ANSWER_SECONDS', '0.02'))

ATOM = (
    '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
    'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"><opensearch:totalResults>0</opensearch:totalResults></feed>'
)
EMPTY = json.dumps({'results': [], 'data': [], 'meta': {'count': 0, 'next_cursor': None}, 'total': 0, 'offset': 0})

lock = threading.Lock()
served: dict[str, int] = {}
throttled: set[str] = set()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self) -> None:
        self._answer()

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self._answer()

    def _answer(self) -> None:
        host = (self.headers.get('Host') or '').split(':')[0]
        moment = time.time()
        with lock:
            served[host] = served.get(host, 0) + 1
            throttle = host == THROTTLE_HOST and served[host] > THROTTLE_AFTER and host not in throttled
            if throttle:
                throttled.add(host)
            with open(LOG, 'a', encoding='utf-8') as handle:
                handle.write(
                    json.dumps({'t': moment, 'host': host, 'throttled': throttle, 'path': self.path[:60]}) + '\n'
                )
        time.sleep(ANSWER_SECONDS)
        if throttle:
            self._send(429, 'application/json', '{}', {'Retry-After': str(THROTTLE_SECONDS)})
        elif 'arxiv' in host:
            self._send(200, 'application/atom+xml', ATOM)
        else:
            self._send(200, 'application/json', EMPTY)

    def _send(self, status: int, kind: str, body: str, headers: dict[str, str] | None = None) -> None:
        payload = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
