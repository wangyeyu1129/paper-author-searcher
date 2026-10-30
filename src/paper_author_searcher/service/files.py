"""FileStore adapters: the local disk, or any S3-compatible object store."""

import shutil
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from .ports import StoredFile
from .s3 import S3Location, presign

CHUNK = 1024 * 1024


def _safe_key(key: str) -> str:
    parts = key.split('/')
    if not key or key.startswith('/') or any(part in ('', '.', '..') for part in parts):
        raise ValueError(f'unsafe file key {key!r}')
    return key


class LocalFileStore:
    """Files under one directory; ``locate`` hands back the path for the HTTP adapter to stream."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _safe_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError(f'unsafe file key {key!r}')
        return path

    def put(self, key: str, source: Path, content_type: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.part')
        shutil.copyfile(source, partial)
        partial.replace(target)

    def fetch(self, key: str, destination: Path) -> bool:
        source = self._path(key)
        if not source.is_file():
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return True

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def locate(self, key: str, download_name: str) -> StoredFile | None:
        path = self._path(key)
        return StoredFile(path=path) if path.is_file() else None

    def check(self) -> None:
        probe = self.root / '.ready'
        probe.write_text('ok')
        probe.unlink()


class S3FileStore:
    """Objects under ``prefix`` in one bucket; every request goes through a presigned URL."""

    def __init__(
        self,
        location: S3Location,
        access_key_id: str,
        secret_access_key: str,
        prefix: str = '',
        presign_seconds: int = 900,
        timeout: float = 60,
    ) -> None:
        if not (access_key_id and secret_access_key):
            raise ValueError('The S3 file store needs AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY')
        self.location = location
        self.credentials = (access_key_id, secret_access_key)
        self.prefix = prefix
        self.presign_seconds = presign_seconds
        self.timeout = timeout

    def _url(self, method: str, key: str, query: dict[str, str] | None = None, expires: int = 300) -> str:
        object_key = self.prefix + _safe_key(key) if key else ''
        return presign(self.location, method, object_key, *self.credentials, expires=expires, query=query)

    def put(self, key: str, source: Path, content_type: str) -> None:
        size = source.stat().st_size
        with source.open('rb') as handle:
            request = urllib.request.Request(
                self._url('PUT', key),
                data=handle,
                method='PUT',
                headers={'Content-Length': str(size), 'Content-Type': content_type},
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read()

    def fetch(self, key: str, destination: Path) -> bool:
        try:
            with urllib.request.urlopen(self._url('GET', key), timeout=self.timeout) as response:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('wb') as handle:
                    shutil.copyfileobj(response, handle, CHUNK)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return False
            raise
        return True

    def exists(self, key: str) -> bool:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(self._url('HEAD', key), method='HEAD'), timeout=self.timeout
            ):
                return True
        except urllib.error.HTTPError as error:
            if error.code in (403, 404):
                return False
            raise

    def locate(self, key: str, download_name: str) -> StoredFile | None:
        if not self.exists(key):
            return None
        disposition = f"attachment; filename*=UTF-8''{quote(download_name)}"
        query = {'response-content-disposition': disposition}
        return StoredFile(url=self._url('GET', key, query=query, expires=self.presign_seconds))

    def check(self) -> None:
        """The bucket is reachable with these credentials (HEAD bucket)."""
        with urllib.request.urlopen(urllib.request.Request(self._url('HEAD', ''), method='HEAD'), timeout=self.timeout):
            return
