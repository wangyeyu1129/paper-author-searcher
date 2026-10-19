"""Compose existing stages and checkpoint their results without changing decisions."""

import logging
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from .config import DEFAULT_COLLECTION, DEFAULT_MATCHING, DEFAULT_STORAGE, AmbiguityPolicy, StorageConfig
from .json_types import JSONObject
from .jsonio import read_json, write_json
from .matching import similarity_cache_statistics
from .models import ProfessorProfile, ProfessorQuery, ResolutionResult
from .paper_collector import IdentityConfirmationRequired
from .paper_reanalysis import saved_collection_profile
from .performance_diagnostics import workflow_outcomes
from .status import ResolutionStatus, RunStatus, StageStatus, UniversityStatus

logger = logging.getLogger(__name__)

MODES = ('metadata', 'pdf-urls', 'full')


def load_checkpoint(path: str | Path, intern_strings: bool = False) -> JSONObject:
    """Report references are local artifacts, never arbitrary paths or URLs."""
    path = Path(path).resolve()
    data = read_json(path, intern_strings)
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
        loaded[key] = read_json(artifact, intern_strings)
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


def _profile(values: JSONObject) -> ProfessorProfile:
    values = dict(values)
    values.setdefault('affiliations', values.get('matched_affiliations') or [])
    values.setdefault('identity_confidence', values.get('confidence', 0))
    values.setdefault('evidence', [])
    allowed = {entry.name for entry in fields(ProfessorProfile)}
    return ProfessorProfile(**{key: value for key, value in values.items() if key in allowed})


def _resolution(saved: JSONObject) -> ResolutionResult:
    if 'candidates' not in saved or 'profile' not in saved:
        if not saved.get('name') or not isinstance(saved.get('author_ids'), dict) or 'status' not in saved:
            raise ValueError('Expected Resolver, Collector or PDF Resolver JSON')
        single = _profile(saved)
        return ResolutionResult(single, [single], saved.get('source_errors') or {}, saved.get('ambiguity'))
    profile = _profile(saved['profile']) if saved['profile'] else None
    return ResolutionResult(
        profile,
        [_profile(item) for item in saved['candidates']],
        saved.get('source_errors') or {},
        saved.get('ambiguity'),
    )


def contender_profile(result: ResolutionResult, maximum_contenders: int, margin: float) -> ProfessorProfile:
    """Merge the leading, school-compatible candidates into one unconfirmed profile for collection."""
    best = result.profile
    if best is None:
        raise IdentityConfirmationRequired('No identity candidate available')
    contenders = [
        candidate
        for candidate in result.candidates
        if candidate.university_status != UniversityStatus.MISMATCH
        and candidate.match_score >= best.match_score - margin
    ][:maximum_contenders]
    merged = deepcopy(best)
    merged.status = ResolutionStatus.AMBIGUOUS_CONTENDERS
    aliases: dict[str, Any] = {}
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


def _select_identity(
    result: ResolutionResult,
    selection: str | None,
    policy: str = 'stop',
    maximum_contenders: int = 3,
    margin: float = 0.1,
) -> tuple[ProfessorProfile, tuple[str, str] | None]:
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
    if result.profile.status != ResolutionStatus.RESOLVED:
        if (
            policy == AmbiguityPolicy.COLLECT_CONTENDERS
            and result.profile.university_status != UniversityStatus.MISMATCH
        ):
            return contender_profile(result, maximum_contenders, margin), None
        raise IdentityConfirmationRequired('Explicit Author ID selection is required for this identity')
    return result.profile, None


def _is_pdf_report(saved: JSONObject) -> bool:
    return 'validation_scope' in saved and isinstance(saved.get('papers'), list)


def _paper_entries(saved: JSONObject) -> list[JSONObject]:
    collection = saved.get('collection', saved)
    papers = collection.get('all_papers', collection.get('papers'))
    if not isinstance(papers, list) or not all(isinstance(item, dict) for item in papers):
        raise ValueError('Expected a paper list')
    return papers


def metadata_audit(
    original: list[JSONObject], pdf: JSONObject | None = None, manifest: JSONObject | None = None
) -> JSONObject:
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
        for index, (before, after) in enumerate(zip(original, downstream.get('papers', []), strict=False)):
            for field in audited_fields:
                value = before.get(field)
                if value not in (None, '', []) and after.get(field) != value:
                    losses.append({'stage': stage, 'entry_index': index, 'field': field})
        if len(downstream.get('papers', [])) < len(original):
            losses.append({'stage': stage, 'field': 'paper_entries', 'error': 'Missing entries'})
    return {'missing_input_fields': missing, 'transmission_losses': losses}


class MissingStageService(RuntimeError):
    """A requested stage needs a service the workflow was built without."""


