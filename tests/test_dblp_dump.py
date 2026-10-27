"""DBLP from a synthetic dump: indexing, the API-shaped client the unchanged providers run on, and work lookups."""

import gzip
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from paper_author_searcher import ProfessorQuery as Q
from paper_author_searcher import dblp_dump
from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import DEFAULT_DBLP_DUMP
from paper_author_searcher.dblp_cli import main as dblp_main
from paper_author_searcher.dblp_dump import (
    DblpDumpClient,
    DblpDumpIndex,
    DblpDumpIndexer,
    DblpWorkLookup,
    base_name,
)
from paper_author_searcher.http import SourceError
from paper_author_searcher.listed_works import AuthorMatcher
from paper_author_searcher.matching import work_keys
from paper_author_searcher.models import ProfessorProfile
from paper_author_searcher.paper_providers import DBLPPaperProvider
from paper_author_searcher.providers import DBLPProvider
from paper_author_searcher.settings import Settings

DUMP = """<?xml version="1.0" encoding="ISO-8859-1"?>
<!DOCTYPE dblp SYSTEM "dblp.dtd">
<dblp>
<www mdate="2024-01-01" key="homepages/11/1">
<author>Mei Lin 0001</author><author>M. Lin 0001</author><title>Home Page</title>
<note type="affiliation">Example University, Department of Computer Science</note>
<url>https://orcid.org/0000-0002-1825-0097</url>
</www>
<www mdate="2024-01-01" key="homepages/22/2">
<author>Mei Lin 0002</author><title>Home Page</title>
<note type="affiliation">Other Institute</note>
</www>
<www mdate="2024-01-01" key="homepages/33/3"><author>J&uuml;rgen M&uuml;ller</author><title>Home Page</title></www>
<www mdate="2024-01-01" key="homepages/l/MeiLin" publtype="disambiguation">
<author>Mei Lin</author><title>Home Page</title></www>
<www key="www/org/example"><title>Some Site</title></www>
<article mdate="2024-01-01" key="journals/x/Lin24">
<author>Mei Lin 0001</author><author>J&uuml;rgen M&uuml;ller</author>
<title>Learned Index Structures at Scale.</title><pages>1-10</pages><year>2024</year><journal>J. Data</journal>
<ee>https://doi.org/10.1000/LIS</ee><url>db/journals/x/x24.html#Lin24</url>
</article>
<inproceedings mdate="2024-01-01" key="conf/y/Lin25">
<author>M. Lin 0001</author><title>Preprinted Paper on Graphs.</title><year>2025</year><booktitle>Conf Y</booktitle>
<ee>https://arxiv.org/abs/2501.01234</ee>
</inproceedings>
<inproceedings mdate="2024-01-01" key="conf/z/Lin23">
<author>Mei Lin 0002</author><title>A Namesake's Work on Chemistry.</title><year>2023</year>
</inproceedings>
<proceedings mdate="2024-01-01" key="conf/y/2025">
<editor>Mei Lin 0001</editor><title>Proceedings of Y.</title></proceedings>
</dblp>
"""


def profile(name='Mei Lin'):
    return ProfessorProfile(
        name=name,
        affiliations=[],
        author_ids={},
        identity_confidence=1.0,
        status='resolved',
        evidence=[],
        requested_name=name,
    )


class DumpTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.dump = root / 'dblp.xml.gz'
        with gzip.open(self.dump, 'wb') as handle:
            handle.write(DUMP.encode('iso-8859-1'))
        self.index_path = root / 'index' / 'dblp.sqlite'
        self.statistics = DblpDumpIndexer(replace(DEFAULT_DBLP_DUMP, batch_size=2)).build(self.dump, self.index_path)
        self.index = DblpDumpIndex(self.index_path)

    def tearDown(self):
        self.directory.cleanup()


class IndexTests(DumpTestCase):
    def test_person_pages_and_publications_are_indexed_and_the_rest_skipped(self):
        self.assertEqual((self.statistics.persons, self.statistics.records), (3, 4))
        self.assertEqual(self.statistics.skipped, {'www': 2})
        self.assertFalse(self.index_path.with_name('dblp.sqlite.partial').exists())

    def test_search_finds_homonyms_by_their_printed_name_most_published_first(self):
        self.assertEqual(self.index.search('Mei Lin', 10), [('11/1', 'Mei Lin 0001'), ('22/2', 'Mei Lin 0002')])
        self.assertEqual(self.index.search('Jürgen Müller', 10), [('33/3', 'Jürgen Müller')])
        self.assertEqual(base_name('Wei Wang 0001'), 'Wei Wang')

    def test_bibliography_has_the_api_shape_with_author_pids(self):
        root = self.index.bibliography('11/1')
        self.assertEqual((root.tag, root.get('pid'), root.get('n')), ('dblpperson', '11/1', '3'))
        self.assertEqual(root.find('person/note').text, 'Example University, Department of Computer Science')
        article = root.find("r/article[@key='journals/x/Lin24']")
        self.assertEqual([author.get('pid') for author in article.findall('author')], ['11/1', '33/3'])
        self.assertIsNone(article.find('pages'))  # dropped: not in kept_fields
        self.assertIsNone(self.index.bibliography('99/9'))

    def test_an_index_of_another_schema_must_be_rebuilt(self):
        old = Path(self.directory.name) / 'old.sqlite'
        sqlite3.connect(old).executescript('CREATE TABLE records(key TEXT, xml TEXT);').close()
        with self.assertRaises(ValueError) as error:
            DblpDumpIndex(old)
        self.assertIn('rebuild', str(error.exception))

    def test_a_missing_index_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            DblpDumpIndex(Path(self.directory.name) / 'absent.sqlite')


