"""Bounded HTTP requests with verified TLS and explicit format diagnostics."""
import json
import hashlib
import ipaddress
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from .config import DEFAULT_HTTP
from .network_safety import secure_download_opener


class SourceError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def require_public_http_url(url):
    if not isinstance(url, str) or any(ord(character) <= 32 for character in url):
        raise SourceError('Malformed resource URL')
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise SourceError('Resource URL must be public HTTP(S) without credentials')
    hostname = parsed.hostname.lower()
    if hostname == 'localhost' or hostname.endswith(('.localhost', '.local')):
        raise SourceError('Local resource URLs are not supported')
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise SourceError('Nonpublic resource addresses are not supported')


class PublicRedirectHandler(HTTPRedirectHandler):
    def __init__(self, maximum_redirects):
        self.max_redirections = maximum_redirects
        self.max_repeats = maximum_redirects

    def redirect_request(self, request, response, code, message, headers, new_url):
        require_public_http_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


@dataclass
class URLProbe:
    url: str
    status: int
    content_type: str
    headers: dict
    prefix: bytes


def has_pdf_signature(prefix):
    return prefix.lstrip().startswith(b'%PDF-')


@dataclass
class DownloadReceipt:
    final_url: str
    status: int
    content_type: str
    file_size: int
    sha256: str