def _required(service: Any, name: str) -> Any:
    if service is None:
        raise MissingStageService(f'This workflow has no {name}; build it with composition.ServiceContainer')
    return service


IDENTITY_STAGE = 'Resolving professor identity'
PDF_STAGE = 'Finding PDF URLs'
DOWNLOAD_STAGE = 'Downloading PDFs'
COLLECTION_STAGES = ('Collecting papers', 'Deduplicating papers', 'Validating ownership')


@dataclass
class RunRequest:
    """What one workflow run was asked to do."""

    query: ProfessorQuery | None
    saved: JSONObject | None
    mode: str
    selected_author: str | None
    include_needs_review: bool
    maximum_pdf_papers: int | None
    maximum_downloads: int | None


@dataclass
class RunContext:
    """State shared by the stages of one run: inputs, the output directory, the report and stage artifacts."""

    request: RunRequest
    root: Path
    report: JSONObject
    progress: Callable[..., None]
    archive: JSONObject | None = None
    pdf: JSONObject | None = None
    manifest: JSONObject | None = None
    halted: bool = False
    compact_artifacts: bool = True

    def write_artifact(self, key: str, filename: str, data: Any) -> None:
        write_json(self.root / filename, data, compact=self.compact_artifacts)
        self.report['artifacts'][key] = filename


class WorkflowStage(ABC):
    """One checkpointed step of the pipeline; it runs only in the modes it declares."""

    name: ClassVar[str]
    modes: ClassVar[frozenset[str]] = frozenset(MODES)

    def applies(self, context: RunContext) -> bool:
        return context.request.mode in self.modes

    @abstractmethod
    def run(self, context: RunContext) -> None:
        """Advance ``context``; set ``context.halted`` to stop the remaining stages."""


class IdentityAndCollectionStage(WorkflowStage):
    """Resolve (or reuse) the identity, then collect, deduplicate and validate its papers (or reuse them)."""

    name = IDENTITY_STAGE

    def __init__(self, resolver: Any, collector: Any, collection_config: Any, matching_config: Any) -> None:
        self.resolver = resolver
        self.collector = collector
        self.collection_config = collection_config
        self.matching_config = matching_config

    def run(self, context: RunContext) -> None:
        archive, pdf = self._prepare_input(context)
        if context.report['run_status'] == RunStatus.IDENTITY_CONFIRMATION_REQUIRED:
            context.halted = True
            return
        context.archive, context.pdf = archive, pdf
        context.write_artifact('papers', 'papers.json', archive)
        context.report['collection_statistics'] = archive.get('collection', archive).get('statistics', {})

    def _prepare_input(self, context: RunContext) -> tuple[JSONObject, JSONObject | None]:
        request, report = context.request, context.report
        query, saved, selection = request.query, request.saved, request.selected_author
        if saved is not None and (_is_pdf_report(saved) or 'collection' in saved):
            if selection:
                raise ValueError('Saved paper ownership is tied to its original identity; select from Resolver JSON')
            # Stages only read the archive; the PDF report gets a shallow copy because two top-level keys change.
            # Neither the caller's checkpoint nor the archive is mutated, so no deep copy is needed.
            archive = saved.get('collection_archive') or saved
            pdf = dict(saved) if _is_pdf_report(saved) else None
            if pdf is not None:
                pdf.pop('collection_archive', None)
            context.progress('Reusing collected papers')
            report['stages'].extend({'stage': stage, 'status': StageStatus.REUSED} for stage in COLLECTION_STAGES)
            identity = saved.get('identity_archive')
            if 'collection' in archive and (identity is None or 'profile' not in identity):
                saved_profile = asdict(saved_collection_profile(archive))
                identity = {
                    'profile': saved_profile,
                    'candidates': [saved_profile],
                    'source_errors': archive.get('source_errors') or {},
                    'ambiguity': archive.get('ambiguity'),
                }
            if identity:
                report['identity'] = deepcopy(identity)
                if pdf is not None:
                    pdf['identity_archive'] = deepcopy(identity)
                context.write_artifact('professor', 'professor.json', identity)
                for source, error in identity.get('source_errors', {}).items():
                    report['errors'].append({'stage': IDENTITY_STAGE, 'source': source, 'error': error})
            _collection_errors(archive, report)
            return archive, pdf
        if saved is not None and 'selected_author_ids' in saved and 'papers' in saved:
            if selection:
                raise ValueError('Cannot change identity for an existing collection')
            _collection_errors(saved, report)
            context.progress('Reusing collected papers')
            report['stages'].append({'stage': 'Collecting papers', 'status': StageStatus.REUSED})
            return saved, None
        context.progress(IDENTITY_STAGE)
        result = _required(self.resolver, 'resolver').resolve(query) if query else _resolution(saved or {})
        identity = result.to_dict()
        context.write_artifact('professor', 'professor.json', identity)
        report['identity'] = identity
        report['stages'].append(
            {'stage': IDENTITY_STAGE, 'status': StageStatus.COMPLETED if query else StageStatus.REUSED}
        )
        for source, error in result.source_errors.items():
            report['errors'].append({'stage': IDENTITY_STAGE, 'source': source, 'error': error})
        try:
            profile, selected = _select_identity(
                result,
                selection,
                self.collection_config.ambiguity_policy,
                self.collection_config.maximum_contenders,
                self.matching_config.ambiguity_margin,
            )
            if profile.status == ResolutionStatus.AMBIGUOUS_CONTENDERS:
                report['warnings'].append(
                    {
                        'stage': IDENTITY_STAGE,
                        'warning': 'Identity unconfirmed; competing profiles collected for review',
                    }
                )
        except IdentityConfirmationRequired as exc:
            report['run_status'] = RunStatus.IDENTITY_CONFIRMATION_REQUIRED
            report['errors'].append({'stage': IDENTITY_STAGE, 'error': str(exc)})
            return {}, None
        context.progress('Collecting papers')
        collection = _required(self.collector, 'collector').collect(
            profile, selected_author=selected, progress=context.progress
        )
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
        report['stages'].extend({'stage': stage, 'status': StageStatus.COMPLETED} for stage in COLLECTION_STAGES)
        _collection_errors(archive, report)
        return archive, None


