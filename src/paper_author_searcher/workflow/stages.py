"""The workflow stages: identity and collection, PDF discovery, download."""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar

from paper_author_searcher.json_types import JSONObject
from paper_author_searcher.jsonio import write_json
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.paper_collector import IdentityConfirmationRequired
from paper_author_searcher.paper_reanalysis import saved_collection_profile
from paper_author_searcher.status import WEB_SEARCH_OFF, ResolutionStatus, RunStatus, StageStatus

from .checkpoints import _is_pdf_report, _resolution, _select_identity

logger = logging.getLogger(__name__)

MODES = ('metadata', 'pdf-urls', 'full')


class MissingStageService(RuntimeError):
    """A requested stage needs a service the workflow was built without."""


def _required(service: Any, name: str) -> Any:
    if service is None:
        raise MissingStageService(f'This workflow has no {name}; build it with composition.ServiceContainer')
    return service


WEB_SEARCH_OFF_MESSAGE = (
    'Web search is not enabled (set TAVILY_API_KEY, BRAVE_API_KEY or EXA_API_KEY): homepages and PDFs that only '
    'other websites host are not found, so full-text coverage is lower'
)
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
                'requested_name': profile.requested_name,
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
            if not self.pdf_resolver.fallbacks:
                logger.warning(WEB_SEARCH_OFF_MESSAGE)
                report['warnings'].append(
                    {'stage': self.name, 'code': WEB_SEARCH_OFF, 'warning': WEB_SEARCH_OFF_MESSAGE}
                )
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
