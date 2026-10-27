"""Venue catalogs (ACL Anthology, PMLR, CVF), OAI-PMH repositories and the catalog store, offline."""

import gzip
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from paper_author_searcher.catalog_cli import main as catalog_main
from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import DEFAULT_CATALOGS
from paper_author_searcher.http import URLProbe
from paper_author_searcher.listed_works import AuthorMatcher
from paper_author_searcher.matching import work_keys
from paper_author_searcher.models import ProfessorProfile
from paper_author_searcher.settings import Settings
from paper_author_searcher.venue_catalogs import (
    AclAnthologyCatalog,
    CatalogEntry,
    CatalogIndexer,
    CatalogStore,
    CatalogWorkLookup,
    CvfCatalog,
    OaiPmhRepository,
    PmlrCatalog,
    RepositoryNameSearch,
    author_key,
    bibtex_authors,
    bibtex_entries,
    catalog_sources,
    latex_text,
    probe_fetch,
)

BIB = r"""
@proceedings{2024.acl-long, title = "Proceedings of ACL", editor = "Someone, A."}
@inproceedings{lin-2024-index,
    title = "Learned {I}ndex Structures for {M}{\"u}ller Graphs",
    author = "Lin, Mei  and
      M{\"u}ller, J{\"u}rgen and Ana Other",
    year = 2024,
    booktitle = {Proceedings of the 62nd Annual Meeting {(ACL)}},
    url = "https://aclanthology.org/2024.acl-long.1/",
    doi = "10.18653/v1/2024.acl-long.1",
}
"""

PMLR = """@InProceedings{pmlr-v235-lin24a,
  title = 	 {Graph Learning at Scale},
  author =       {Lin, Mei and Other, Ana},
  booktitle = 	 {Proceedings of the 41st ICML},
  year = 	 {2024},
  pdf = 	 {https://raw.githubusercontent.com/mlresearch/v235/main/assets/lin24a/lin24a.pdf},
  url = 	 {https://proceedings.mlr.press/v235/lin24a.html}
}
"""

CVF = """<dl>
<dt class="ptitle"><br><a href="/content/CVPR2024/html/Lin_Seeing_CVPR_2024_paper.html">Seeing Graphs Clearly</a></dt>
<dd><form><input type="hidden" name="query_author" value="Mei Lin"><a href="#">Mei Lin</a>,</form>
<form><input type="hidden" name="query_author" value="Ana Other"><a href="#">Ana Other</a></form></dd>
<dd>[<a href="/content/CVPR2024/papers/Lin_Seeing_CVPR_2024_paper.pdf">pdf</a>]</dd>
<dt class="ptitle"><a href="/x.html">No Authors</a></dt><dd></dd>
</dl>"""

OAI_NS = 'xmlns="http://www.openarchives.org/OAI/2.0/"'
DC_NS = 'xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/"'


def oai_page(records, token=''):
    body = ''.join(records)
    resumption = f'<resumptionToken>{token}</resumptionToken>' if token else '<resumptionToken/>'
    return ET.fromstring(f'<OAI-PMH {OAI_NS}><ListRecords>{body}{resumption}</ListRecords></OAI-PMH>')


def oai_record(identifier, title, *creators, deleted=False, doi=None):
    if deleted:
        return f'<record><header status="deleted"><identifier>{identifier}</identifier></header></record>'
    names = ''.join(f'<dc:creator>{name}</dc:creator>' for name in creators)
    doi_identifier = f'<dc:identifier>https://doi.org/{doi}</dc:identifier>' if doi else ''
    return (
        f'<record><header><identifier>{identifier}</identifier></header><metadata><oai_dc:dc {DC_NS}>'
        f'<dc:title>{title}</dc:title>{names}<dc:date>2023-05-01</dc:date>'
        f'<dc:identifier>https://repo.example.edu/{identifier}</dc:identifier>{doi_identifier}'
        f'</oai_dc:dc></metadata></record>'
    )


class Client:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.requests = []

    def get(self, url, params=None, headers=None, xml=False):
        self.requests.append((url, dict(params or {})))
        return self.pages.pop(0)


def profile(name='Mei Lin', university='Example University'):
    return ProfessorProfile(
        name=name,
        affiliations=[university],
        author_ids={},
        identity_confidence=1.0,
        status='resolved',
        evidence=[],
        requested_name=name,
        requested_university=university,
    )


