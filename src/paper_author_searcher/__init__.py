from .config import CollectionConfig, HttpConfig, MatchingConfig, SourceConfig, PDFConfig, DownloadConfig
from .models import AuthorCandidate, ProfessorProfile, ProfessorQuery, ResolutionResult
from .resolver import ProfessorResolver
from .paper_collector import PaperCollector
from .paper_ownership import PaperOwnershipValidator
from .models import Paper, PaperRecord, PaperCollectionResult
from .evaluation import Evaluator
from .evaluation_matching import GroundTruth
from .pdf_resolver import PDFResolver
from .models import PDFCandidate, PDFProviderResult, PaperPDFResult, PDFResolutionResult
from .pdf_downloader import PDFDownloader
from .pdf_file_validation import PDFFileValidator
from .models import PDFFileValidation, PaperDownloadResult
from .workflow import PaperSearchWorkflow
from .performance_evaluation import PerformanceEvaluator, compare_performance

__all__ = [
    "PerformanceEvaluator", "compare_performance",
    "PaperSearchWorkflow",
    "PDFDownloader", "DownloadConfig", "PDFFileValidator", "PDFFileValidation", "PaperDownloadResult",
    "PDFResolver", "PDFConfig", "PDFCandidate", "PDFProviderResult", "PaperPDFResult", "PDFResolutionResult",
    "Evaluator",
    "GroundTruth",
    "PaperOwnershipValidator",
    "PaperCollector",
    "Paper",
    "PaperRecord",
    "PaperCollectionResult",
    "CollectionConfig",
    "ProfessorQuery",
    "ProfessorProfile",
    "ResolutionResult",
    "AuthorCandidate",
    "ProfessorResolver",
    "MatchingConfig",
    "HttpConfig",
    "SourceConfig",
]
