"""Back up and restore what a deployment cannot rebuild: the jobs and limits databases, API keys and local files.

An archive is one tar.gz with a manifest of every member's SHA-256. Databases are copied with SQLite's online backup,
so workers may keep running during a backup. A restore checks everything first and replaces the data only after that;
stop the service before restoring. Indexes, the HTTP cache and run directories can be rebuilt and are left out. With
``file_store = s3`` the files live in the bucket and are backed up there.
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
DATABASES = ('jobs.sqlite', 'limits.sqlite')
KEYS_MEMBER = 'api_keys.json'
FILES_DIRECTORY = 'files'
MANIFEST = 'manifest.json'
ARCHIVE_PREFIX = 'paper-search-backup-'
ARCHIVE_SUFFIX = '.tar.gz'
MARKER = 'last_backup'
HASH_CHUNK_BYTES = 1024 * 1024


def last_backup_time(data_directory: Path) -> float | None:
    """When the last backup of this data directory finished (epoch seconds), or None."""
    try:
        return (data_directory / MARKER).stat().st_mtime
    except OSError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_database(source: Path, destination: Path) -> None:
    origin = sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True)
    try:
        copy = sqlite3.connect(destination)
        try:
            origin.backup(copy)
        finally:
            copy.close()
    finally:
        origin.close()


def _check_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        answer = connection.execute('PRAGMA integrity_check').fetchone()[0]
    except sqlite3.Error as exc:
        raise ValueError(f'{path.name} is not a readable database: {exc}') from exc
    finally:
        connection.close()
    if answer != 'ok':
        raise ValueError(f'{path.name} failed its integrity check: {answer}')


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(path.parts) and not path.is_absolute() and '..' not in path.parts


@dataclass(frozen=True)
class Backups:
    data_directory: Path
    keys_file: Path
    # False when the files live elsewhere (file_store = s3).
    include_files: bool = True

    def create(self, output: Path, keep: int = 0) -> Path:
        """Write a new archive into ``output``; ``keep`` > 0 then deletes all but the newest ``keep``."""
        output.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target = output / f'{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}'
        with tempfile.TemporaryDirectory(prefix='.backup-', dir=output) as scratch:
            manifest: dict[str, dict[str, object]] = {}
            partial = Path(scratch) / target.name
            with tarfile.open(partial, 'w:gz', compresslevel=1) as archive:
                # Databases first: a file written after this copy is one no saved job refers to yet.
                for name in DATABASES:
                    source = self.data_directory / name
                    if source.exists():
                        copy = Path(scratch) / name
                        _copy_database(source, copy)
                        self._add(archive, manifest, name, copy)
                if self.keys_file.exists():
                    self._add(archive, manifest, KEYS_MEMBER, self.keys_file)
                if self.include_files:
                    for name, path in self._local_files():
                        try:
                            self._add(archive, manifest, name, path)
                        except FileNotFoundError:
                            logger.info('Skipped %s: deleted during the backup', name)
                self._add_manifest(archive, Path(scratch), manifest)
            os.replace(partial, target)
        (self.data_directory / MARKER).touch()
        self._prune(output, keep)
        return target

    def restore(self, archive_path: Path, force: bool = False) -> int:
        """Replace the data with the archive's after checking all of it; returns the number of members restored."""
        if not force and (self.data_directory / DATABASES[0]).exists():
            raise ValueError(f'{self.data_directory} already holds a jobs database; restoring needs force')
        self.data_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=self.data_directory) as scratch:
            staged = Path(scratch)
            manifest = self._unpack(archive_path, staged)
            for name in DATABASES:
                if name in manifest:
                    _check_database(staged / name)
            self._replace(staged, manifest)
        return len(manifest)

    def _local_files(self) -> Iterator[tuple[str, Path]]:
        root = self.data_directory / FILES_DIRECTORY
        for path in sorted(root.rglob('*')) if root.exists() else ():
            if path.is_file() and not path.is_symlink():
                yield f'{FILES_DIRECTORY}/{path.relative_to(root).as_posix()}', path

    @staticmethod
    def _add(archive: tarfile.TarFile, manifest: dict, name: str, path: Path) -> None:
        digest, size = _sha256(path), path.stat().st_size
        archive.add(path, arcname=name, recursive=False)
        manifest[name] = {'sha256': digest, 'bytes': size}

    @staticmethod
    def _add_manifest(archive: tarfile.TarFile, scratch: Path, members: dict) -> None:
        document = {
            'format': FORMAT_VERSION,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'members': members,
        }
        path = scratch / MANIFEST
        path.write_text(json.dumps(document, indent=2), encoding='utf-8')
        archive.add(path, arcname=MANIFEST, recursive=False)

    @staticmethod
    def _unpack(archive_path: Path, staged: Path) -> dict[str, dict]:
        """Every member under ``staged``, each checked against the manifest; anything else is refused."""
        with tarfile.open(archive_path, 'r:gz') as archive:
            members = archive.getmembers()
            for member in members:
                if not member.isfile() or not _safe_name(member.name):
                    raise ValueError(f'Refusing archive member {member.name!r}')
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f'Unreadable archive member {member.name!r}')
                destination = staged / member.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source, destination.open('wb') as handle:
                    shutil.copyfileobj(source, handle)
        try:
            document = json.loads((staged / MANIFEST).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError(f'{archive_path.name} has no readable manifest') from exc
        if document.get('format') != FORMAT_VERSION:
            raise ValueError(f'{archive_path.name} has format {document.get("format")}, not {FORMAT_VERSION}')
        manifest: dict[str, dict] = document['members']
        found = {member.name for member in members} - {MANIFEST}
        if found != set(manifest):
            raise ValueError(f'{archive_path.name} does not match its manifest')
        for name, entry in manifest.items():
            if _sha256(staged / name) != entry['sha256']:
                raise ValueError(f'{name} is damaged: its checksum does not match the manifest')
        return manifest

    def _replace(self, staged: Path, manifest: Iterable[str]) -> None:
        names = set(manifest)
        for name in DATABASES:
            # A leftover write-ahead log would be replayed over the restored database.
            for suffix in ('', '-wal', '-shm'):
                (self.data_directory / f'{name}{suffix}').unlink(missing_ok=True)
            if name in names:
                os.replace(staged / name, self.data_directory / name)
        if KEYS_MEMBER in names:
            self.keys_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged / KEYS_MEMBER), self.keys_file)
        if self.include_files:
            files = self.data_directory / FILES_DIRECTORY
            shutil.rmtree(files, ignore_errors=True)
            if (staged / FILES_DIRECTORY).exists():
                os.replace(staged / FILES_DIRECTORY, files)

    @staticmethod
    def _prune(output: Path, keep: int) -> None:
        if keep < 1:
            return
        archives = sorted(output.glob(f'{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}'), key=lambda path: path.name, reverse=True)
        for path in archives[keep:]:
            path.unlink(missing_ok=True)


def backup_forever(backups: Backups, output: Path, keep: int, every_hours: float) -> None:
    """Back up now, then every ``every_hours``; a failed backup is logged (the stale-backup alert covers the rest)."""
    while True:
        try:
            logger.warning('Backup written: %s', backups.create(output, keep))
        except Exception:
            logger.exception('Backup failed; retrying in %.1f h', every_hours)
        time.sleep(every_hours * 3600)