class PDFDiscoveryStage(WorkflowStage):
    """Find and validate public PDF URLs (or reuse a saved PDF report)."""

    name = PDF_STAGE
    modes = frozenset({'pdf-urls', 'full'})

    def __init__(self, pdf_resolver: Any) -> None:
        self.pdf_resolver = pdf_resolver

    def run(self, context: RunContext) -> None:
        request, report = context.request, context.report
        context.progress(self.name)
        pdf = context.pdf
        if pdf is None:
            pdf = (
                _required(self.pdf_resolver, 'pdf_resolver')
                .resolve(
                    context.archive, request.include_needs_review, request.maximum_pdf_papers, progress=context.progress
                )
                .to_dict()
            )
            # The full archive keeps raw authorship and identity provenance when resuming.
            pdf['identity_archive'] = report.get('identity') or {}
        context.pdf = pdf
        reused = _is_pdf_report(request.saved or {})
        report['stages'].append({'stage': self.name, 'status': StageStatus.REUSED if reused else StageStatus.COMPLETED})
        context.write_artifact('pdf_urls', 'pdf_urls.json', pdf)
        report['pdf_statistics'] = pdf.get('statistics', {})
        for item in pdf['papers']:
            title = item.get('title')
            report['warnings'].extend(
                dict(warning, stage=self.name, title=title) for warning in item.get('warnings') or []
            )
            report['candidate_decisions'].extend(
                dict(decision, stage=self.name, title=title) for decision in item.get('candidate_decisions') or []
            )
            report['errors'].extend(
                dict(error, stage=self.name, title=title) for error in item.get('error_details') or []
            )
        write_json(context.root / 'report.json', report)


class DownloadStage(WorkflowStage):
    """Download, validate and file the PDFs, and measure whether the search found complete papers."""

    name = DOWNLOAD_STAGE
    modes = frozenset({'full'})

    def __init__(self, downloader: Any) -> None:
        self.downloader = downloader

    def run(self, context: RunContext) -> None:
        request, report = context.request, context.report
        context.progress(self.name)
        manifest = _required(self.downloader, 'downloader').download(
            context.pdf,
            context.root / 'papers',
            request.include_needs_review,
            request.maximum_downloads,
            manifest_path=context.root / 'manifest.json',
            progress=context.progress,
        )
        context.manifest = manifest
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
        report['stages'].append({'stage': self.name, 'status': StageStatus.COMPLETED})
        for item in manifest['papers']:
            for error in item.get('error_details') or []:
                report['errors'].append({'stage': self.name, 'title': item.get('title'), 'error': error})


