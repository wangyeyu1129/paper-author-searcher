"""Resumable paper downloads with atomic manifests and independent content checks."""
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .config import DEFAULT_DOWNLOAD
from .http import HttpClient
from .matching import canonical_doi, normalize
from .models import PaperDownloadResult
from .pdf_file_validation import PDFFileValidator, validation_target
from .pdf_identity import native_id
from .performance_diagnostics import pdf_failure_reasons


def paper_key(paper):
    if paper.get('doi'):
        identity = canonical_doi(paper['doi'])
    elif paper.get('source') and paper.get('source_paper_id'):
        identity = [paper['source'], native_id(paper['source'], paper['source_paper_id'])]
    else:
        identity = [normalize(paper.get('title') or ''), paper.get('year'),
                    sorted(normalize(author) for author in paper.get('authors') or [])]
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def paper_filename(paper, config=DEFAULT_DOWNLOAD):
    title = unicodedata.normalize('NFKC', paper.get('title') or 'untitled')
    title = ''.join('_' if unicodedata.category(character) in ('Cc', 'Cf', 'Cs') else character
                    for character in title)
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', title)
    title = re.sub(r'\s+', '_', title).strip(' ._') or 'untitled'
    year = str(paper.get('year') or 'unknown_year')
    year = re.sub(r'[^a-zA-Z0-9_]', '_', year)
    suffix = '_' + paper_key(paper)[:config.filename_hash_characters] + '.pdf'
    prefix = year + '_' + title
    return _bounded_filename(prefix, suffix, config.filename_bytes)


def _bounded_filename(prefix, suffix, maximum_bytes):
    available = maximum_bytes - len(suffix.encode('utf-8'))
    prefix = prefix.encode('utf-8')[:available].decode('utf-8', errors='ignore').rstrip(' ._')
    return prefix + suffix


def file_digest(path, config=DEFAULT_DOWNLOAD):
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as handle:
        while True:
            block = handle.read(config.chunk_bytes)
            if not block:
                break
            size += len(block)
            if size > config.maximum_file_bytes:
                raise ValueError('Existing file exceeds the configured limit')
            digest.update(block)
    return size, digest.hexdigest()


