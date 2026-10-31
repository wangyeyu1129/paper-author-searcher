"""Unified application settings: the single place configuration and credentials are loaded.

Precedence, lowest to highest:

1. code defaults (the section dataclasses in ``config``);
2. a JSON settings file (``--settings`` or ``$PAPER_SEARCH_SETTINGS``), e.g. ``{"http": {"timeout": 30}}``;
3. environment overrides ``PAPER_SEARCH__<SECTION>__<FIELD>``, e.g. ``PAPER_SEARCH__HTTP__TIMEOUT=30``.

Values are coerced to each field's declared type and every section re-runs its own validation. Credentials come
only from their conventional environment variables, never from a settings file, and are never rendered.
"""

import argparse
import json
import os
import re
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, ClassVar, Union

from .config import (
    DEFAULT_ANCHORS,
    DEFAULT_CATALOGS,
    DEFAULT_COLLECTION,
    DEFAULT_CS_SOURCES,
    DEFAULT_DBLP_DUMP,
    DEFAULT_DEDUPLICATION,
    DEFAULT_DISAMBIGUATION,
    DEFAULT_DISCOVERY,
    DEFAULT_DOWNLOAD,
    DEFAULT_EXTRACTION,
    DEFAULT_HTTP,
    DEFAULT_LISTED_WORKS,
    DEFAULT_MATCHING,
    DEFAULT_NAME_SEARCHES,
    DEFAULT_OWNERSHIP,
    DEFAULT_PDF,
    DEFAULT_PRECISION,
    DEFAULT_PROFILE_LINKS,
    DEFAULT_SERVICE,
    DEFAULT_SOURCES,
    DEFAULT_STORAGE,
    AnchorConfig,
    CatalogConfig,
    CollectionConfig,
    CSSourcesConfig,
    DblpDumpConfig,
    DeduplicationConfig,
    DisambiguationConfig,
    DiscoveryConfig,
    DownloadConfig,
    ExtractionConfig,
    HttpConfig,
    ListedWorksConfig,
    MatchingConfig,
    NameSearchConfig,
    OwnershipConfig,
    PDFConfig,
    PrecisionGuardConfig,
    ProfileLinkConfig,
    ServiceConfig,
    SourceConfig,
    StorageConfig,
)
from .redaction import register_secrets