class PaperSearchWorkflow:
    """Runs the pipeline stages in order, checkpointing each, and classifies the run's outcome."""

    def __init__(
        self,
        resolver: Any = None,
        collector: Any = None,
        pdf_resolver: Any = None,
        downloader: Any = None,
        progress: Callable[..., None] | None = None,
        collection_config: Any = DEFAULT_COLLECTION,
        matching_config: Any = DEFAULT_MATCHING,
        stages: Sequence[WorkflowStage] | None = None,
        storage: StorageConfig = DEFAULT_STORAGE,
    ) -> None:
        self.resolver = resolver
        self.collector = collector
        self.collection_config = collection_config
        self.matching_config = matching_config
        self.pdf_resolver = pdf_resolver
        self.downloader = downloader
        self.progress = progress
        self.current_stage = IDENTITY_STAGE
        self._stages = list(stages) if stages is not None else None
        self.storage = storage

    @property
    def stages(self) -> list[WorkflowStage]:
        """Injected stages, or the default pipeline over the configured services (read at run time)."""
        if self._stages is not None:
            return self._stages
        return [
            IdentityAndCollectionStage(self.resolver, self.collector, self.collection_config, self.matching_config),
            PDFDiscoveryStage(self.pdf_resolver),
            DownloadStage(self.downloader),
        ]

    def _progress(self, stage: str, completed: int | None = None, total: int | None = None) -> None:
        self.current_stage = stage
        if self.progress:
            self.progress(stage, completed, total)

    def _directory(self, output_dir: Any, name: str, resume_dir: Any) -> Path:
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
        query: ProfessorQuery | None = None,
        saved: JSONObject | None = None,
        output_dir: Any = 'output',
        mode: str = 'full',
        selected_author: str | None = None,
        include_needs_review: bool = False,
        maximum_pdf_papers: int | None = None,
        maximum_downloads: int | None = None,
        resume_dir: Any = None,
    ) -> JSONObject:
        if mode not in MODES:
            raise ValueError('Unknown workflow mode')
        if (query is None) == (saved is None):
            raise ValueError('Provide professor information or an input checkpoint')
        for limit in (maximum_pdf_papers, maximum_downloads):
            if limit is not None and limit < 1:
                raise ValueError('Workflow limits must be positive')
        request = RunRequest(
            query, saved, mode, selected_author, include_needs_review, maximum_pdf_papers, maximum_downloads
        )
        stored_identity = (saved or {}).get('identity_archive') or saved or {}
        identity = stored_identity.get('profile') or stored_identity.get('collection_identity') or stored_identity
        name = query.name if query else identity.get('name', 'saved_search')
        root = self._directory(output_dir, name, resume_dir)
        if (root / 'papers').is_symlink():
            raise ValueError('PDF directory must not be a symlink')
        (root / 'papers').mkdir(exist_ok=True)
        report: JSONObject = {
            'schema_version': 1,
            'report_type': 'paper_search_workflow',
            'mode': mode,
            'run_status': RunStatus.RUNNING,
            'output_directory': str(root),
            'stages': [],
            'errors': [],
            'artifacts': {},
            'limits': {'maximum_pdf_papers': maximum_pdf_papers, 'maximum_downloads': maximum_downloads},
            'include_needs_review': include_needs_review,
            'count_scope': 'retrieved_metadata_not_total_publications',
            'warnings': [],
            'candidate_decisions': [],
        }
        if query:
            report['query'] = asdict(query)
        write_json(root / 'report.json', report)
        context = RunContext(request, root, report, self._progress, compact_artifacts=self.storage.compact_artifacts)
        self.current_stage = IDENTITY_STAGE
        try:
            for stage in self.stages:
                if not stage.applies(context):
                    continue
                self.current_stage = stage.name
                stage.run(context)
                if context.halted:
                    return report
            self._finish(context)
        except IdentityConfirmationRequired as exc:
            report['run_status'] = RunStatus.IDENTITY_CONFIRMATION_REQUIRED
            report['errors'].append({'stage': self.current_stage, 'error': str(exc)})
        except KeyboardInterrupt:
            report['run_status'] = RunStatus.INTERRUPTED
            report['errors'].append(
                {'stage': self.current_stage, 'error': 'Interrupted; use the saved checkpoint to continue'}
            )
            raise
        except Exception as exc:
            logger.exception('Workflow failed during stage %s', self.current_stage)
            report['run_status'] = RunStatus.FAILED
            report['errors'].append({'stage': self.current_stage, 'error': str(exc)})
        finally:
            report['outcomes'] = workflow_outcomes(report)
            report['similarity_caches'] = similarity_cache_statistics()
            write_json(root / 'report.json', report)
        return report

    def _finish(self, context: RunContext) -> None:
        report, pdf, manifest = context.report, context.pdf, context.manifest
        report['metadata_audit'] = metadata_audit(_paper_entries(context.archive or {}), pdf, manifest)
        review_files = bool(manifest and manifest['statistics']['needs_review_papers'])
        incomplete_pdf_search = bool(pdf and pdf.get('statistics', {}).get('incomplete_search_papers'))
        partial = bool(
            report['errors'] or report['metadata_audit']['transmission_losses'] or review_files or incomplete_pdf_search
        )
        report['run_status'] = RunStatus.PARTIAL if partial else RunStatus.COMPLETED
        self._progress('Generating report')


def _collection_errors(archive: JSONObject, report: JSONObject) -> None:
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
