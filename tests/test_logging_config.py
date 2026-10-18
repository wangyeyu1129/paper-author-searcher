import argparse
import io
import json
import logging
import os
import unittest
from unittest.mock import patch

from paper_author_searcher.logging_config import (
    LEVEL_ENVIRONMENT_VARIABLE,
    PACKAGE_LOGGER,
    add_logging_arguments,
    configure_from_arguments,
    configure_logging,
    log_isolated_failure,
)


class LoggingConfigTests(unittest.TestCase):
    def setUp(self):
        self.package = logging.getLogger(PACKAGE_LOGGER)
        self.saved = (list(self.package.handlers), self.package.level, self.package.propagate)
        self.stream = io.StringIO()

    def tearDown(self):
        self.package.handlers[:], self.package.level, self.package.propagate = self.saved

    def test_text_format_respects_level(self):
        configure_logging('WARNING', 'text', self.stream)
        logger = logging.getLogger(PACKAGE_LOGGER + '.module')
        logger.info('hidden')
        logger.warning('shown %s', 1)
        output = self.stream.getvalue()
        self.assertNotIn('hidden', output)
        self.assertIn('WARNING', output)
        self.assertIn('paper_author_searcher.module: shown 1', output)

    def test_json_format_carries_extra_fields(self):
        configure_logging('INFO', 'json', self.stream)
        logging.getLogger(PACKAGE_LOGGER).info('done', extra={'person_id': 'p1'})
        entry = json.loads(self.stream.getvalue())
        self.assertEqual((entry['level'], entry['message'], entry['person_id']), ('INFO', 'done', 'p1'))

    def test_reconfiguring_replaces_the_handler(self):
        configure_logging('INFO', 'text', io.StringIO())
        configure_logging('INFO', 'text', self.stream)
        active = [item for item in self.package.handlers if not isinstance(item, logging.NullHandler)]
        self.assertEqual(len(active), 1)

    def test_environment_supplies_default_level(self):
        with patch.dict(os.environ, {LEVEL_ENVIRONMENT_VARIABLE: 'error'}):
            configure_logging(stream=self.stream)
        self.assertEqual(self.package.level, logging.ERROR)

    def test_invalid_level_is_rejected(self):
        with self.assertRaises(ValueError):
            configure_logging('LOUD', stream=self.stream)

    def test_arguments_round_trip(self):
        parser = argparse.ArgumentParser()
        add_logging_arguments(parser)
        arguments = parser.parse_args(['--log-level', 'debug', '--log-format', 'json'])
        with patch('sys.stderr', new=self.stream):
            configure_from_arguments(arguments)
        self.assertEqual(self.package.level, logging.DEBUG)

    def test_isolated_failure_attaches_traceback_only_at_debug(self):
        logger = logging.getLogger(PACKAGE_LOGGER + '.isolated')
        for level, expect_traceback in (('INFO', False), ('DEBUG', True)):
            stream = io.StringIO()
            configure_logging(level, 'text', stream)
            try:
                raise RuntimeError('boom')
            except RuntimeError as exc:
                log_isolated_failure(logger, 'source failed: %s', exc)
            self.assertIn('source failed: boom', stream.getvalue())
            self.assertEqual('Traceback' in stream.getvalue(), expect_traceback)


if __name__ == '__main__':
    unittest.main()
