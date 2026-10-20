"""Composition root: the one place concrete services are chosen and wired together.

Library classes receive their collaborators; only this module decides which implementations are used. Entry points
(CLIs, the benchmark, a future HTTP service) translate their inputs into ``RunOptions`` and ask a
``ServiceContainer`` for a ready workflow, so wiring is never duplicated per entry point.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from functools import cached_property
from typing import Any, ClassVar

from .config import CollectionConfig, DownloadConfig, PDFConfig
from .http import HttpClient
from .http_budget import budgeted_client
from .http_cache import cached_client
from .identity_anchors import AnchorFinder, ScholarlyAnchorFinder
from .paper_collector import PaperCollector
from .paper_ownership import PaperOwnershipValidator
from .pdf_resolver import PDFResolver
from .pdf_resource_requests import PublicResourceRequests
from .pdf_validation import PDFURLValidator
from .precision_guards import PrecisionGuard
from .progress import ProgressReporter
from .resolver import ProfessorResolver
from .settings import Settings
from .source_catalog import IDENTITY_SOURCES, PAPER_SOURCES, PDF_SOURCES
from .workflow import PaperSearchWorkflow

FULL_MODE = 'full'
BYTES_PER_MEGABYTE = 1024 * 1024
DEFAULT_COST_LEDGER = 'default'


@dataclass(frozen=True)
class RunOptions:
    """Per-run choices layered over ``Settings``; ``None`` means "use the configured value"."""

    mode: str = FULL_MODE
    timeout: float | None = None
    retries: int | None = None
    max_pages: int | None = None
    page_size: int | None = None
    max_file_mb: float | None = None
    unpaywall_email: str | None = None
    bibliography_urls: tuple[str, ...] = ()
    ambiguity_policy: str | None = None
    llm_adjudication: bool = False
    max_cost_usd: float | None = None
    cost_ledger: str = DEFAULT_COST_LEDGER
    http_cache_dir: str | None = None
    http_cache_mode: str | None = None
    verbose: bool = False

    # Attribute names some entry points use for the same option.
    ALIASES: ClassVar[Mapping[str, str]] = {'bibliography_urls': 'bibliography_url'}

    @classmethod
    def from_namespace(cls, namespace: Any) -> 'RunOptions':
        """Build from any object with matching attributes (argparse namespace, benchmark RunSettings)."""
        values: dict[str, Any] = {}
        for item in fields(cls):
            for name in (item.name, cls.ALIASES.get(item.name)):
                if name and hasattr(namespace, name):
                    value = getattr(namespace, name)
                    values[item.name] = tuple(value) if isinstance(value, list) else value
                    break
        values['mode'] = values.get('mode') or FULL_MODE
        values['cost_ledger'] = values.get('cost_ledger') or DEFAULT_COST_LEDGER
        values['bibliography_urls'] = tuple(values.get('bibliography_urls') or ())
        return cls(**values)


class ServiceContainer:
    """Builds each service once per container from ``Settings`` and ``RunOptions``."""

    def __init__(
        self,
        settings: Settings,
        options: RunOptions | None = None,
        adjudicator_factory: Callable[[], Any] | None = None,
        anchor_finder: AnchorFinder | None = None,
    ) -> None:
        self.settings = settings
        self.options = options or RunOptions()
        self.adjudicator_factory = adjudicator_factory or _claude_adjudicator
        # Overrides how caller hints become anchors (an offline replay supplies the anchors a run recorded).
        self.anchor_finder_override = anchor_finder

    def with_settings(self, settings: Settings) -> 'ServiceContainer':
        """A fresh container (new clients, providers and caches) for a variant of the settings."""
        return ServiceContainer(settings, self.options, self.adjudicator_factory, self.anchor_finder_override)

    # Transport ---------------------------------------------------------------------------------------------------

    @cached_property
    def http_client(self) -> Any:
        """cache -> budget -> network: cached replays are free; only real requests are priced."""
        options = self.options
        network = HttpClient(timeout=options.timeout, retries=options.retries, config=self.settings.http)
        metered = budgeted_client(network, options.max_cost_usd, options.cost_ledger)
        return cached_client(metered, options.http_cache_dir, options.http_cache_mode)

    # Identity ----------------------------------------------------------------------------------------------------

    @cached_property
    def identity_providers(self) -> list[Any]:
        return IDENTITY_SOURCES.create(self.settings.sources.identity_sources, self)

    @cached_property
    def anchor_finder(self) -> AnchorFinder:
        if self.anchor_finder_override is not None:
            return self.anchor_finder_override
        settings = self.settings
        return ScholarlyAnchorFinder(
            self.http_client,
            source_config=settings.sources,
            matching_config=settings.matching,
            anchor_config=settings.anchors,
            openalex_api_key=settings.credentials.openalex_api_key,
            semantic_scholar_api_key=settings.credentials.semantic_scholar_api_key,
        )

    @cached_property
    def resolver(self) -> ProfessorResolver:
        adjudicator = self.adjudicator_factory() if self.options.llm_adjudication else None
        return ProfessorResolver(
            self.identity_providers,
            config=self.settings.matching,
            anchor_finder=self.anchor_finder,
            adjudicator=adjudicator,
        )

    # Collection --------------------------------------------------------------------------------------------------

    @cached_property
    def collection_config(self) -> CollectionConfig:
        options, config = self.options, self.settings.collection
        changes: dict[str, Any] = {}
        if options.max_pages is not None:
            changes['maximum_pages'] = options.max_pages
        if options.page_size is not None:
            changes.update(openalex_page_size=options.page_size, semantic_scholar_page_size=options.page_size)
        if options.ambiguity_policy is not None:
            changes['ambiguity_policy'] = options.ambiguity_policy
        return replace(config, **changes)

    @cached_property
    def paper_providers(self) -> list[Any]:
        """One collector per enabled identity source that has a registered paper source."""
        return [
            PAPER_SOURCES.factory(provider.name)(self, provider)
            for provider in self.identity_providers
            if provider.name in PAPER_SOURCES
        ]

    @cached_property
    def precision_guard(self) -> PrecisionGuard | None:
        config = self.settings.precision
        if not config.enabled:
            return None
        return PrecisionGuard(config, institution_similarity=self.settings.ownership.institution_similarity)

    @cached_property
    def ownership_validator(self) -> PaperOwnershipValidator:
        return PaperOwnershipValidator(self.settings.ownership, self.precision_guard)

    @cached_property
    def collector(self) -> PaperCollector:
        return PaperCollector(
            self.paper_providers,
            self.ownership_validator,
            self.settings.deduplication,
            concurrency=dict(self.collection_config.source_concurrency),
            raw_metadata=self.collection_config.raw_metadata,
        )

    # Full text ---------------------------------------------------------------------------------------------------

    @cached_property
    def pdf_config(self) -> PDFConfig:
        configured = self.settings.pdf.bibliography_urls
        extra = tuple(url for url in self.options.bibliography_urls if url not in configured)
        return replace(self.settings.pdf, bibliography_urls=configured + extra)

    @cached_property
    def resource_requests(self) -> PublicResourceRequests:
        """Shared scheduler (rate limits, cooldowns, page cache) for every public-resource request."""
        return PublicResourceRequests(self.http_client, self.pdf_config)

    @cached_property
    def pdf_providers(self) -> list[Any]:
        return PDF_SOURCES.create(self.pdf_config.sources, self)

    @cached_property
    def pdf_validator(self) -> PDFURLValidator:
        return PDFURLValidator(self.resource_requests, self.pdf_config)

    @cached_property
    def pdf_resolver(self) -> PDFResolver:
        return PDFResolver(self.pdf_providers, self.pdf_validator, self.resource_requests, self.pdf_config)

    @cached_property
    def download_config(self) -> DownloadConfig:
        if self.options.max_file_mb is None:
            return self.settings.download
        return replace(self.settings.download, maximum_file_bytes=int(self.options.max_file_mb * BYTES_PER_MEGABYTE))

    @cached_property
    def downloader(self) -> Any:
        from .pdf_downloader import PDFDownloader  # deferred: needs the optional "download" extra (pypdf)

        # The file validator is a local, configuration-driven parser the downloader derives from its config.
        return PDFDownloader(client=self.http_client, config=self.download_config, storage=self.settings.storage)

    # Pipeline ----------------------------------------------------------------------------------------------------

    def workflow(self, progress: Callable | None = None) -> PaperSearchWorkflow:
        return PaperSearchWorkflow(
            resolver=self.resolver,
            collector=self.collector,
            pdf_resolver=self.pdf_resolver,
            downloader=self.downloader if self.options.mode == FULL_MODE else None,
            progress=progress if progress is not None else ProgressReporter(self.options.verbose),
            collection_config=self.collection_config,
            matching_config=self.settings.matching,
            storage=self.settings.storage,
        )


def _claude_adjudicator() -> Any:
    from .identity_adjudication import ClaudeIdentityAdjudicator  # deferred: optional "llm" extra

    return ClaudeIdentityAdjudicator()