class BibtexTests(unittest.TestCase):
    def test_values_become_plain_text(self):
        self.assertEqual(latex_text(r'M{\"u}ller \& {S}ons \textit{x}'), 'Müller & Sons x')
        self.assertEqual(latex_text(r'Fran\c{c}ois Ren\'e'), 'François René')

    def test_entries_fields_and_authors_are_parsed(self):
        entries = list(bibtex_entries(BIB))
        self.assertEqual(
            [(kind, key) for kind, key, _ in entries],
            [('proceedings', '2024.acl-long'), ('inproceedings', 'lin-2024-index')],
        )
        fields = entries[1][2]
        self.assertEqual(fields['title'], 'Learned Index Structures for Müller Graphs')
        self.assertEqual(fields['year'], '2024')
        self.assertEqual(fields['booktitle'], 'Proceedings of the 62nd Annual Meeting (ACL)')
        self.assertEqual(bibtex_authors(fields['author']), ('Mei Lin', 'Jürgen Müller', 'Ana Other'))


class SourceTests(unittest.TestCase):
    def test_acl_anthology_reads_the_gzipped_export_and_skips_proceedings(self):
        entries = list(AclAnthologyCatalog(lambda url: gzip.compress(BIB.encode()), 'https://acl/bib.gz').entries())
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual((entry.catalog, entry.year, entry.doi), ('acl_anthology', 2024, '10.18653/v1/2024.acl-long.1'))
        self.assertEqual(entry.pdf_url, 'https://aclanthology.org/2024.acl-long.1.pdf')

    def test_pmlr_reads_each_configured_volume(self):
        fetched = []
        catalog = PmlrCatalog(lambda url: fetched.append(url) or PMLR.encode(), 'https://p/v{volume}.bib', [1, 2])
        entries = list(catalog.entries())
        self.assertEqual(fetched, ['https://p/v1.bib', 'https://p/v2.bib'])
        self.assertEqual(entries[0].authors, ('Mei Lin', 'Ana Other'))
        self.assertTrue(entries[0].pdf_url.endswith('lin24a.pdf'))

    def test_cvf_listing_gives_titles_authors_and_absolute_links(self):
        entries = list(CvfCatalog(lambda url: CVF.encode(), 'https://openaccess.thecvf.com/', ['CVPR2024']).entries())
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(
            (entry.title, entry.authors, entry.year), ('Seeing Graphs Clearly', ('Mei Lin', 'Ana Other'), 2024)
        )
        self.assertEqual(
            entry.pdf_url, 'https://openaccess.thecvf.com/content/CVPR2024/papers/Lin_Seeing_CVPR_2024_paper.pdf'
        )

    def test_oai_pmh_follows_resumption_tokens_and_skips_deleted_records(self):
        client = Client(
            oai_page([oai_record('a', 'Repository Paper One', 'Lin, Mei'), oai_record('b', '', deleted=True)], 'next'),
            oai_page([oai_record('c', 'Repository Paper Two', 'Mei Lin', doi='10.5555/abc')]),
        )
        repository = OaiPmhRepository(client, 'Example University', 'https://repo/oai', 'dept', '2021-01-01', 5)
        entries = list(repository.entries())
        self.assertEqual([entry.key for entry in entries], ['a', 'c'])
        self.assertEqual(entries[0].authors, ('Mei Lin',))
        self.assertEqual((entries[1].doi, entries[1].institution), ('10.5555/abc', 'Example University'))
        self.assertEqual(
            client.requests[0][1],
            {'verb': 'ListRecords', 'metadataPrefix': 'oai_dc', 'set': 'dept', 'from': '2021-01-01'},
        )
        self.assertEqual(client.requests[1][1], {'verb': 'ListRecords', 'resumptionToken': 'next'})
        self.assertEqual(repository.name, 'oai_pmh:https://repo/oai#dept')

    def test_oai_pmh_errors(self):
        empty = ET.fromstring(f'<OAI-PMH {OAI_NS}><error code="noRecordsMatch">none</error></OAI-PMH>')
        self.assertEqual(list(OaiPmhRepository(Client(empty), 'U', 'https://r').entries()), [])
        bad = ET.fromstring(f'<OAI-PMH {OAI_NS}><error code="badArgument">no</error></OAI-PMH>')
        with self.assertRaises(ValueError):
            list(OaiPmhRepository(Client(bad), 'U', 'https://r').entries())

    def test_sources_follow_configuration(self):
        config = replace(
            DEFAULT_CATALOGS, oai_repositories=(('Example University', 'https://r', 's'), ('Other', 'https://o'))
        )
        names = [source.name for source in catalog_sources(config, lambda url: b'', None)]
        self.assertEqual(names, ['acl_anthology', 'pmlr', 'cvf', 'oai_pmh:https://r#s', 'oai_pmh:https://o'])
        with self.assertRaises(ValueError):
            catalog_sources(replace(DEFAULT_CATALOGS, sources=('nope',)), lambda url: b'', None)

    def test_probe_fetch_refuses_a_truncated_body(self):
        class Prober:
            def __init__(self, complete):
                self.complete = complete

            def probe(self, url, maximum_bytes, html_bytes):
                return URLProbe(url, 200, 'application/x-gzip', {}, b'data', self.complete)

        self.assertEqual(probe_fetch(Prober(True), 10)('https://x'), b'data')
        with self.assertRaises(ValueError):
            probe_fetch(Prober(False), 10)('https://x')


