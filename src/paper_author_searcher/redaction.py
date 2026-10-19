"""Keep credentials out of logs, error messages and saved reports.

Two layers: query parameters that carry secrets (``api_key=``, ``mailto=`` …) are masked wherever they appear in
text, and the actual credential values loaded from the environment are masked in every encoding they could take
in a URL. ``SourceError`` messages and the package's log records pass through ``redact``.
"""

import logging
import re
import threading
from collections.abc import Iterable
from urllib.parse import quote, quote_plus

MASK = '[redacted]'
SECRET_PARAMETERS = ('api_key', 'apikey', 'api-key', 'key', 'token', 'access_token', 'mailto', 'email', 'password')
_PARAMETER = re.compile(
    r'(?P<name>(?<![\w-])(?:'
    + '|'.join(re.escape(name) for name in SECRET_PARAMETERS)
    + r'))=(?P<value>[^&\s#"\'<>]+)',
    re.IGNORECASE,
)
# Shorter values would mask ordinary words; real keys and email addresses are longer.
MINIMUM_SECRET_LENGTH = 6

_secrets: set[str] = set()
_lock = threading.Lock()


def register_secrets(values: Iterable[str | None]) -> None:
    """Mask these exact values (and their URL encodings) from now on."""
    with _lock:
        for value in values:
            if value and len(value) >= MINIMUM_SECRET_LENGTH:
                _secrets.update({value, quote(value, safe=''), quote_plus(value)})


def redact(text: str) -> str:
    if not text:
        return text
    text = _PARAMETER.sub(lambda match: f'{match.group("name")}={MASK}', text)
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, MASK)
    return text


class RedactingFilter(logging.Filter):
    """Redacts the rendered message (and any traceback) of every record passing the handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True
