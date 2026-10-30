"""Compose existing stages and checkpoint their results without changing decisions.

checkpoints reads saved results and chooses the identity to collect for; stages holds the identity, PDF and
download stages; runner chains them and writes the run directory."""

from .checkpoints import (
    _is_pdf_report,
    _paper_entries,
    _profile,
    _resolution,
    _select_identity,
    contender_profile,
    load_checkpoint,
    metadata_audit,
)
from .runner import PaperSearchWorkflow
from .stages import (
    COLLECTION_STAGES,
    DOWNLOAD_STAGE,
    IDENTITY_STAGE,
    MODES,
    PDF_STAGE,
    DownloadStage,
    IdentityAndCollectionStage,
    MissingStageService,
    PDFDiscoveryStage,
    RunContext,
    RunRequest,
    WorkflowStage,
    _collection_errors,
    _required,
)

__all__ = [
    'COLLECTION_STAGES',
    'DOWNLOAD_STAGE',
    'IDENTITY_STAGE',
    'MODES',
    'PDF_STAGE',
    'DownloadStage',
    'IdentityAndCollectionStage',
    'MissingStageService',
    'PDFDiscoveryStage',
    'PaperSearchWorkflow',
    'RunContext',
    'RunRequest',
    'WorkflowStage',
    '_collection_errors',
    '_is_pdf_report',
    '_paper_entries',
    '_profile',
    '_required',
    '_resolution',
    '_select_identity',
    'contender_profile',
    'load_checkpoint',
    'metadata_audit',
]