def entry(key, title='Seeing Graphs Clearly', authors=('Mei Lin', 'Ana Other'), institution=None, catalog='cvf'):
    return CatalogEntry(catalog=catalog, key=key, title=title, authors=authors, year=2024, institution=institution)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = CatalogStore(Path(self.directory.name) / 'catalogs.sqlite')

    def tearDown(self):
        self.directory.cleanup()

    def test_a_catalog_is_replaced_as_a_whole_and_queried_by_work_and_author(self):
        self.store.replace('cvf', [entry('1'), entry('2', title='Another Paper Entirely')])
        self.store.replace('cvf', [entry('1')])
        self.store.replace('pmlr', [entry('9', catalog='pmlr')])
        self.assertEqual(self.store.counts(), {'cvf': 1, 'pmlr': 1})
        key = next(iter(work_keys(titles=['Seeing Graphs Clearly'])))
        self.assertEqual([found.key for found in self.store.works(key)], ['1', '9'])
        self.assertEqual(len(self.store.by_author('Lin, Mei')), 2)
        self.assertEqual(self.store.by_author('Mei Lin', 'pm')[0].catalog, 'pmlr')
        self.assertEqual(author_key('Lin, Mei'), author_key('mei lin'))

    def test_a_failing_source_keeps_its_previous_entries(self):
        self.store.replace('cvf', [entry('1')])

        class Broken:
            name = 'cvf'

            def entries(self):
                raise ValueError('HTTP 503')
                yield

        report = CatalogIndexer(self.store, [Broken()]).build()
        self.assertEqual(report.errors, {'cvf': 'HTTP 503'})
        self.assertEqual(self.store.counts(), {'cvf': 1})

    def test_lookup_and_repository_search_name_the_person(self):
        self.store.replace('cvf', [entry('1')])
        self.store.replace(
            'oai_pmh:https://r',
            [
                entry(
                    'r1', title='Repository Paper One', institution='Example University', catalog='oai_pmh:https://r'
                ),
                entry('r2', title='Repository Paper Two', institution='Other College', catalog='oai_pmh:https://r'),
            ],
        )
        key = next(iter(work_keys(titles=['Seeing Graphs Clearly'])))
        record = CatalogWorkLookup(self.store, AuthorMatcher()).find(key, profile())
        self.assertEqual((record.source, record.source_author_id), ('cvf', 'cvf:1#0'))
        self.assertIsNone(CatalogWorkLookup(self.store, AuthorMatcher()).find(key, profile('Zed Nobody')))
        found = RepositoryNameSearch(self.store).collect(profile())
        self.assertEqual([paper.source_paper_id for paper in found.papers], ['r1'])
        self.assertEqual((found.papers[0].source, found.papers[0].source_author_id), ('repository', 'name:mei lin'))
        self.assertTrue(found.complete)


class CompositionTests(unittest.TestCase):
    def test_the_store_enables_its_lookup_and_repository_search(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalogs.sqlite'
            self.assertIsNone(ServiceContainer(Settings(), RunOptions(mode='metadata')).catalog_store)
            missing = Settings.from_mapping({'catalogs': {'store_path': str(path)}})
            with self.assertRaises(FileNotFoundError):
                _ = ServiceContainer(missing, RunOptions(mode='metadata')).catalog_store
            CatalogStore(path)
            repositories = {'store_path': str(path), 'oai_repositories': [['Example University', 'https://r']]}
            container = ServiceContainer(Settings.from_mapping({'catalogs': repositories}), RunOptions(mode='metadata'))
            self.assertIn('catalogs', [lookup.name for lookup in container.listed_work_retriever.lookups])
            self.assertIn('repository', [search.name for search in container.name_searches])
            self.assertEqual(len(container.catalog_indexer().sources), 4)

    def test_the_cli_reports_what_it_stored(self):
        with tempfile.TemporaryDirectory() as directory:
            store = str(Path(directory) / 'catalogs.sqlite')
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(catalog_main(['--store', store, '--source', 'oai_pmh']), 0)
            self.assertEqual(json.loads(output.getvalue()), {'store': store, 'stored': {}, 'errors': {}})
            with self.assertRaises(ValueError):
                ServiceContainer(Settings(), RunOptions(mode='metadata')).catalog_indexer()


if __name__ == '__main__':
    unittest.main()
