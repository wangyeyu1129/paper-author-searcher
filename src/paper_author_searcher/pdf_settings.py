"""Keep contact values out of diagnostics; contacts themselves are loaded by ``settings.Credentials``."""

from urllib.parse import quote, quote_plus


def redact_contact(message, email):
    if not email:
        return message
    for value in (email, quote(email, safe=''), quote_plus(email)):
        message = message.replace(value, '[redacted contact]')
    return message