class CollisionTests(unittest.TestCase):
    def test_reads_stay_exact_when_every_hash_collides(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(dblp_dump, 'digest', return_value=7):
            dump = Path(directory) / 'dblp.xml'
            dump.write_text(DUMP, encoding='iso-8859-1')
            path = Path(directory) / 'i.sqlite'
            DblpDumpIndexer().build(dump, path)
            index = DblpDumpIndex(path)
            root = index.bibliography('22/2')
            self.assertEqual([record.get('key') for record in root.iter('inproceedings')], ['conf/z/Lin23'])
            self.assertEqual([record.get('key') for record in index.works('doi:10.1000/lis')], ['journals/x/Lin24'])


class ProviderTests(DumpTestCase):
    def test_the_unchanged_dblp_providers_run_on_the_dump_client(self):
        client = DblpDumpClient(self.index)
        identity = DBLPProvider(client=client)
        candidates = identity.search(Q('Mei Lin', 'Example University'))
        self.assertEqual([candidate.author_id for candidate in candidates], ['11/1', '22/2'])
        first = candidates[0]
        self.assertIn('Example University, Department of Computer Science', first.affiliations)
        self.assertEqual(first.orcid, 'https://orcid.org/0000-0002-1825-0097')
        self.assertIn('Jürgen Müller', first.coauthors)
        self.assertEqual(first.paper_ids, ['doi:10.1000/lis'])
        collected = DBLPPaperProvider(identity).collect('11/1')
        self.assertTrue(collected.complete, collected.error)
        verified = {paper.source_paper_id: paper.target_author_present for paper in collected.papers}
        self.assertEqual(verified, {'journals/x/Lin24': True, 'conf/y/Lin25': True, 'conf/y/2025': False})

    def test_other_endpoints_and_unknown_pids_are_404s(self):
        client = DblpDumpClient(self.index)
        with self.assertRaises(SourceError) as missing:
            client.get('https://dblp.org/pid/99/9.xml', xml=True)
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(SourceError):
            client.get('https://dblp.org/rec/x.xml')


class LookupTests(DumpTestCase):
    def test_listed_works_are_found_by_doi_arxiv_and_title_as_the_persons_record(self):
        lookup = DblpWorkLookup(self.index, AuthorMatcher())
        by_doi = lookup.find('doi:10.1000/lis', profile())
        self.assertEqual((by_doi.source, by_doi.source_author_id, by_doi.doi), ('dblp', '11/1', '10.1000/lis'))
        self.assertEqual(lookup.find('arxiv:2501.01234', profile()).source_paper_id, 'conf/y/Lin25')
        title = next(iter(work_keys(titles=["A Namesake's Work on Chemistry"])))
        by_title = lookup.find(title, profile())
        self.assertEqual(by_title.source_author_id, '22/2')
        self.assertIsNone(lookup.find('doi:10.1000/lis', profile('Ana Other')))
        self.assertIsNone(lookup.find('doi:10.9/none', profile()))


class CompositionTests(DumpTestCase):
    def test_a_built_index_replaces_the_dblp_api(self):
        settings = Settings.from_mapping({'dblp_dump': {'index_path': str(self.index_path)}})
        container = ServiceContainer(settings, RunOptions(mode='metadata'))
        dblp = next(provider for provider in container.identity_providers if provider.name == 'dblp')
        self.assertIsInstance(dblp.client, DblpDumpClient)
        self.assertIn('dblp', [lookup.name for lookup in container.listed_work_retriever.lookups])
        plain = ServiceContainer(Settings(), RunOptions(mode='metadata'))
        self.assertIsNone(plain.dblp_index)
        self.assertNotIn('dblp', [lookup.name for lookup in plain.listed_work_retriever.lookups])
        absent = Settings.from_mapping({'dblp_dump': {'index_path': str(Path(self.directory.name) / 'none')}})
        with self.assertRaises(FileNotFoundError):
            _ = ServiceContainer(absent, RunOptions(mode='metadata')).dblp_index

    def test_the_cli_builds_an_index_and_reports_statistics(self):
        target = Path(self.directory.name) / 'cli.sqlite'
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(dblp_main(['--dump', str(self.dump), '--index', str(target)]), 0)
        self.assertEqual(json.loads(output.getvalue())['records'], 4)
        self.assertTrue(target.is_file())


if __name__ == '__main__':
    unittest.main()
