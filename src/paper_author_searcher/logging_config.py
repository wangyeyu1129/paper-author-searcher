"""Process-wide logging for console entry points.

Library modules only call ``logging.getLogger(__name__)``; handlers, levels and formats are chosen once, here, by
the process that owns stderr. Command results still go to stdout, so diagnostics never corrupt piped output.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import IO

from .redaction import RedactingFilter

PACKAGE_LOGGER = 'paper_author_searcher'
LEVEL_ENVIRONMENT_VARIABLE = 'PAPER_SEARCH_LOG_LEVEL'
FORMAT_ENVIRONMENT_VARIABLE = 'PAPER_SEARCH_LOG_FORMAT'
DEFAULT_LEVEL = 'INFO'
LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')
TEXT_FORMAT = '%(asctime)s %(levelname)-7s %(name)s: %(message)s'
TEXT = 'text'
JSON = 'json'
FORMATS = (TEXT, JSON)

# LogRecord attributes that are not caller-supplied ``extra`` fields.
_RESERVED = frozenset(vars(logging.LogRecord('', 0, '', 0, '', (), None))) | {'message', 'asctime'}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, carrying any ``extra=`` fields, for log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            'time': datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        entry.update({key: value for key, value in vars(record).items() if key not in _RESERVED})
        if record.exc_info:
            entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def add_logging_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group('logging')
    group.add_argument(
        '--log-level',
        choices=LEVELS,
        type=str.upper,
        default=None,
        help=f'diagnostics written to stderr (default: ${LEVEL_ENVIRONMENT_VARIABLE} or {DEFAULT_LEVEL})',
    )
    group.add_argument(
        '--log-format',
        choices=FORMATS,
        default=None,
        help=f'stderr log format (default: ${FORMAT_ENVIRONMENT_VARIABLE} or {TEXT})',
    )


def configure_logging(level: str | None = None, fmt: str | None = None, stream: IO | None = None) -> None:
    """Install a single stderr handler on the package logger; safe to call more than once."""
    level = (level or os.environ.get(LEVEL_ENVIRONMENT_VARIABLE) or DEFAULT_LEVEL).upper()
    fmt = (fmt or os.environ.get(FORMAT_ENVIRONMENT_VARIABLE) or TEXT).lower()
    if level not in LEVELS:
        raise ValueError(f'log level must be one of {", ".join(LEVELS)}')
    if fmt not in FORMATS:
        raise ValueError(f'log format must be one of {", ".join(FORMATS)}')
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == JSON else logging.Formatter(TEXT_FORMAT))
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger(PACKAGE_LOGGER)
    for existing in [item for item in logger.handlers if not isinstance(item, logging.NullHandler)]:
        logger.removeHandler(existing)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def configure_from_arguments(arguments: argparse.Namespace) -> None:
    configure_logging(getattr(arguments, 'log_level', None), getattr(arguments, 'log_format', None))


def log_isolated_failure(logger: logging.Logger, message: str, *args: object) -> None:
    """Warn about a failure that was contained and recorded in a result; attach the traceback only at DEBUG.

    Call from inside the ``except`` block that isolated the failure.
    """
    logger.warning(message, *args, exc_info=logger.isEnabledFor(logging.DEBUG))
