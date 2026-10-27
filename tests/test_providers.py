import unittest
import xml.etree.ElementTree as ET

from paper_author_searcher import ProfessorQuery
from paper_author_searcher.providers import (
    DBLPProvider,
    OpenAlexProvider,
    SemanticScholarProvider,
    SourceError,
)


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, xml=False):
        self.calls.append((url, params, headers, xml))
        if url.endswith('/institutions'):
            # Institution lookup for scoped author search; fixtures that queue responses do not model it.
            return {'results': []}
        return next(self.responses)


class UnavailableEnrichmentClient(Client):
    def get(self, url, params=None, headers=None, xml=False):
        if url.endswith('/works'):
            raise SourceError('HTTP 429')
        if xml:
            raise SourceError('HTML challenge')
        return super().get(url, params, headers, xml)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.query = ProfessorQuery('Alex Morgan', 'Example University')

    def test_openalex_adds_institution_scoped_namesakes(self):
        famous = {'id': 'https://openalex.org/A1', 'display_name': 'Alex Morgan', 'affiliations': []}
        local = {
            'id': 'https://openalex.org/A2',
            'display_name': 'Alex Morgan',
            'last_known_institutions': [{'display_name': 'Example University'}],
        }

        class ScopedClient(Client):
            def get(self, url, params=None, headers=None, xml=False):
                self.calls.append((url, params, headers, xml))
                if url.endswith('/institutions'):
                    return {
                        'results': [
                            {'id': 'https://openalex.org/I9', 'display_name': 'Example University'},
                            {'id': 'https://openalex.org/I8', 'display_name': 'Other College'},
                        ]
                    }
                if url.endswith('/authors'):
                    return {'results': [local] if 'filter' in params else [famous]}
                return {'results': []}

        client = ScopedClient([])
        candidates = OpenAlexProvider(client).search(self.query)
        self.assertEqual(sorted(candidate.author_id for candidate in candidates), ['A1', 'A2'])
        scoped_call = next(call for call in client.calls if call[0].endswith('/authors') and 'filter' in call[1])
        self.assertEqual(scoped_call[1]['filter'], 'affiliations.institution.id:I9')
        works = [call[1]['filter'] for call in client.calls if call[0].endswith('/works')]
        self.assertEqual(works[0], 'authorships.author.id:A2')

    def test_openalex(self):
        author = {
            'id': 'https://openalex.org/fixture-author',
            'display_name': 'Alex Morgan',
            'last_known_institutions': [{'display_name': 'Example University'}],
            'affiliations': [{'institution': {'display_name': 'Western University'}}],
            'topics': [
                {
                    'display_name': 'Biodiversity',
                    'field': {'display_name': 'Ecology'},
                }
            ],
        }
        client = Client([{'results': [author]}])
        record = OpenAlexProvider(client, enrich_limit=0).search(self.query)[0]
        self.assertEqual(record.author_id, 'fixture-author')
        self.assertIn('Western University', record.affiliations)
        self.assertIn('Ecology', record.topics)

    def test_semantic_scholar(self):
        author = {
            'authorId': 'fixture-author',
            'name': 'Alex Morgan',
            'affiliations': [],
            'externalIds': {'ORCID': 'fixture-orcid'},
            'papers': [
                {
                    'title': 'Biodiversity',
                    'externalIds': {'DOI': '10.example/ABC'},
                },
                {
                    'title': 'Long Range Dispersal of Seeds in Fragmented Forests',
                    'externalIds': {'ArXiv': '2401.00001'},
                },
            ],
        }
        client = Client([{'data': [author]}])
        record = SemanticScholarProvider(client).search(self.query)[0]
        self.assertEqual(record.paper_ids, ['doi:10.example/abc'])
        self.assertEqual(
            record.works,
            [
                ('doi:10.example/abc',),
                ('arxiv:2401.00001', 'title:long range dispersal of seeds in fragmented forests'),
            ],
        )
        self.assertEqual(record.orcid, 'fixture-orcid')

    def test_dblp(self):
        info = {
            'author': 'Alex E. Morgan',
            'url': 'https://dblp.org/pid/example/author',
        }
        root = ET.fromstring("""
            <dblpperson>
                <person>
                    <author>Alex E. Morgan</author>
                    <note type="affiliation">Example University</note>
                    <url>https://orcid.org/fixture-orcid</url>
                </person>
                <r><article>
                    <title>A <i>sample</i> paper</title>
                    <ee>https://doi.org/10.example/ABC</ee>
                </article></r>
            </dblpperson>
        """)
        client = Client([{'result': {'hits': {'hit': {'info': info}}}}, root])
        record = DBLPProvider(client).search(self.query)[0]
        self.assertEqual(record.author_id, 'example/author')
        self.assertEqual(record.affiliations, ['Example University'])
        self.assertEqual(record.current_affiliations, ['Example University'])
        self.assertEqual(record.topics, ['A sample paper'])
        self.assertEqual(record.paper_ids, ['doi:10.example/abc'])

    def test_dblp_affiliation_notes_split_into_institution_and_unit(self):
        info = {'author': 'Yang Liu 0005', 'url': 'https://dblp.org/pid/51/3710-5'}
        current = 'Tsinghua University, Department of Computer Science and Technology, China'
        former = 'University of California, Los Angeles, Department of Computer Science, CA, USA'
        root = ET.fromstring(f"""
            <dblpperson>
                <person>
                    <author>Yang Liu 0005</author>
                    <note type="affiliation">{current}</note>
                    <note type="affiliation" label="former">{former}</note>
                </person>
            </dblpperson>
        """)
        client = Client([{'result': {'hits': {'hit': {'info': info}}}}, root])
        record = DBLPProvider(client).search(ProfessorQuery('Yang Liu', 'Tsinghua University'))[0]
        self.assertIn('Tsinghua University', record.affiliations)
        # A comma inside the institution's name: every prefix before the unit is kept.
        self.assertIn('University of California, Los Angeles', record.affiliations)
        self.assertEqual(
            record.departments, ['department of computer science and technology', 'department of computer science']
        )
        # Only the unlabelled note is current; "former" is history.
        self.assertEqual(record.current_affiliations, ['Tsinghua University'])

    def test_openalex_enrichment_uses_own_authorship(self):
        author = {'id': 'https://openalex.org/fixture-author', 'display_name': 'Alex Morgan'}
        work = {
            'doi': 'https://doi.org/10.example/A',
            'topics': [{'display_name': 'Ecology'}],
            'authorships': [
                {
                    'author': author,
                    'institutions': [{'display_name': 'Example University'}],
                    'raw_affiliation_strings': ['Department of Ecology, Example University'],
                },
                {
                    'author': {'id': 'https://openalex.org/coauthor', 'display_name': 'Someone Else'},
                    'institutions': [{'display_name': 'Other University'}],
                    'raw_affiliation_strings': ['Department of Physics, Other University'],
                },
            ],
        }
        client = Client([{'results': [author]}, {'results': [work]}])
        record = OpenAlexProvider(client).search(self.query)[0]
        self.assertEqual(record.affiliations, ['Example University'])
        self.assertEqual(record.coauthors, ['Someone Else'])
        # Only the candidate's own printed affiliation lines, one tuple per work.
        self.assertEqual(record.bylines, [('Department of Ecology, Example University',)])
        self.assertEqual(record.paper_ids, ['doi:10.example/a'])
        self.assertIn('select', client.calls[1][1])

    def test_enrichment_failure_preserves_candidate(self):
        author = {
            'id': 'https://openalex.org/fixture-author',
            'display_name': 'Alex Morgan',
            'affiliations': [{'institution': {'display_name': 'Example University'}}],
        }
        client = UnavailableEnrichmentClient([{'results': [author]}])
        record = OpenAlexProvider(client).search(self.query)[0]
        self.assertEqual(record.affiliations, ['Example University'])
        self.assertEqual(record.metadata_errors, ['HTTP 429'])

    def test_dblp_xml_failure_retains_search_hit(self):
        info = {'author': 'Alex Morgan', 'url': 'https://dblp.org/pid/example/author'}
        response = {'result': {'hits': {'hit': [{'info': info}]}}}
        client = UnavailableEnrichmentClient([response])
        record = DBLPProvider(client).search(self.query)[0]
        self.assertEqual(record.author_id, 'example/author')
        self.assertEqual(record.metadata_errors, ['HTML challenge'])
