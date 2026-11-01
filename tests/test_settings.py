import ast
import json
import tempfile
import unittest
from pathlib import Path

from paper_author_searcher.config import DEFAULT_HTTP, DEFAULT_PDF
from paper_author_searcher.settings import (
    SETTINGS_FILE_VARIABLE,
    Credentials,
    Settings,
    validate_contact_email,
)

PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'paper_author_searcher'
# The only modules allowed to read process configuration from the environment.
ENVIRONMENT_READERS = {'settings.py', 'logging_config.py'}


class SettingsLoadingTests(unittest.TestCase):
    def write(self, directory, data):
        path = Path(directory) / 'settings.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_defaults_without_file_or_environment(self):
        settings = Settings.load(environ={})
        self.assertEqual(settings.http, DEFAULT_HTTP)
        self.assertEqual(settings.credentials, Credentials())

    def test_file_then_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'http': {'timeout': 30, 'retries': 4}})
            environ = {SETTINGS_FILE_VARIABLE: str(path), 'PAPER_SEARCH__HTTP__TIMEOUT': '45'}
            settings = Settings.load(environ=environ)
        self.assertEqual(settings.http.timeout, 45.0)
        self.assertEqual(settings.http.retries, 4)

    def test_explicit_path_wins_over_environment_path(self):
        with tempfile.TemporaryDirectory() as directory:
            chosen = self.write(directory, {'http': {'retries': 7}})
            settings = Settings.load(chosen, environ={SETTINGS_FILE_VARIABLE: str(Path(directory) / 'missing.json')})
        self.assertEqual(settings.http.retries, 7)

    def test_environment_values_are_coerced_to_field_types(self):
        settings = Settings.load(
            environ={
                'PAPER_SEARCH__COLLECTION__MAXIMUM_PAGES': '3',
                'PAPER_SEARCH__MATCHING__MERGE_SPLIT_PROFILES': 'false',
                'PAPER_SEARCH__HTTP__RETRY_STATUSES': '429, 503',
                'PAPER_SEARCH__PDF__JOURNAL_CATALOGS': '[["Journal", "https://journal.example/"]]',
            }
        )
        self.assertEqual(settings.collection.maximum_pages, 3)
        self.assertIs(settings.matching.merge_split_profiles, False)
        self.assertEqual(settings.http.retry_statuses, (429, 503))
        self.assertEqual(settings.pdf.journal_catalogs, (('Journal', 'https://journal.example/'),))

    def test_optional_values_accept_none(self):
        settings = Settings.from_mapping({'collection': {'maximum_pages': 2}})
        self.assertIsNone(settings.with_overrides({'collection': {'maximum_pages': 'none'}}).collection.maximum_pages)

    def test_json_lists_become_tuples(self):
        settings = Settings.from_mapping({'pdf': {'bibliography_urls': ['https://a.example/']}})
        self.assertEqual(settings.pdf.bibliography_urls, ('https://a.example/',))
        self.assertEqual(settings.pdf.unpaywall_url, DEFAULT_PDF.unpaywall_url)

    def test_section_validation_still_runs(self):
        with self.assertRaisesRegex(ValueError, 'collection'):
            Settings.from_mapping({'collection': {'openalex_page_size': 500}})

    def test_type_errors_are_reported_with_section(self):
        for data in (
            {'http': {'retries': 'many'}},
            {'http': {'retries': 1.5}},
            {'matching': {'merge_split_profiles': 2}},
        ):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'Invalid settings'):
                Settings.from_mapping(data)

    def test_unknown_sections_and_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown settings sections'):
            Settings.from_mapping({'htp': {}})
        with self.assertRaisesRegex(ValueError, "Unknown settings in 'http'"):
            Settings.from_mapping({'http': {'timeout_seconds': 3}})
        with self.assertRaisesRegex(ValueError, 'PAPER_SEARCH__'):
            Settings.load(environ={'PAPER_SEARCH__TIMEOUT': '3'})

    def test_credentials_are_never_read_from_files(self):
        with self.assertRaisesRegex(ValueError, 'environment only'):
            Settings.from_mapping({'credentials': {'openalex_api_key': 'secret-value'}})


