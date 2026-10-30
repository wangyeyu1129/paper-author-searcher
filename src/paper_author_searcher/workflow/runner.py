"""Run the stages in order, checkpoint each result and write the run report."""

import logging
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_author_searcher.config import (
    DEFAULT_COLLECTION,
    DEFAULT_MATCHING,
    DEFAULT_STORAGE,
    StorageConfig,
)
from paper_author_searcher.json_types import JSONObject
from paper_author_searcher.jsonio import read_json, write_json
from paper_author_searcher.matching import similarity_cache_statistics
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.paper_collector import IdentityConfirmationRequired
from paper_author_searcher.performance_diagnostics import workflow_outcomes
from paper_author_searcher.status import RunStatus

from .checkpoints import _paper_entries, metadata_audit
from .stages import (
    IDENTITY_STAGE,
    MODES,
    DownloadStage,
    IdentityAndCollectionStage,
    PDFDiscoveryStage,
    RunContext,
    RunRequest,
    WorkflowStage,
)

logger = logging.getLogger(__name__)


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
