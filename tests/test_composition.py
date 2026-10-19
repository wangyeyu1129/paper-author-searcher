import ast
import importlib.util
import unittest
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.http_budget import BudgetedHttpClient
from paper_author_searcher.http_cache import CachingHttpClient
from paper_author_searcher.settings import Settings

PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'paper_author_searcher'
CLI_MODULES = {'cli', 'workflow_cli', 'pdf_cli', 'download_cli', 'evaluation_cli', 'performance_cli'}


class RunOptionsTests(unittest.TestCase):
    def test_from_namespace_reads_cli_attribute_names(self):
        options = RunOptions.from_namespace(
            Namespace(mode=None, timeout=5.0, bibliography_url=['https://a.example/'], unrelated=1)
        )
        self.assertEqual(options.mode, 'full')
        self.assertEqual(options.timeout, 5.0)
        self.assertEqual(options.bibliography_urls, ('https://a.example/',))
        self.assertIsNone(options.ambiguity_policy)


class ServiceContainerTests(unittest.TestCase):
    def container(self, settings=None, **options):
        return ServiceContainer(settings or Settings(), RunOptions(**options))

    def test_one_transport_chain_is_shared_by_every_service(self):
        container = self.container()
        client = container.http_client
        self.assertIs(container.http_client, client)
        self.assertTrue(all(provider.client is client for provider in container.identity_providers))
        self.assertIs(container.anchor_finder.client, client)
        self.assertIs(container.paper_providers[0].provider, container.identity_providers[0])

    def test_transport_layers_follow_the_options(self):
        plain = self.container().http_client
        self.assertNotIsInstance(plain, (CachingHttpClient, BudgetedHttpClient))
        layered = self.container(max_cost_usd=0.1, http_cache_dir='/tmp/cache', http_cache_mode='replay').http_client
        self.assertIsInstance(layered, CachingHttpClient)
        self.assertIsInstance(layered.client, BudgetedHttpClient)

    def test_options_override_settings_only_when_given(self):
        settings = Settings.from_mapping({'collection': {'maximum_contenders': 4}, 'download': {'chunk_bytes': 2048}})
        defaults = self.container(settings)
        self.assertEqual(defaults.collection_config, settings.collection)
        self.assertEqual(defaults.download_config, settings.download)
        tuned = self.container(settings, max_pages=2, page_size=50, ambiguity_policy='stop', max_file_mb=1)
        self.assertEqual(tuned.collection_config.maximum_pages, 2)
        self.assertEqual(tuned.collection_config.semantic_scholar_page_size, 50)
        self.assertEqual(tuned.collection_config.maximum_contenders, 4)
        self.assertEqual(tuned.download_config.maximum_file_bytes, 1024 * 1024)
        self.assertEqual(tuned.download_config.chunk_bytes, 2048)

    def test_bibliography_urls_extend_configured_ones(self):
        settings = Settings.from_mapping({'pdf': {'bibliography_urls': ['https://a.example/']}})
        container = self.container(settings, bibliography_urls=('https://a.example/', 'https://b.example/'))
        self.assertEqual(container.pdf_config.bibliography_urls, ('https://a.example/', 'https://b.example/'))

    def test_metadata_mode_never_builds_a_downloader(self):
        self.assertIsNone(self.container(mode='metadata').workflow().downloader)

    @unittest.skipUnless(importlib.util.find_spec('pypdf'), 'pypdf (download extra) is required')
    def test_full_mode_builds_the_downloader(self):
        container = self.container(mode='full')
        self.assertIs(container.workflow().downloader, container.downloader)

    def test_adjudicator_is_built_only_when_requested(self):
        factory = Mock(return_value='judge')
        self.assertIsNone(ServiceContainer(Settings(), RunOptions(), factory).resolver.adjudicator)
        factory.assert_not_called()
        enabled = ServiceContainer(Settings(), RunOptions(llm_adjudication=True), factory)
        self.assertEqual(enabled.resolver.adjudicator, 'judge')

    def test_variants_get_fresh_services(self):
        base = self.container()
        variant = base.with_settings(replace(base.settings, matching=replace(base.settings.matching, review_cap=0.5)))
        self.assertIsNot(variant.resolver, base.resolver)
        self.assertEqual(variant.resolver.config.review_cap, 0.5)
        self.assertEqual(variant.options, base.options)


class LayeringTests(unittest.TestCase):
    def test_library_modules_never_import_entry_points(self):
        offenders = []
        for path in PACKAGE.rglob('*.py'):
            module = path.stem
            if module in CLI_MODULES or (path.parent.name == 'benchmark' and module == 'cli'):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported = node.module.rsplit('.', 1)[-1]
                    if imported in CLI_MODULES:
                        offenders.append(f'{path.relative_to(PACKAGE)}:{node.lineno} imports {node.module}')
        self.assertEqual(offenders, [], 'build services through paper_author_searcher.composition')


if __name__ == '__main__':
    unittest.main()
