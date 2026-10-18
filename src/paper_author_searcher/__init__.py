"""Find a researcher's papers with verified ownership and, where lawful, their public full text."""

import logging

from .config import CollectionConfig, DownloadConfig, HttpConfig, MatchingConfig, PDFConfig, SourceConfig
from .evaluation import Evaluator
from .evaluation_matching import GroundTruth
from .models import (
    AuthorCandidate,
    Paper,
    PaperCollectionResult,
    PaperDownloadResult,
    PaperPDFResult,
    PaperRecord,
    PDFCandidate,
    PDFFileValidation,
    PDFProviderResult,
    PDFResolutionResult,
    ProfessorProfile,
    ProfessorQuery,
    ResolutionResult,
)
from .paper_collector import PaperCollector
from .paper_ownership import PaperOwnershipValidator
from .pdf_downloader import PDFDownloader
from .pdf_file_validation import PDFFileValidator
from .pdf_resolver import PDFResolver
from .performance_evaluation import PerformanceEvaluator, compare_performance
from .resolver import ProfessorResolver
from .workflow import PaperSearchWorkflow

# Library logging stays silent until an application configures it (see logging_config).
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    'AuthorCandidate',
    'CollectionConfig',
    'DownloadConfig',
    'Evaluator',
    'GroundTruth',
    'HttpConfig',
    'MatchingConfig',
    'PDFCandidate',
    'PDFConfig',
    'PDFDownloader',
    'PDFFileValidation',
    'PDFFileValidator',
    'PDFProviderResult',
    'PDFResolutionResult',
    'PDFResolver',
    'Paper',
    'PaperCollectionResult',
    'PaperCollector',
    'PaperDownloadResult',
    'PaperOwnershipValidator',
    'PaperPDFResult',
    'PaperRecord',
    'PaperSearchWorkflow',
    'PerformanceEvaluator',
    'ProfessorProfile',
    'ProfessorQuery',
    'ProfessorResolver',
    'ResolutionResult',
    'SourceConfig',
    'compare_performance',
]