class CredentialTests(unittest.TestCase):
    def test_credentials_come_from_conventional_variables(self):
        credentials = Credentials.from_environment({'OPENALEX_API_KEY': ' key-1 ', 'UNPAYWALL_EMAIL': ''})
        self.assertEqual(credentials.openalex_api_key, 'key-1')
        self.assertIsNone(credentials.unpaywall_email)

    def test_a_file_in_the_secrets_directory_wins_over_the_variable(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'OPENALEX_API_KEY').write_text(' from-file\n')
            credentials = Credentials.from_environment(
                {
                    'PAPER_SEARCH_SECRETS_DIRECTORY': directory,
                    'OPENALEX_API_KEY': 'from-variable',
                    'TAVILY_API_KEY': 'only-variable',
                }
            )
        self.assertEqual(credentials.openalex_api_key, 'from-file')
        self.assertEqual(credentials.tavily_api_key, 'only-variable')

    def test_secrets_never_render(self):
        settings = Settings.load(environ={'OPENALEX_API_KEY': 'secret-value', 'CROSSREF_MAILTO': 'me@example.org'})
        self.assertNotIn('secret-value', repr(settings))
        rendered = json.dumps(settings.to_dict(), default=str)
        self.assertNotIn('secret-value', rendered)
        self.assertNotIn('me@example.org', rendered)
        self.assertEqual(settings.to_dict()['credentials']['openalex_api_key'], 'set')
        self.assertEqual(settings.to_dict()['credentials']['semantic_scholar_api_key'], 'unset')

    def test_contact_email_validation(self):
        self.assertEqual(validate_contact_email(' me@example.org '), 'me@example.org')
        self.assertIsNone(validate_contact_email(None))
        with self.assertRaises(ValueError):
            validate_contact_email('Me <me@example.org>')


class ArchitectureTests(unittest.TestCase):
    def test_only_settings_modules_read_the_environment(self):
        offenders = []
        for path in PACKAGE.rglob('*.py'):
            if path.name in ENVIRONMENT_READERS:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                is_getenv = isinstance(node, ast.Attribute) and node.attr in ('getenv', 'environ')
                if is_getenv and isinstance(node.value, ast.Name) and node.value.id == 'os':
                    offenders.append(f'{path.relative_to(PACKAGE)}:{node.lineno}')
        self.assertEqual(offenders, [], 'read configuration through paper_author_searcher.settings')


if __name__ == '__main__':
    unittest.main()


class CompositionTests(unittest.TestCase):
    def test_workflow_wires_credentials_into_every_consumer(self):
        from argparse import Namespace

        from paper_author_searcher.workflow_cli import build_workflow

        arguments = Namespace(
            timeout=None, retries=None, max_pages=None, page_size=None, ambiguity_policy=None, mode='pdf-urls',
            max_file_mb=None, llm_adjudication=False, unpaywall_email=None, bibliography_url=[], verbose=False,
        )  # fmt: skip
        settings = Settings(
            credentials=Credentials('openalex-key', 'semantic-key', 'unpaywall@example.org', 'crossref@example.org')
        )
        workflow = build_workflow(arguments, settings)
        openalex, semantic_scholar, _dblp = workflow.resolver.providers
        self.assertEqual((openalex.api_key, semantic_scholar.api_key), ('openalex-key', 'semantic-key'))
        anchors = workflow.resolver.anchor_finder
        self.assertEqual((anchors.openalex_key, anchors.semantic_scholar_key), ('openalex-key', 'semantic-key'))
        pdf = {provider.name: provider for provider in workflow.pdf_resolver.providers}
        self.assertEqual(pdf['openalex'].api_key, 'openalex-key')
        self.assertEqual(pdf['semantic_scholar'].api_key, 'semantic-key')
        self.assertEqual(pdf['unpaywall'].email, 'unpaywall@example.org')
        self.assertEqual(pdf['crossref'].email, 'crossref@example.org')


class InterningTests(unittest.TestCase):
    def test_interned_load_equals_plain_load_and_shares_strings(self):
        from paper_author_searcher.jsonio import read_json

        document = {'papers': [{'source': 'openalex', 'authors': ['Mei Lin', 'Alex Example']} for _ in range(3)]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.json'
            path.write_text(json.dumps(document), encoding='utf-8')
            plain, interned = read_json(path), read_json(path, intern_strings=True)
        self.assertEqual(plain, interned)
        first, second = interned['papers'][0], interned['papers'][1]
        self.assertIs(first['authors'][0], second['authors'][0])
        self.assertIs(first['source'], second['source'])
