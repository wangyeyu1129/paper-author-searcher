"""Compose existing stages and checkpoint their results without changing decisions."""

import json
import os
import re
import tempfile
import uuid
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

from .config import CONTENDER_STATUS, DEFAULT_COLLECTION, DEFAULT_MATCHING
from .models import ProfessorProfile, ResolutionResult
from .paper_collector import IdentityConfirmationRequired, PaperCollector
from .paper_reanalysis import saved_collection_profile
from .pdf_resolver import PDFResolver
from .performance_diagnostics import workflow_outcomes
from .resolver import ProfessorResolver

MODES = ('metadata', 'pdf-urls', 'full')


def write_json(path, data):
    if path.is_symlink():
        raise ValueError('Output JSON must not be a symlink')
    descriptor, temporary = tempfile.mkstemp(prefix='.checkpoint-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    with Path(path).open(encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('Input must be a JSON object')
    return data


def load_checkpoint(path):
    """Report references are local artifacts, never arbitrary paths or URLs."""
    path = Path(path).resolve()
    data = read_json(path)
    if data.get('report_type') != 'paper_search_workflow':
        return data
    loaded = {}
    for key in ('professor', 'papers', 'pdf_urls'):
        filename = data.get('artifacts', {}).get(key)
        if not filename:
            continue
        artifact = path.parent / filename
        if artifact.is_symlink() or artifact.resolve().parent != path.parent:
            raise ValueError('Report artifact must be a file in the report directory')
        loaded[key] = read_json(artifact)
    if 'pdf_urls' in loaded:
        saved = loaded['pdf_urls']
        if 'papers' in loaded:
            saved['collection_archive'] = loaded['papers']
        if 'professor' in loaded:
            saved['identity_archive'] = loaded['professor']
        return saved
    if 'papers' in loaded:
        return loaded['papers']
    if 'professor' in loaded:
        return loaded['professor']
    raise ValueError('Report contains no resumable checkpoint')


def _profile(values):
    values = dict(values)
    values.setdefault('affiliations', values.get('matched_affiliations') or [])
    values.setdefault('identity_confidence', values.get('confidence', 0))
    values.setdefault('evidence', [])
    allowed = {entry.name for entry in fields(ProfessorProfile)}
    return ProfessorProfile(**{key: value for key, value in values.items() if key in allowed})


def _resolution(saved):
    if 'candidates' not in saved or 'profile' not in saved:
        if not saved.get('name') or not isinstance(saved.get('author_ids'), dict) or 'status' not in saved:
            raise ValueError('Expected Resolver, Collector or PDF Resolver JSON')
        profile = _profile(saved)
        return ResolutionResult(profile, [profile], saved.get('source_errors') or {}, saved.get('ambiguity'))
    profile = _profile(saved['profile']) if saved['profile'] else None
    return ResolutionResult(
        profile,
        [_profile(item) for item in saved['candidates']],
        saved.get('source_errors') or {},
        saved.get('ambiguity'),
    )


def contender_profile(result, maximum_contenders, margin):
    """Merge the leading, school-compatible candidates into one unconfirmed profile for collection."""
    best = result.profile
    contenders = [
        candidate
        for candidate in result.candidates
        if candidate.university_status != 'mismatch' and candidate.match_score >= best.match_score - margin
    ][:maximum_contenders]
    merged = deepcopy(best)
    merged.status = CONTENDER_STATUS
    aliases = {}
    for candidate in contenders:
        for source, author_id in candidate.author_ids.items():
            aliases.setdefault(source, [])
            for value in (candidate.author_id_aliases.get(source) or []) + [author_id]:
                if value not in aliases[source]:
                    aliases[source].append(value)
    merged.author_id_aliases = aliases
    merged.author_ids = {source: values[0] for source, values in aliases.items()}
    merged.evidence = [
        *list(best.evidence),
        f'Collected {len(contenders)} competing profiles without confirming identity',
    ]
    return merged


def _select_identity(result, selection, policy='stop', maximum_contenders=3, margin=0.1):
    if selection:
        if ':' not in selection:
            raise IdentityConfirmationRequired('Selection must use SOURCE:AUTHOR_ID')
        source, author_id = selection.split(':', 1)
        for candidate in result.candidates:
            ids = candidate.author_id_aliases.get(source) or []
            if candidate.author_ids.get(source) != author_id and author_id not in ids:
                continue
            confirmed = deepcopy(candidate)
            confirmed.author_ids = {source: author_id}
            confirmed.author_id_aliases = {source: [author_id]}
            return confirmed, (source, author_id)
        raise IdentityConfirmationRequired('Selected Author ID is absent from the candidates')
    if result.profile is None:
        raise IdentityConfirmationRequired('No identity candidate available')
    if result.profile.status != 'resolved':
        if policy == 'collect_contenders' and result.profile.university_status != 'mismatch':
            return contender_profile(result, maximum_contenders, margin), None
        raise IdentityConfirmationRequired('Explicit Author ID selection is required for this identity')
    return result.profile, None


def _is_pdf_report(saved):
    return 'validation_scope' in saved and isinstance(saved.get('papers'), list)


def _paper_entries(saved):
    collection = saved.get('collection', saved)
    papers = collection.get('all_papers', collection.get('papers'))
    if not isinstance(papers, list) or not all(isinstance(item, dict) for item in papers):
        raise ValueError('Expected a paper list')
    return papers


def metadata_audit(original, pdf=None, manifest=None):
    """Missing source data is separate from evidence lost between stages."""
    audited_fields = (
        'title',
        'authors',
        'year',
        'doi',
        'source',
        'source_paper_id',
        'source_author_id',
        'ownership_status',
        'ownership_evidence',
    )
    missing = {}
    losses = []
    for field in audited_fields:
        missing[field] = sum(item.get(field) in (None, '', []) for item in original)
    for stage, downstream in (('pdf_urls', pdf), ('download', manifest)):
        if downstream is None:
            continue
        for index, (before, after) in enumerate(zip(original, downstream.get('papers', []))):
            for field in audited_fields:
                value = before.get(field)
                if value not in (None, '', []) and after.get(field) != value:
                    losses.append({'stage': stage, 'entry_index': index, 'field': field})
        if len(downstream.get('papers', [])) < len(original):
            losses.append({'stage': stage, 'field': 'paper_entries', 'error': 'Missing entries'})
    return {'missing_input_fields': missing, 'transmission_losses': losses}


class PaperSearchWorkflow:
    def __init__(
        self,
        resolver=None,
        collector=None,
        pdf_resolver=None,
        downloader=None,
        progress=None,
        collection_config=DEFAULT_COLLECTION,
        matching_config=DEFAULT_MATCHING,
    ):
        self.resolver = resolver or ProfessorResolver()
        self.collector = collector or PaperCollector()
        self.collection_config = collection_config
        self.matching_config = matching_config
        self.pdf_resolver = pdf_resolver
        self.downloader = downloader
        self.progress = progress

    def _progress(self, stage, completed=None, total=None):
        self.current_stage = stage
        if self.progress:
            self.progress(stage, completed, total)

    def _directory(self, output_dir, name, resume_dir):
        if resume_dir is not None:
            root = Path(resume_dir).resolve()
            marker = read_json(root / 'report.json')
            if marker.get('report_type') != 'paper_search_workflow':
                raise ValueError('--resume-dir must be a workflow output directory')
            return root
        # A fixed prefix avoids Windows reserved names; UUID isolates simultaneous searches.
        slug = re.sub(r'[^\w-]+', '_', name, flags=re.UNICODE).strip('._') or 'professor'
        slug = slug.encode('utf-8')[:80].decode('utf-8', errors='ignore')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        root = Path(output_dir).resolve() / ('search_' + slug + '_' + stamp + '_' + uuid.uuid4().hex[:12])
        root.mkdir(parents=True, exist_ok=False)
        return root

    def run(
        self,
        query=None,
        saved=None,
        output_dir='output',
        mode='full',
        selected_author=None,
        include_needs_review=False,
        maximum_pdf_papers=None,
        maximum_downloads=None,
        resume_dir=None,
    ):
        if mode not in MODES:
            raise ValueError('Unknown workflow mode')
        if (query is None) == (saved is None):
            raise ValueError('Provide professor information or an input checkpoint')
        for limit in (maximum_pdf_papers, maximum_downloads):
            if limit is not None and limit < 1:
                raise ValueError('Workflow limits must be positive')
        stored_identity = (saved or {}).get('identity_archive') or saved or {}
        identity = stored_identity.get('profile') or stored_identity.get('collection_identity') or stored_identity
        name = query.name if query else identity.get('name', 'saved_search')
        root = self._directory(output_dir, name, resume_dir)
        if (root / 'papers').is_symlink():
            raise ValueError('PDF directory must not be a symlink')
        (root / 'papers').mkdir(exist_ok=True)
        report = {
            'schema_version': 1,
            'report_type': 'paper_search_workflow',
            'mode': mode,
            'run_status': 'running',
            'output_directory': str(root),
            'stages': [],
            'errors': [],
            'artifacts': {},
            'limits': {'maximum_pdf_papers': maximum_pdf_papers, 'maximum_downloads': maximum_downloads},
            'include_needs_review': include_needs_review,
            'count_scope': 'retrieved_metadata_not_total_publications',
        }
        report['warnings'] = []
        report['candidate_decisions'] = []
        if query:
            report['query'] = asdict(query)
        write_json(root / 'report.json', report)
        stage = 'Resolving professor identity'
        self.current_stage = stage
        try:
            archive, pdf = self._prepare_input(query, saved, selected_author, root, report)
            if report['run_status'] == 'identity_confirmation_required':
                return report
            original = _paper_entries(archive)
            write_json(root / 'papers.json', archive)
            report['artifacts']['papers'] = 'papers.json'
            report['collection_statistics'] = archive.get('collection', archive).get('statistics', {})
            manifest = None
            if mode != 'metadata':
                stage = 'Finding PDF URLs'
                self._progress(stage)
                if pdf is None:
                    resolver = self.pdf_resolver or PDFResolver()
                    pdf = resolver.resolve(
                        archive, include_needs_review, maximum_pdf_papers, progress=self._progress
                    ).to_dict()
                    # The full archive keeps raw authorship and identity provenance when resuming.
                    pdf['identity_archive'] = report.get('identity') or {}
                report['stages'].append(
                    {'stage': stage, 'status': 'reused' if _is_pdf_report(saved or {}) else 'completed'}
                )
                write_json(root / 'pdf_urls.json', pdf)
                report['artifacts']['pdf_urls'] = 'pdf_urls.json'
                report['pdf_statistics'] = pdf.get('statistics', {})
                for item in pdf['papers']:
                    report['warnings'].extend(
                        dict(warning, stage=stage, title=item.get('title')) for warning in item.get('warnings') or []
                    )
                    report['candidate_decisions'].extend(
                        dict(decision, stage=stage, title=item.get('title'))
                        for decision in item.get('candidate_decisions') or []
                    )
                    report['errors'].extend(
                        dict(error, stage=stage, title=item.get('title')) for error in item.get('error_details') or []
                    )
                write_json(root / 'report.json', report)
            if mode == 'full':
                stage = 'Downloading PDFs'
                self._progress(stage)
                if self.downloader is None:
                    from .pdf_downloader import PDFDownloader

                    self.downloader = PDFDownloader()
                manifest = self.downloader.download(
                    pdf,
                    root / 'papers',
                    include_needs_review,
                    maximum_downloads,
                    manifest_path=root / 'manifest.json',
                    progress=self._progress,
                )
                report['artifacts']['manifest'] = 'manifest.json'
                report['download_statistics'] = manifest['statistics']
                processed = report['pdf_statistics'].get('processed_papers', 0)
                satisfied = manifest['statistics'].get('search_sufficiently_satisfied_papers', 0)
                report['search_satisfaction'] = {
                    'satisfied_papers': satisfied,
                    'processed_papers': processed,
                    'rate': satisfied / processed if processed else None,
                    'unknown_document_type_papers': manifest['statistics'].get('search_satisfaction_unknown_papers', 0),
                    'definition': 'Verified ownership, local integrity, PDF format, content identity '
                    'and full_text/preprint type',
                    'independent_of_search_completion': True,
                }
                report['download_run_complete'] = manifest['run_complete']
                report['stages'].append({'stage': stage, 'status': 'completed'})
                for item in manifest['papers']:
                    for error in item.get('error_details') or []:
                        report['errors'].append({'stage': stage, 'title': item.get('title'), 'error': error})
            report['metadata_audit'] = metadata_audit(original, pdf, manifest)
            review_files = bool(manifest and manifest['statistics']['needs_review_papers'])
            incomplete_pdf_search = bool(pdf and pdf.get('statistics', {}).get('incomplete_search_papers'))
            partial = bool(
                report['errors']
                or report['metadata_audit']['transmission_losses']
                or review_files
                or incomplete_pdf_search
            )
            report['run_status'] = 'partial' if partial else 'completed'
            self._progress('Generating report')
        except IdentityConfirmationRequired as exc:
            report['run_status'] = 'identity_confirmation_required'
            report['errors'].append({'stage': self.current_stage, 'error': str(exc)})
        except KeyboardInterrupt:
            report['run_status'] = 'interrupted'
            report['errors'].append(
                {'stage': self.current_stage, 'error': 'Interrupted; use the saved checkpoint to continue'}
            )
            raise
        except Exception as exc:
            report['run_status'] = 'failed'
            report['errors'].append({'stage': self.current_stage, 'error': str(exc)})
        finally:
            report['outcomes'] = workflow_outcomes(report)
            write_json(root / 'report.json', report)
        return report

    def _prepare_input(self, query, saved, selection, root, report):
        if saved is not None and (_is_pdf_report(saved) or 'collection' in saved):
            if selection:
                raise ValueError('Saved paper ownership is tied to its original identity; select from Resolver JSON')
            archive = deepcopy(saved.get('collection_archive') or saved)
            pdf = deepcopy(saved) if _is_pdf_report(saved) else None
            if pdf is not None:
                pdf.pop('collection_archive', None)
            self._progress('Reusing collected papers')
            report['stages'].extend(
                {'stage': stage, 'status': 'reused'}
                for stage in ('Collecting papers', 'Deduplicating papers', 'Validating ownership')
            )
            identity = saved.get('identity_archive')
            if 'collection' in archive and (identity is None or 'profile' not in identity):
                profile = asdict(saved_collection_profile(archive))
                identity = {
                    'profile': profile,
                    'candidates': [profile],
                    'source_errors': archive.get('source_errors') or {},
                    'ambiguity': archive.get('ambiguity'),
                }
            if identity:
                report['identity'] = deepcopy(identity)
                if pdf is not None:
                    pdf['identity_archive'] = deepcopy(identity)
                write_json(root / 'professor.json', identity)
                report['artifacts']['professor'] = 'professor.json'
                for source, error in identity.get('source_errors', {}).items():
                    report['errors'].append({'stage': 'Resolving professor identity', 'source': source, 'error': error})
            self._collection_errors(archive, report)
            return archive, pdf
        if saved is not None and 'selected_author_ids' in saved and 'papers' in saved:
            if selection:
                raise ValueError('Cannot change identity for an existing collection')
            self._collection_errors(saved, report)
            self._progress('Reusing collected papers')
            report['stages'].append({'stage': 'Collecting papers', 'status': 'reused'})
            return deepcopy(saved), None
        self._progress('Resolving professor identity')
        result = self.resolver.resolve(query) if query else _resolution(saved)
        identity = result.to_dict()
        write_json(root / 'professor.json', identity)
        report['identity'] = identity
        report['artifacts']['professor'] = 'professor.json'
        report['stages'].append({'stage': 'Resolving professor identity', 'status': 'completed' if query else 'reused'})
        for source, error in result.source_errors.items():
            report['errors'].append({'stage': 'Resolving professor identity', 'source': source, 'error': error})
        try:
            profile, selected = _select_identity(
                result,
                selection,
                self.collection_config.ambiguity_policy,
                self.collection_config.maximum_contenders,
                self.matching_config.ambiguity_margin,
            )
            if profile.status == CONTENDER_STATUS:
                report['warnings'].append(
                    {
                        'stage': 'Resolving professor identity',
                        'warning': 'Identity unconfirmed; competing profiles collected for review',
                    }
                )
        except IdentityConfirmationRequired as exc:
            report['run_status'] = 'identity_confirmation_required'
            report['errors'].append({'stage': 'Resolving professor identity', 'error': str(exc)})
            return {}, None
        self._progress('Collecting papers')
        collection = self.collector.collect(profile, selected_author=selected, progress=self._progress)
        archive = dict(
            identity,
            collection=collection.to_dict(),
            collection_identity={
                'name': profile.name,
                'status': profile.status,
                'orcid': profile.orcid,
                'name_aliases': profile.name_aliases,
                'author_ids': collection.selected_author_ids,
                'confirmation': collection.identity_confirmation,
            },
        )
        report['stages'].extend(
            {'stage': stage, 'status': 'completed'}
            for stage in ('Collecting papers', 'Deduplicating papers', 'Validating ownership')
        )
        self._collection_errors(archive, report)
        return archive, None

    @staticmethod
    def _collection_errors(archive, report):
        collection = archive.get('collection', archive)
        report['collection_complete'] = collection.get('complete')
        report['collection_sources'] = deepcopy(collection.get('sources') or [])
        for source in collection.get('sources') or []:
            if source.get('error'):
                report['errors'].append(
                    {'stage': 'Collecting papers', 'source': source.get('source'), 'error': source['error']}
                )
            for error in source.get('metadata_errors') or []:
                report['errors'].append({'stage': 'Collecting papers', 'source': source.get('source'), 'error': error})
        if collection.get('complete') is False and not any(
            source.get('error') for source in collection.get('sources') or []
        ):
            report['errors'].append({'stage': 'Collecting papers', 'error': 'Incomplete collection or pagination'})
