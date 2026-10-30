import unittest

from paper_author_searcher.composition import ServiceContainer
from paper_author_searcher.registry import Registry, UnknownComponent
from paper_author_searcher.settings import Settings
from paper_author_searcher.source_catalog import IDENTITY_SOURCES, PAPER_SOURCES, PDF_SOURCES


class RegistryTests(unittest.TestCase):
    def test_register_create_and_order(self):
        registry: Registry[str] = Registry('widget')
        registry.register('a')(lambda prefix: prefix + 'a')
        registry.register('b')(lambda prefix: prefix + 'b')
        self.assertEqual(registry.create(['b', 'a'], '>'), ['>b', '>a'])
        self.assertEqual(registry.names(), ('a', 'b'))

    def test_duplicates_and_unknown_names_are_rejected(self):
        registry: Registry[str] = Registry('widget')
        registry.register('a')(lambda: 'a')
        with self.assertRaisesRegex(ValueError, 'already registered'):
            registry.register('a')(lambda: 'again')
        with self.assertRaisesRegex(UnknownComponent, "Unknown widget 'z'; available: a"):
            registry.create(['a', 'z'])
        with self.assertRaisesRegex(ValueError, 'more than once'):
            registry.create(['a', 'a'])


class CatalogTests(unittest.TestCase):
    def test_defaults_enable_every_built_in_source_in_the_historic_order(self):
        container = ServiceContainer(Settings())
        self.assertEqual([p.name for p in container.identity_providers], ['openalex', 'semantic_scholar', 'dblp'])
        self.assertEqual([p.name for p in container.paper_providers], ['openalex', 'semantic_scholar', 'dblp'])
        self.assertEqual(
            [p.name for p in container.pdf_providers],
            ['existing_metadata', 'openalex', 'semantic_scholar', 'crossref', 'unpaywall', 'europepmc', 'openaire',
             'nber', 'arxiv', 'journal_catalog', 'public_pages', 'homepage'],
        )  # fmt: skip
        self.assertEqual(set(PDF_SOURCES.names()), {p.name for p in container.pdf_providers})
        self.assertEqual(set(IDENTITY_SOURCES.names()), set(PAPER_SOURCES.names()))

    def test_configuration_selects_and_orders_sources(self):
        settings = Settings.from_mapping(
            {
                'sources': {'identity_sources': ['semantic_scholar', 'openalex']},
                'pdf': {'sources': ['arxiv', 'existing_metadata']},
            }
        )
        container = ServiceContainer(settings)
        self.assertEqual([p.name for p in container.identity_providers], ['semantic_scholar', 'openalex'])
        self.assertEqual([p.name for p in container.collector.providers], ['semantic_scholar', 'openalex'])
        self.assertEqual([p.name for p in container.pdf_resolver.providers], ['arxiv', 'existing_metadata'])

    def test_unknown_configured_source_fails_fast(self):
        settings = Settings.from_mapping({'pdf': {'sources': ['existing_metadata', 'scihub']}})
        with self.assertRaisesRegex(UnknownComponent, "Unknown PDF source 'scihub'"):
            _ = ServiceContainer(settings).pdf_resolver


if __name__ == '__main__':
    unittest.main()
