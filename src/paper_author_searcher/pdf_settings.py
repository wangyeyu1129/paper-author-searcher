"""Read contact configuration without exposing contact values in diagnostics."""

import os
import re
from urllib.parse import quote, quote_plus


def contact_email(explicit, environment_name):
    value = explicit if explicit is not None else os.getenv(environment_name)
    if value is None or not value.strip():
        return None
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', value):
        raise ValueError('Contact email must be a plain, valid email address')
    return value


def redact_contact(message, email):
    if not email:
        return message
    for value in (email, quote(email, safe=''), quote_plus(email)):
        message = message.replace(value, '[redacted contact]')
    return message
