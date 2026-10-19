import io
import logging
import unittest
from urllib.parse import quote

from paper_author_searcher.http import SourceError
from paper_author_searcher.logging_config import PACKAGE_LOGGER, configure_logging
from paper_author_searcher.redaction import MASK, redact, register_secrets
from paper_author_searcher.settings import Settings

KEY = 'oa-secret-key-12345'
EMAIL = 'owner+lab@example.org'


class RedactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Settings.load(environ={'OPENALEX_API_KEY': KEY, 'UNPAYWALL_EMAIL': EMAIL})  # registers the credentials

    def test_secret_query_parameters_are_masked(self):
        text = 'GET https://api.openalex.org/works?filter=x&api_key=abc123&mailto=me%40x.org failed'
        self.assertEqual(
            redact(text), f'GET https://api.openalex.org/works?filter=x&api_key={MASK}&mailto={MASK} failed'
        )

    def test_ordinary_text_is_untouched(self):
        text = 'monkey=see; keyboard layout; HTTP 429 from api.openalex.org'
        self.assertEqual(redact(text), text)

    def test_loaded_credentials_are_masked_in_every_encoding(self):
        for form in (KEY, EMAIL, quote(EMAIL, safe='')):
            self.assertNotIn(form, redact(f'value seen: {form} (path /a/{form})'))

    def test_short_values_are_not_registered(self):
        register_secrets(['abc'])
        self.assertEqual(redact('abc is a word'), 'abc is a word')

    def test_source_errors_never_carry_secrets(self):
        error = SourceError(f'https://api.openalex.org/authors?api_key={KEY}: HTTP 401')
        self.assertNotIn(KEY, str(error))

    def test_log_records_and_tracebacks_are_redacted(self):
        stream = io.StringIO()
        package = logging.getLogger(PACKAGE_LOGGER)
        saved = (list(package.handlers), package.level, package.propagate)
        self.addCleanup(lambda: setattr(package, 'handlers', saved[0]) or package.setLevel(saved[1]))
        configure_logging('DEBUG', 'text', stream)
        logger = logging.getLogger(PACKAGE_LOGGER + '.test')
        logger.warning('request to %s failed', f'https://x.org/?api_key={KEY}')
        try:
            raise ValueError(f'contact {EMAIL} rejected')
        except ValueError:
            logger.exception('lookup failed')
        output = stream.getvalue()
        self.assertNotIn(KEY, output)
        self.assertNotIn(EMAIL, output)
        self.assertIn(MASK, output)


if __name__ == '__main__':
    unittest.main()