class PDFDownloader:
    def __init__(self, client=None, validator=None, config=DEFAULT_DOWNLOAD):
        self.client = client or HttpClient()
        self.validator = validator or PDFFileValidator(config)
        self.config = config

    def download(self, saved_result, output_dir, include_needs_review=False, maximum_downloads=None,
                 manifest_path=None, progress=None):
        if maximum_downloads is not None and maximum_downloads < 1:
            raise ValueError('maximum_downloads must be positive')
        papers = saved_result.get('papers')
        if not isinstance(papers, list) or not all(isinstance(paper, dict) for paper in papers):
            raise ValueError('Input must be a PDF Resolver report with paper objects')
        root = Path(output_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = Path(manifest_path) if manifest_path is not None else root / 'manifest.json'
        if manifest_path.parent.resolve() not in (root, root.parent):
            raise ValueError('Manifest must be in the PDF directory or its parent')
        previous = self._load_manifest(root, manifest_path)
        previous_by_key = {}
        known_files = {}
        for entry in previous.get('papers', []):
            previous_by_key.setdefault(entry['paper_key'], []).append(entry)
            if entry.get('sha256') and entry.get('local_filename'):
                known_files.setdefault(entry['sha256'], entry['local_filename'])
        results = [self._new_record(paper, previous_by_key) for paper in papers]
        current_keys = {record.paper_key for record in results}
        retained = [entry for entry in previous.get('papers', []) if entry['paper_key'] not in current_keys]
        attempted = 0
        manifest = self._save_manifest(root, results, retained, attempted, False, previous, manifest_path)
        complete = False
        try:
            for paper, record in zip(papers, results):
                candidates = self._eligible_candidates(paper, record, include_needs_review)
                if not candidates:
                    continue
                if self._reuse_existing(root, paper, record, previous_by_key.get(record.paper_key, [])):
                    continue
                if maximum_downloads is not None and attempted >= maximum_downloads:
                    record.download_status = 'skipped_limit'
                    continue
                attempted += 1
                record.download_status = 'downloading'
                if progress:
                    progress('Downloading PDFs', attempted, maximum_downloads or len(papers))
                self._save_manifest(root, results, retained, attempted, False, previous, manifest_path)
                self._download_paper(root, paper, record, candidates, known_files)
                previous_by_key.setdefault(record.paper_key, []).append(asdict(record))
                if record.local_filename and record.sha256:
                    known_files.setdefault(record.sha256, record.local_filename)
                self._save_manifest(root, results, retained, attempted, False, previous, manifest_path)
            self._organize_confirmed_files(root, results, retained)
            complete = True
        except KeyboardInterrupt:
            for record in results:
                if record.download_status == 'downloading':
                    record.download_status = 'interrupted'
                    record.error_details.append('Interrupted transfer; incomplete files restart from byte zero')
            raise
        finally:
            manifest = self._save_manifest(root, results, retained, attempted, complete, previous, manifest_path)
        return manifest

    def _new_record(self, paper, previous_by_key):
        key = paper_key(paper)
        record = PaperDownloadResult(
            key, paper.get('title', ''), paper.get('ownership_status', 'needs_review'),
            authors=deepcopy(paper.get('authors') or []), year=paper.get('year'), doi=paper.get('doi'),
            source=paper.get('source'), resolver_error_details=deepcopy(paper.get('error_details') or []),
            ownership_evidence=deepcopy(paper.get('ownership_evidence') or []),
            source_paper_id=paper.get('source_paper_id'), source_author_id=paper.get('source_author_id'),
            external_ids=deepcopy(paper.get('external_ids') or {}),
            paper_metadata=deepcopy(paper.get('paper_metadata') or {}),
        )
        previous = previous_by_key.get(key, [])
        if previous:
            last = previous[-1]
            record.history = deepcopy(last.get('history') or [])
            record.history.append({name: deepcopy(value) for name, value in last.items() if name != 'history'})
        return record

    def _eligible_candidates(self, paper, record, include_needs_review):
        allowed = {'verified'}
        if include_needs_review:
            allowed.add('needs_review')
        if record.ownership_status not in allowed:
            record.download_status = 'skipped_ownership'
            return []
        candidates = []
        seen_urls = set()
        for candidate in paper.get('pdf_candidates') or []:
            if candidate.get('validation_status') != 'verified' or candidate.get('identity_status') != 'verified':
                continue
            # A previously resolved CDN URL can expire while its original public locator still works.
            for url in (candidate.get('final_url'), candidate.get('url')):
                if isinstance(url, str) and url not in seen_urls:
                    candidates.append(dict(candidate, url=url, final_url=None))
                    seen_urls.add(url)
        best = paper.get('best_pdf_url')
        candidates.sort(key=lambda candidate: (candidate.get('final_url') or candidate.get('url')) != best)
        if not candidates:
            record.download_status = 'skipped_unverified_url'
        return candidates

    def _reuse_existing(self, root, paper, record, entries):
        possible = list(entries)
        filename = paper_filename(paper, self.config)
        for folder in ('', 'needs_review', 'quarantine'):
            relative = str(PurePosixPath(folder) / filename)
            possible.append({'local_filename': relative})
        seen_paths = set()
        for entry in possible:
            relative = entry.get('local_filename')
            if not relative or relative in seen_paths:
                continue
            seen_paths.add(relative)
            try:
                path = self._safe_path(root, relative)
                if not path.is_file():
                    continue
                size, digest = file_digest(path, self.config)
                if entry.get('sha256') and digest != entry['sha256']:
                    record.error_details.append('Previously recorded file checksum changed: ' + relative)
                    continue
                candidate = next((item for item in paper.get('pdf_candidates') or []
                                  if entry.get('pdf_url') in (item.get('url'), item.get('final_url'))), None)
                validation = self.validator.validate(path, validation_target(paper, candidate))
                if validation.format_status != 'valid' or validation.identity_status == 'rejected':
                    continue
                self._apply_validation(record, validation)
                record.file_size, record.sha256 = size, digest
                record.local_filename = relative
                record.pdf_url = entry.get('pdf_url')
                record.final_url = entry.get('final_url')
                record.pdf_source = entry.get('pdf_source')
                record.source = paper.get('source') or entry.get('source') or record.pdf_source
                record.version_type = entry.get('version_type', 'unknown')
                record.access_basis = entry.get('access_basis', '')
                record.license = entry.get('license')
                record.pdf_provenance = deepcopy(entry.get('pdf_provenance') or [])
                record.download_status = 'skipped_existing'
                return True
            except Exception as exc:
                record.error_details.append(str(exc))
        return False

    def _download_paper(self, root, paper, record, candidates, known_files):
        rejected = None
        for candidate in candidates:
            descriptor, temporary_name = tempfile.mkstemp(prefix='.download-', suffix='.part', dir=str(root))
            os.close(descriptor)
            temporary = Path(temporary_name)
            url = candidate.get('final_url') or candidate['url']
            record.pdf_url = url
            record.pdf_source = candidate.get('source')
            record.source = paper.get('source') or record.pdf_source
            record.file_size, record.sha256 = None, None
            record.validation = {}
            record.format_status, record.identity_status = 'not_checked', 'not_checked'
            record.validation_status = 'not_checked'
            attempt = {'url': url, 'source': candidate.get('source'), 'status': 'started',
                       'url_validation_status': candidate.get('validation_status'),
                       'provenance': deepcopy(candidate.get('provenance') or [])}
            record.attempts.append(attempt)
            try:
                receipt = self.client.stream_pdf(url, temporary, self.config)
                attempt.update({'final_url': receipt.final_url, 'http_status': receipt.status,
                                'content_type': receipt.content_type, 'file_size': receipt.file_size,
                                'sha256': receipt.sha256})
                validation = self.validator.validate(temporary, validation_target(paper, candidate))
                attempt['validation'] = asdict(validation)
                attempt['file_size'] = receipt.file_size
                attempt['sha256'] = receipt.sha256
                if validation.format_status != 'valid':
                    self._apply_validation(record, validation)
                    record.file_size, record.sha256 = receipt.file_size, receipt.sha256
                    raise ValueError('Downloaded file could not pass PDF structure parsing: ' + '; '.join(validation.error_details))
                duplicate = self._duplicate_file(root, receipt.sha256, known_files)
                if duplicate:
                    relative = duplicate
                else:
                    relative = self._store_file(root, temporary, paper, validation.identity_status)
                attempt['local_filename'] = relative
                if validation.identity_status == 'rejected':
                    attempt['status'] = 'identity_mismatch'
                    record.error_details.append('PDF content identifies a different paper; quarantined or retained by hash')
                    rejected = (candidate, receipt, validation, relative, duplicate)
                    continue
                self._record_completed(record, paper, candidate, receipt, validation, relative, duplicate)
                attempt['status'] = 'completed'
                return
            except Exception as exc:
                attempt['status'] = 'failed'
                attempt['error'] = str(exc)
                attempt['http_status'] = getattr(exc, 'status_code', None)
                record.error_details.append(str(exc))
            finally:
                if temporary.exists():
                    temporary.unlink()
        if rejected is not None:
            self._record_completed(record, paper, *rejected)
            record.download_status = 'rejected_content'
        else:
            record.download_status = 'failed'
            if record.format_status == 'invalid':
                record.validation_status = 'invalid'
            elif record.format_status == 'unverified':
                record.validation_status = 'unverified'

    def _record_completed(self, record, paper, candidate, receipt, validation, relative, duplicate):
        self._apply_validation(record, validation)
        record.file_size, record.sha256 = receipt.file_size, receipt.sha256
        record.pdf_url = candidate.get('final_url') or candidate['url']
        record.final_url = receipt.final_url
        record.pdf_source = candidate.get('source')
        record.source = paper.get('source') or record.pdf_source
        record.version_type = candidate.get('version_type', 'unknown')
        record.access_basis = candidate.get('access_basis', '')
        record.license = candidate.get('license')
        record.pdf_provenance = deepcopy(candidate.get('provenance') or [])
        record.local_filename = relative
        record.duplicate_of = duplicate
        record.download_status = 'duplicate_content' if duplicate else 'downloaded'

    def _apply_validation(self, record, validation):
        record.validation = asdict(validation)
        record.validation_status = validation.validation_status
        record.format_status = validation.format_status
        record.identity_status = validation.identity_status

    def _duplicate_file(self, root, digest, known_files):
        relative = known_files.get(digest)
        if not relative:
            return None
        try:
            path = self._safe_path(root, relative)
            if path.is_file() and file_digest(path, self.config)[1] == digest:
                return relative
        except (OSError, ValueError):
            pass
        return None

    def _safe_path(self, root, relative):
        if not isinstance(relative, str) or '\\' in relative:
            raise ValueError('Unsafe manifest filename')
        parts = PurePosixPath(relative).parts
        if not parts or len(parts) > 2 or any(part in ('..', '.', '/') for part in parts):
            raise ValueError('Unsafe manifest filename')
        if len(parts) == 2 and parts[0] not in ('needs_review', 'quarantine'):
            raise ValueError('Unexpected manifest subdirectory')
        path = root.joinpath(*parts)
        if path.is_symlink() or path.parent.is_symlink() or root not in path.resolve().parents:
            raise ValueError('Manifest path escapes the output folder or follows a symlink')
        return path

    def _store_file(self, root, temporary, paper, identity_status, filename=None):
        folder = {'needs_review': 'needs_review', 'rejected': 'quarantine'}.get(identity_status, '')
        filename = filename or paper_filename(paper, self.config)
        relative = str(PurePosixPath(folder) / filename)
        destination = self._safe_path(root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        while True:
            try:
                # Reserve exclusively before atomic replacement; existing user files are never overwritten.
                descriptor = os.open(str(destination), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
                break
            except FileExistsError:
                counter += 1
                stem_parts = Path(filename).stem.rsplit('_', 1)
                prefix = stem_parts[0]
                identifier = stem_parts[-1]
                if len(stem_parts) == 1:
                    identifier = paper_key({'title': filename})[:self.config.filename_hash_characters]
                suffix = '_{}_{}.pdf'.format(identifier, counter)
                alternate = _bounded_filename(prefix, suffix, self.config.filename_bytes)
                relative = str(PurePosixPath(folder) / alternate)
                destination = self._safe_path(root, relative)
        try:
            os.replace(str(temporary), str(destination))
        except OSError:
            destination.unlink()
            raise
        return destination.relative_to(root).as_posix()

    def _organize_confirmed_files(self, root, results, retained):
        references = {}
        for record in results:
            if record.local_filename:
                references.setdefault(record.local_filename, []).append(record)
        for entry in retained:
            if entry.get('local_filename'):
                references.setdefault(entry['local_filename'], []).append(entry)
        for relative, entries in references.items():
            if not relative.startswith('needs_review/'):
                continue
            confirmed = True
            for entry in entries:
                if isinstance(entry, PaperDownloadResult):
                    status = entry.validation_status
                else:
                    status = entry.get('validation_status')
                if status != 'verified':
                    confirmed = False
                    break
            if not confirmed:
                continue
            path = self._safe_path(root, relative)
            record = next((entry for entry in entries if isinstance(entry, PaperDownloadResult)), None)
            if record is None or not path.is_file():
                continue
            new_relative = self._store_file(root, path, {}, 'verified', filename=PurePosixPath(relative).name)
            for entry in entries:
                if isinstance(entry, PaperDownloadResult):
                    entry.local_filename = new_relative
                    if entry.duplicate_of == relative:
                        entry.duplicate_of = new_relative
                else:
                    entry['local_filename'] = new_relative
                    if entry.get('duplicate_of') == relative:
                        entry['duplicate_of'] = new_relative

    def _load_manifest(self, root, manifest_path=None):
        path = manifest_path if manifest_path is not None else root / 'manifest.json'
        if path.is_symlink():
            raise ValueError('Manifest must not be a symlink')
        if not path.exists():
            return {}
        with path.open(encoding='utf-8') as handle:
            saved = json.load(handle)
        if not isinstance(saved, dict) or saved.get('schema_version') != 1 or not isinstance(saved.get('papers'), list):
            raise ValueError('Existing manifest format is invalid; it will not be overwritten')
        if not all(isinstance(entry, dict) and entry.get('paper_key') for entry in saved['papers']):
            raise ValueError('Existing manifest has invalid paper entries')
        expected_root = os.path.relpath(str(root), str(path.parent))
        if saved.get('file_root', '.') != expected_root:
            raise ValueError('Manifest refers to a different PDF directory')
        return saved

    def _save_manifest(self, root, results, retained, attempted, complete, previous, manifest_path=None):
        manifest_path = manifest_path if manifest_path is not None else root / 'manifest.json'
        if manifest_path.is_symlink():
            raise ValueError('Manifest must not be a symlink')
        now = datetime.now(timezone.utc).isoformat()
        for record in results:
            record.failure_reasons = pdf_failure_reasons(asdict(record))
        statistics = {
            'total_input_papers': len(results), 'attempted_papers': attempted,
            'downloaded_files': sum(record.download_status == 'downloaded' for record in results),
            'verified_papers': sum(record.validation_status == 'verified' for record in results),
            'needs_review_papers': sum(record.format_status == 'valid' and record.identity_status == 'needs_review' for record in results),
            'failed_papers': sum(record.download_status in ('failed', 'rejected_content') for record in results),
            'reused_existing_papers': sum(record.download_status == 'skipped_existing' for record in results),
            'duplicate_content_papers': sum(record.download_status == 'duplicate_content' for record in results),
            'skipped_papers': sum(record.download_status.startswith('skipped_') and record.download_status != 'skipped_existing' for record in results),
        }
        manifest = {'schema_version': 1, 'created_at': previous.get('created_at', now), 'updated_at': now,
                    'run_complete': complete, 'statistics': statistics,
                    'papers': [asdict(record) for record in results] + retained,
                    'file_root': os.path.relpath(str(root), str(manifest_path.parent))}
        descriptor, filename = tempfile.mkstemp(prefix='.manifest-', suffix='.tmp', dir=str(manifest_path.parent))
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, str(manifest_path))
        finally:
            if os.path.exists(filename):
                os.unlink(filename)
        return manifest