class HttpClient:
    def __init__(self, timeout=None, retries=None, config=DEFAULT_HTTP):
        overrides = {}
        if timeout is not None:
            overrides["timeout"] = timeout
        if retries is not None:
            overrides["retries"] = retries
        self.config = replace(config, **overrides)
        self.timeout = self.config.timeout
        self.retries = self.config.retries

    def get(self, url, params=None, headers=None, xml=False):
        request = self._request(url, params, headers, xml)
        return self._with_retries(request, lambda: self._read_response(request, xml))

    def _with_retries(self, request, read_response):
        for attempt in range(self.retries + 1):
            try:
                return read_response()
            except HTTPError as exc:
                retryable = exc.code in self.config.retry_statuses
                if not retryable or attempt == self.retries:
                    message = "HTTP {} from {}".format(exc.code, request.host)
                    raise SourceError(message, status_code=exc.code) from exc
                time.sleep(self._retry_delay(exc, attempt))
            except (OSError, HTTPException) as exc:
                if attempt == self.retries:
                    raise SourceError("{}: {}".format(request.host, exc)) from exc
                delay = min(self.config.retry_backoff_base ** attempt, self.config.maximum_retry_delay)
                time.sleep(delay)
            except (ValueError, ET.ParseError) as exc:
                raise SourceError("{}: {}".format(request.host, exc)) from exc

    def probe(self, url, maximum_bytes=1024, html_bytes=0, maximum_redirects=5):
        """Read a bounded prefix, even when a server ignores Range; never save a body."""
        require_public_http_url(url)
        if maximum_bytes < 1 or html_bytes < 0:
            raise ValueError('Probe limits must be positive')
        request = self._request(url, headers={
            'Range': 'bytes=0-{}'.format(max(maximum_bytes, html_bytes) - 1),
            'Accept': 'application/pdf,text/html;q=0.8,*/*;q=0.1',
            'Accept-Encoding': 'identity',
        }, params=None, xml=False)
        opener = build_opener(PublicRedirectHandler(maximum_redirects))

        def read_prefix():
            with opener.open(request, timeout=self.timeout) as response:
                content_type = response.headers.get('Content-Type', '').lower().split(';', 1)[0]
                limit = maximum_bytes
                if html_bytes and content_type in ('text/html', 'application/xhtml+xml'):
                    limit = html_bytes
                prefix = response.read(limit)
                final_url = response.geturl()
                require_public_http_url(final_url)
                return URLProbe(final_url, response.status, content_type,
                                dict(response.headers.items()), prefix)

        return self._with_retries(request, read_prefix)

    def stream_pdf(self, url, destination, config):
        """Restart failed transfers from byte zero; validate public DNS on every connection."""
        require_public_http_url(url)
        request = self._request(url, params=None, headers={
            'Accept': 'application/pdf,application/octet-stream;q=0.8',
            'Accept-Encoding': 'identity',
        }, xml=False)
        opener = secure_download_opener(PublicRedirectHandler(config.maximum_redirects), config.allowed_ports)

        def transfer():
            deadline = time.monotonic() + config.maximum_transfer_seconds
            with opener.open(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                require_public_http_url(final_url)
                content_type = response.headers.get('Content-Type', '').lower().split(';', 1)[0]
                expected_size = self._download_size(response, content_type, config)
                digest = hashlib.sha256()
                size = 0
                prefix = bytearray()
                signature_confirmed = False
                read_block = getattr(response, 'read1', response.read)
                with open(destination, 'wb') as handle:
                    while True:
                        if time.monotonic() > deadline:
                            raise SourceError('Download exceeded the configured total transfer deadline')
                        block = read_block(min(config.chunk_bytes, config.maximum_file_bytes - size + 1))
                        if not block:
                            break
                        if not signature_confirmed:
                            prefix.extend(block[:max(0, 1024 - len(prefix))])
                            signature_confirmed = has_pdf_signature(prefix)
                            if len(prefix) >= 1024 and not signature_confirmed:
                                raise SourceError('Download does not begin with a PDF file signature')
                        size += len(block)
                        if size > config.maximum_file_bytes:
                            raise SourceError('Download exceeds the configured file size limit')
                        handle.write(block)
                        digest.update(block)
                    handle.flush()
                    os.fsync(handle.fileno())
                if not signature_confirmed:
                    raise SourceError('Download does not begin with a PDF file signature')
                if not size or (expected_size is not None and size != expected_size):
                    raise OSError('Downloaded size does not match the complete HTTP response')
                return DownloadReceipt(final_url, response.status, content_type, size, digest.hexdigest())

        return self._with_retries(request, transfer)

    def _download_size(self, response, content_type, config):
        if response.status not in (200, 206):
            raise SourceError('Unexpected download HTTP {}'.format(response.status), response.status)
        if content_type in ('text/html', 'application/xhtml+xml'):
            raise SourceError('Download is an HTML page, not a PDF')
        encoding = response.headers.get('Content-Encoding', 'identity').lower()
        if encoding not in ('', 'identity'):
            raise SourceError('Unexpected compressed HTTP response')
        length = response.headers.get('Content-Length')
        expected = None
        if length is not None:
            if not length.isdigit():
                raise SourceError('Invalid Content-Length')
            expected = int(length)
        if response.status == 206:
            match = re.fullmatch(r'bytes 0-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if not match or int(match.group(1)) + 1 != int(match.group(2)):
                raise SourceError('Unexpected partial download; a complete response is required')
            total = int(match.group(2))
            if expected is not None and expected != total:
                raise SourceError('Content-Length conflicts with Content-Range')
            expected = total
        if expected is not None and expected > config.maximum_file_bytes:
            raise SourceError('Declared download size exceeds the configured limit')
        return expected

    def _request(self, url, params, headers, xml):
        if params:
            supplied_params = {}
            for key, value in params.items():
                if value is not None:
                    supplied_params[key] = value
            url += "?" + urlencode(supplied_params)
        accept = "application/json"
        if xml:
            accept = "application/xml,text/xml"
        request_headers = {"User-Agent": self.config.user_agent, "Accept": accept}
        request_headers.update(headers or {})
        return Request(url, headers=request_headers)

    def _read_response(self, request, xml):
        with urlopen(request, timeout=self.timeout) as response:
            data = response.read(self.config.response_limit + 1)
            status = response.status
            content_type = response.headers.get("Content-Type", "").lower()
            endpoint = response.geturl().split("?", 1)[0]
        if len(data) > self.config.response_limit:
            raise SourceError("response exceeds size limit")
        diagnostic = "{} HTTP {} Content-Type {}".format(
            endpoint, status, content_type or "missing",
        )
        if not 200 <= status < 300:
            raise SourceError(diagnostic)
        return self._parse_response(data, content_type, diagnostic, xml)

    def _parse_response(self, data, content_type, diagnostic, xml):
        expected = "json"
        if xml:
            expected = "xml"
        if expected not in content_type:
            hint = ""
            beginning = data[:self.config.html_preview_bytes].lower()
            if b"<html" in beginning or b"<!doctype html" in beginning:
                hint = " (HTML anti-bot/interstitial response)"
            raise SourceError(diagnostic + ": expected " + expected.upper() + hint)
        if not data.strip():
            raise SourceError(diagnostic + ": empty response")
        try:
            if xml:
                return ET.fromstring(data)
            parsed = json.loads(data)
        except (ValueError, ET.ParseError) as exc:
            raise SourceError(diagnostic + ": malformed " + expected.upper()) from exc
        if not isinstance(parsed, dict):
            raise SourceError(diagnostic + ": expected JSON object")
        return parsed

    def _retry_delay(self, error, attempt):
        delay = error.headers.get("Retry-After", "")
        if delay.isdigit():
            seconds = float(delay)
        else:
            seconds = self.config.retry_backoff_base ** attempt
        return min(seconds, self.config.maximum_retry_delay)