SETTINGS_FILE_VARIABLE = 'PAPER_SEARCH_SETTINGS'
OVERRIDE_PREFIX = 'PAPER_SEARCH__'
OVERRIDE_SEPARATOR = '__'
_EMAIL = re.compile(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+')
_TRUE = frozenset({'1', 'true', 'yes', 'on'})
_FALSE = frozenset({'0', 'false', 'no', 'off'})
_NONE = frozenset({'', 'none', 'null'})


# RFC 5321.
MAXIMUM_EMAIL_LENGTH = 254


# PAPER_SEARCH__<SECTION>__<FIELD>
OVERRIDE_PARTS = 2


def validate_contact_email(value: str | None) -> str | None:
    """Return a stripped contact email, ``None`` when unset, or raise ``ValueError`` when malformed."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    if len(value) > MAXIMUM_EMAIL_LENGTH or not _EMAIL.fullmatch(value):
        raise ValueError('Contact email must be a plain, valid email address')
    return value


@dataclass(frozen=True)
class Credentials:
    """API keys and contact emails. ``repr`` hides every value; ``describe`` reports only presence."""

    openalex_api_key: str | None = field(default=None, repr=False)
    semantic_scholar_api_key: str | None = field(default=None, repr=False)
    unpaywall_email: str | None = field(default=None, repr=False)
    crossref_email: str | None = field(default=None, repr=False)
    # OpenReview's profile and note APIs refuse guests; a token of the operator's own account enables them.
    openreview_token: str | None = field(default=None, repr=False)
    # CORE (core.ac.uk) aggregates institutional repositories; its API needs a key.
    core_api_key: str | None = field(default=None, repr=False)
    # Web search providers for homepage discovery; only the configured one is used.
    brave_api_key: str | None = field(default=None, repr=False)
    tavily_api_key: str | None = field(default=None, repr=False)
    exa_api_key: str | None = field(default=None, repr=False)
    # Claude for publication-list extraction (identity_anchors pages); unset disables it.
    anthropic_api_key: str | None = field(default=None, repr=False)
    # The search service's S3-compatible file store (service.file_store = 's3').
    s3_access_key_id: str | None = field(default=None, repr=False)
    s3_secret_access_key: str | None = field(default=None, repr=False)

    ENVIRONMENT: ClassVar[Mapping[str, str]] = {
        'openalex_api_key': 'OPENALEX_API_KEY',
        'semantic_scholar_api_key': 'SEMANTIC_SCHOLAR_API_KEY',
        'unpaywall_email': 'UNPAYWALL_EMAIL',
        'crossref_email': 'CROSSREF_MAILTO',
        'openreview_token': 'OPENREVIEW_TOKEN',
        'core_api_key': 'CORE_API_KEY',
        'brave_api_key': 'BRAVE_API_KEY',
        'tavily_api_key': 'TAVILY_API_KEY',
        'exa_api_key': 'EXA_API_KEY',
        'anthropic_api_key': 'ANTHROPIC_API_KEY',
        's3_access_key_id': 'AWS_ACCESS_KEY_ID',
        's3_secret_access_key': 'AWS_SECRET_ACCESS_KEY',
    }

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> 'Credentials':
        environ = os.environ if environ is None else environ
        values = {name: (environ.get(variable) or '').strip() or None for name, variable in cls.ENVIRONMENT.items()}
        return cls(**values)

    def describe(self) -> dict[str, str]:
        return {item.name: 'set' if getattr(self, item.name) else 'unset' for item in fields(self)}


@dataclass(frozen=True)
class Settings:
    """Every tunable of the search service, grouped by concern. Immutable; derive variants with ``replace``."""

    matching: MatchingConfig = DEFAULT_MATCHING
    anchors: AnchorConfig = DEFAULT_ANCHORS
    disambiguation: DisambiguationConfig = DEFAULT_DISAMBIGUATION
    http: HttpConfig = DEFAULT_HTTP
    sources: SourceConfig = DEFAULT_SOURCES
    collection: CollectionConfig = DEFAULT_COLLECTION
    ownership: OwnershipConfig = DEFAULT_OWNERSHIP
    precision: PrecisionGuardConfig = DEFAULT_PRECISION
    listed_works: ListedWorksConfig = DEFAULT_LISTED_WORKS
    cs_sources: CSSourcesConfig = DEFAULT_CS_SOURCES
    name_searches: NameSearchConfig = DEFAULT_NAME_SEARCHES
    catalogs: CatalogConfig = DEFAULT_CATALOGS
    dblp_dump: DblpDumpConfig = DEFAULT_DBLP_DUMP
    profile_links: ProfileLinkConfig = DEFAULT_PROFILE_LINKS
    discovery: DiscoveryConfig = DEFAULT_DISCOVERY
    extraction: ExtractionConfig = DEFAULT_EXTRACTION
    deduplication: DeduplicationConfig = DEFAULT_DEDUPLICATION
    pdf: PDFConfig = DEFAULT_PDF
    download: DownloadConfig = DEFAULT_DOWNLOAD
    storage: StorageConfig = DEFAULT_STORAGE
    service: ServiceConfig = DEFAULT_SERVICE
    credentials: Credentials = field(default_factory=Credentials)

    SECRET_SECTIONS: ClassVar[frozenset] = frozenset({'credentials'})

    @classmethod
    def load(cls, path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> 'Settings':
        """Defaults, then the settings file, then ``PAPER_SEARCH__`` overrides, then credentials."""
        environ = os.environ if environ is None else environ
        path = path or environ.get(SETTINGS_FILE_VARIABLE) or None
        settings = cls.from_mapping(_read_json(Path(path)) if path else {})
        settings = settings.with_overrides(_environment_overrides(environ))
        credentials = Credentials.from_environment(environ)
        register_secrets(getattr(credentials, item.name) for item in fields(credentials))
        return replace(settings, credentials=credentials)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> 'Settings':
        return cls().with_overrides(data)

    def with_overrides(self, data: Mapping[str, Any]) -> 'Settings':
        """Return a copy with ``{section: {field: value}}`` applied; unknown names are errors, not silently lost."""
        if not isinstance(data, Mapping):
            raise ValueError('Settings must be an object of sections')
        sections = {item.name for item in fields(self)} - self.SECRET_SECTIONS
        unknown = set(data) - sections
        if unknown & self.SECRET_SECTIONS:
            raise ValueError('Credentials are read from the environment only, never from settings files')
        if unknown:
            raise ValueError(f'Unknown settings sections: {sorted(unknown)}')
        changes = {name: _apply_section(getattr(self, name), name, values) for name, values in data.items()}
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Effective configuration for logs and run reports; credentials appear only as set/unset."""
        result: dict[str, Any] = {}
        for item in fields(self):
            section = getattr(self, item.name)
            if item.name in self.SECRET_SECTIONS:
                result[item.name] = section.describe()
            else:
                result[item.name] = {entry.name: getattr(section, entry.name) for entry in fields(section)}
        return result


def _read_json(path: Path) -> Mapping[str, Any]:
    with path.open(encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f'Settings file {path} must contain a JSON object')
    return data


def _environment_overrides(environ: Mapping[str, str]) -> dict[str, dict[str, str]]:
    overrides: dict[str, dict[str, str]] = {}
    for variable, value in environ.items():
        if not variable.startswith(OVERRIDE_PREFIX):
            continue
        parts = variable[len(OVERRIDE_PREFIX) :].lower().split(OVERRIDE_SEPARATOR)
        if len(parts) != OVERRIDE_PARTS or not all(parts):
            raise ValueError(f'{variable} must look like {OVERRIDE_PREFIX}<SECTION>{OVERRIDE_SEPARATOR}<FIELD>')
        overrides.setdefault(parts[0], {})[parts[1]] = value
    return overrides


def _apply_section(section: Any, name: str, values: Mapping[str, Any]) -> Any:
    if not isinstance(values, Mapping):
        raise ValueError(f'Settings section {name!r} must be an object')
    hints = typing.get_type_hints(type(section))
    known = {item.name for item in fields(section)}
    unknown = set(values) - known
    if unknown:
        raise ValueError(f'Unknown settings in {name!r}: {sorted(unknown)}')
    try:
        coerced = {key: _coerce(value, hints[key]) for key, value in values.items()}
        return replace(section, **coerced)  # re-runs the section's __post_init__ validation
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid settings in {name!r}: {exc}') from exc


def _coerce(value: Any, annotation: Any) -> Any:
    """Convert JSON values or environment strings to ``annotation`` (scalars, Optional, Tuple, nested Tuple)."""
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)
    if origin in (Union, types.UnionType):  # Optional[X] and X | None
        options = [option for option in arguments if option is not type(None)]
        if value is None or (isinstance(value, str) and value.strip().lower() in _NONE):
            return None
        return _coerce(value, options[0])
    if origin is tuple:
        if isinstance(value, str):
            text = value.strip()
            value = json.loads(text) if text.startswith('[') else [item.strip() for item in text.split(',') if item]
        if not isinstance(value, list | tuple):
            raise ValueError(f'expected a list, got {value!r}')
        item_type = arguments[0] if arguments else Any
        return tuple(_coerce(item, item_type) for item in value)
    if annotation is bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE | _FALSE:
            return text in _TRUE
        raise ValueError(f'expected a boolean, got {value!r}')
    if annotation in (int, float):
        if isinstance(value, bool):
            raise ValueError(f'expected a number, got {value!r}')
        number = annotation(value)
        if annotation is int and isinstance(value, float) and not value.is_integer():
            raise ValueError(f'expected an integer, got {value!r}')
        return number
    if annotation is str:
        if not isinstance(value, str):
            raise ValueError(f'expected a string, got {value!r}')
        return value
    return value


def add_settings_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--settings',
        type=Path,
        default=None,
        help=f'JSON settings file (default: ${SETTINGS_FILE_VARIABLE}); '
        f'{OVERRIDE_PREFIX}<SECTION>{OVERRIDE_SEPARATOR}<FIELD> variables override it',
    )


def load_settings(arguments: Any = None) -> Settings:
    """Settings for a command: its ``--settings`` file (if any), the environment and its credentials."""
    return Settings.load(getattr(arguments, 'settings', None))
