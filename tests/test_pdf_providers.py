import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from unittest.mock import Mock, patch

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError, URLProbe
from paper_author_searcher.pacing import RequestPacer
from paper_author_searcher.pdf_identity import arxiv_id, metadata_identity
from paper_author_searcher.pdf_providers import (
    ArxivPDFProvider,
    EuropePMCPDFProvider,
    ExistingPDFProvider,
    HomepagePDFProvider,
    LandingPagePDFProvider,
    NBERPDFProvider,
    OpenAIREPDFProvider,
    OpenAlexPDFProvider,
    SemanticScholarPDFProvider,
    UnpaywallPDFProvider,
    openalex_arxiv_ids,
)


def paper():
    return {
        'title': 'Evidence for a general method',
        'year': 2020,
        'doi': '10.1234/example',
        'authors': ['Alex Example'],
        'ownership_status': 'verified',
        'records': [
            {
                'source': 'openalex',
                'source_paper_id': 'https://openalex.org/W101',
                'doi': '10.1234/example',
                'raw_metadata': {},
            },
            {'source': 'semantic_scholar', 'source_paper_id': 'abc123', 'raw_metadata': {}},
        ],
    }


def openalex_work():
    return {
        'id': 'https://openalex.org/W101',
        'title': 'Evidence for a general method',
        'doi': 'https://doi.org/10.1234/example',
        'primary_location': {
            'is_oa': True,
            'pdf_url': 'https://repository.example.edu/paper.pdf',
            'landing_page_url': 'https://repository.example.edu/paper',
            'version': 'acceptedVersion',
            'license': 'cc-by',
        },
    }


class IdentityTests(unittest.TestCase):
    def test_stable_source_id_requires_corroboration(self):
        saved = paper()
        self.assertEqual(metadata_identity(saved, 'openalex', 'W101')[0], 'unverified')
        self.assertEqual(metadata_identity(saved, 'openalex', 'W101', title=saved['title'])[0], 'verified')

    def test_conflicting_doi_or_title_blocks_link(self):
        saved = paper()
        self.assertEqual(
            metadata_identity(saved, 'openalex', 'W101', doi='10.1234/other', title=saved['title'])[0], 'conflicting'
        )
        self.assertEqual(
            metadata_identity(saved, 'openalex', 'W101', doi=saved['doi'], title='A different work')[0], 'conflicting'
        )

    def test_title_alone_is_not_verified(self):
        saved = paper()
        self.assertEqual(metadata_identity(saved, 'landing_page', title=saved['title'])[0], 'unverified')
        self.assertEqual(
            metadata_identity(saved, 'landing_page', title=saved['title'], year=2020, authors=['A. Example'])[0],
            'verified',
        )

    def test_rejected_source_record_cannot_bind_pdf(self):
        saved = paper()
        saved['records'][0]['ownership_status'] = 'rejected'
        self.assertEqual(metadata_identity(saved, 'openalex', 'W101', title=saved['title'])[0], 'unverified')

    def test_title_year_and_one_shared_author_do_not_verify_different_author_lists(self):
        saved = paper()
        saved['authors'].append('Taylor Researcher')
        identity = metadata_identity(
            saved, 'landing_page', title=saved['title'], year=2020, authors=['Alex Example', 'Unrelated Person']
        )
        self.assertEqual(identity[0], 'unverified')

    def preprint_paper(self):
        """A saved arXiv paper (its DOI is the arXiv DOI) whose preprint was renamed for its journal edition."""
        return dict(
            paper(),
            title='CRISPR-GPT: An LLM Agent for Automated Design of Gene-Editing Experiments',
            doi='10.48550/arxiv.2404.18021',
            year=2024,
            authors=['Kaixuan Huang', 'Yuanhao Qu', 'Henry Cousins'],
            records=[],
        )

    def test_the_journal_edition_of_an_arxiv_paper_is_not_a_doi_conflict(self):
        saved = self.preprint_paper()
        identity = metadata_identity(
            saved,
            'openalex',
            'W4412759774',
            doi='10.1038/s41551-025-01463-z',
            title='CRISPR-GPT for agentic automation of gene-editing experiments',
            authors=['Yuanhao Qu', 'Kaixuan Huang', 'Ming Yin', 'Henry C. Cousins'],
            editions=['2404.18021'],
        )
        self.assertEqual(identity[0], 'verified')
        self.assertIn('title changed between editions', identity[1][0])

    def test_own_arxiv_id_with_a_renamed_title_needs_shared_authors(self):
        saved = self.preprint_paper()
        renamed = 'CRISPR-GPT for Agentic Automation of Gene-editing Experiments'
        shared = metadata_identity(
            saved, 'arxiv', '2404.18021v2', title=renamed, authors=['Yuanhao Qu', 'Kaixuan Huang']
        )
        self.assertEqual(shared[0], 'verified')
        strangers = metadata_identity(saved, 'arxiv', '2404.18021v2', title=renamed, authors=['Unrelated Person'])
        self.assertEqual(strangers[0], 'conflicting')
        self.assertEqual(metadata_identity(saved, 'arxiv', '2404.18021v2', title=renamed)[0], 'conflicting')

    def test_another_works_arxiv_id_does_not_excuse_a_doi_conflict(self):
        saved = self.preprint_paper()
        identity = metadata_identity(
            saved, 'openalex', 'W1', doi='10.1038/other', title=saved['title'], editions=['2401.00001']
        )
        self.assertEqual(identity[0], 'conflicting')

    def test_preprint_dated_a_year_early_binds_with_title_and_full_byline(self):
        saved = dict(paper(), year=2024, authors=['Adam Winnifrith', 'Carlos Outeiral', 'Brian L. Hie'])
        authors = ['Adam Winnifrith', 'Carlos Outeiral', 'Brian Hie']
        early = metadata_identity(saved, 'arxiv', '2310.09685v1', title=saved['title'], year='2023', authors=authors)
        self.assertEqual(early[0], 'verified')
        far = metadata_identity(saved, 'arxiv', '2310.09685v1', title=saved['title'], year='2022', authors=authors)
        self.assertEqual(far[0], 'unverified')
        partial = metadata_identity(
            saved,
            'arxiv',
            '2310.09685v1',
            title=saved['title'],
            year='2023',
            authors=authors[:2] + ['Unrelated Person'],
        )
        self.assertEqual(partial[0], 'unverified')

    def test_year_tolerance_is_configurable(self):
        saved = dict(paper(), year=2024)
        strict = replace(DEFAULT_PDF, identity_year_tolerance=0)
        identity = metadata_identity(
            saved, 'landing_page', title=saved['title'], year=2023, authors=['Alex Example'], config=strict
        )
        self.assertEqual(identity[0], 'unverified')

    def test_openalex_lists_its_merged_arxiv_copies(self):
        locations = [
            {'id': 'pmh:oai:arXiv.org:2404.18021', 'landing_page_url': 'https://arxiv.org/abs/2404.18021'},
            {'id': 'doi:10.1038/x', 'pdf_url': 'https://arxiv.org/pdf/2404.18021v2'},
            {'id': 'pmh:oai:europepmc.org:1', 'landing_page_url': 'https://europepmc.org/abstract/MED/1'},
        ]
        self.assertEqual(openalex_arxiv_ids(locations), ['2404.18021', '2404.18021v2'])

    def test_arxiv_identifiers_preserve_versions(self):
        self.assertEqual(arxiv_id('https://arxiv.org/pdf/2401.00001v2.pdf'), '2401.00001v2')
        self.assertEqual(arxiv_id('arXiv:hep-th/9901001'), 'hep-th/9901001')
        self.assertIsNone(arxiv_id('https://unrelated.example.org/pdf/2401.00001'))


class ProviderTests(unittest.TestCase):
    def test_existing_metadata_uses_explicit_oa_pdf_only(self):
        saved = paper()
        saved['records'][0]['raw_metadata'] = openalex_work()
        result = ExistingPDFProvider().find(saved)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertEqual(result.candidates[0].version_type, 'accepted_manuscript')
        saved['records'][0]['raw_metadata']['primary_location']['is_oa'] = False
        self.assertEqual(ExistingPDFProvider().find(saved).candidates, [])

    def test_ordinary_paper_url_never_assumed_pdf(self):
        saved = paper()
        saved['records'][0]['paper_url'] = 'https://publisher.example.edu/article.pdf'
        self.assertEqual(ExistingPDFProvider().find(saved).candidates, [])

    def test_malformed_cached_location_does_not_discard_other_source(self):
        saved = paper()
        saved['records'][0]['raw_metadata'] = dict(openalex_work(), locations=[None])
        saved['records'][1]['raw_metadata'] = {
            'paperId': 'abc123',
            'title': saved['title'],
            'openAccessPdf': {'url': 'https://repo.example.edu/a.pdf'},
        }
        result = ExistingPDFProvider().find(saved)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(len(result.candidates), 1)
        self.assertTrue(result.errors)

    def test_malformed_api_location_is_source_error(self):
        client = Mock()
        client.get.return_value = dict(openalex_work(), locations='wrong shape')
        self.assertEqual(OpenAlexPDFProvider(client).find(paper()).status, 'error')

    def test_openalex_requests_all_locations_and_preserves_versions(self):
        client = Mock()
        work = openalex_work()
        work['locations'] = [
            {'is_oa': True, 'pdf_url': 'https://arxiv.org/pdf/2001.00001', 'version': 'submittedVersion'}
        ]
        client.get.return_value = work
        result = OpenAlexPDFProvider(client).find(paper())
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.candidates[0].version_type, 'submitted_manuscript')
        self.assertIn('locations', client.get.call_args[1]['params']['select'])
        self.assertTrue(client.get.call_args[0][0].endswith('/works/W101'))
        self.assertTrue(result.landing_pages)

    def test_semantic_scholar_requests_open_access_pdf(self):
        client = Mock()
        client.get.return_value = {
            'paperId': 'abc123',
            'title': paper()['title'],
            'externalIds': {'DOI': paper()['doi']},
            'openAccessPdf': {'url': 'https://repo.example.edu/a.pdf', 'status': 'GREEN'},
        }
        result = SemanticScholarPDFProvider(client, api_key='test-key').find(paper())
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertIn('openAccessPdf', client.get.call_args[1]['params']['fields'])
        self.assertEqual(client.get.call_args[1]['headers'], {'x-api-key': 'test-key'})
        self.assertEqual(result.candidates[0].version_type, 'unknown')

    def test_semantic_scholar_closed_or_empty_pdf_is_not_candidate(self):
        client = Mock()
        for location in ({'url': 'https://repo.example.edu/file', 'status': 'CLOSED'}, {'url': ''}, None):
            client.get.return_value = {'openAccessPdf': location}
            self.assertEqual(SemanticScholarPDFProvider(client).find(paper()).candidates, [])

    def test_doi_fallback_queries_metadata_not_pdf_guess(self):
        saved = paper()
        saved['records'] = []
        client = Mock()
        client.get.return_value = openalex_work()
        OpenAlexPDFProvider(client).find(saved)
        self.assertIn('/works/https://doi.org/10.1234/example', client.get.call_args[0][0])
        client.get.return_value = {'openAccessPdf': None}
        SemanticScholarPDFProvider(client).find(saved)
        self.assertIn('/paper/DOI:10.1234%2Fexample', client.get.call_args[0][0])

    def test_one_lookup_failure_retains_other_versions(self):
        saved = paper()
        saved['records'].append({'source': 'openalex', 'source_paper_id': 'W102'})
        client = Mock()
        client.get.side_effect = [SourceError('HTTP 429'), openalex_work()]
        result = OpenAlexPDFProvider(client).find(saved)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(len(result.errors), 1)

    def test_unpaywall_requires_real_email_and_uses_pdf_field(self):
        client = Mock()
        with patch.dict('os.environ', {}, clear=True):
            result = UnpaywallPDFProvider(client).find(paper())
        self.assertEqual(result.status, 'disabled')
        client.get.assert_not_called()
        client.get.return_value = {
            'doi': paper()['doi'],
            'title': paper()['title'],
            'is_oa': True,
            'oa_locations': [
                {'url': 'https://repo.example.edu/page'},
                {'url_for_pdf': 'https://repo.example.edu/file', 'version': 'publishedVersion'},
            ],
        }
        result = UnpaywallPDFProvider(client, email='person@example.edu').find(paper())
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].version_type, 'published_version')
        self.assertEqual(client.get.call_args[1]['params']['email'], 'person@example.edu')

    def test_europepmc_offers_only_free_pdf_links(self):
        client = Mock()
        client.get.return_value = {
            'resultList': {
                'result': [
                    {
                        'doi': paper()['doi'],
                        'title': paper()['title'],
                        'pmcid': 'PMC11194069',
                        'fullTextUrlList': {
                            'fullTextUrl': [
                                {'documentStyle': 'doi', 'availabilityCode': 'S', 'url': 'https://doi.org/x'},
                                {'documentStyle': 'pdf', 'availabilityCode': 'S', 'url': 'https://pub.example/pdf'},
                                {'documentStyle': 'html', 'availabilityCode': 'F', 'url': 'https://epmc/PMC1'},
                                {
                                    'documentStyle': 'pdf',
                                    'availabilityCode': 'F',
                                    'availability': 'Free',
                                    'site': 'Europe_PMC',
                                    'url': 'https://europepmc.org/articles/PMC11194069?pdf=render',
                                },
                            ]
                        },
                    }
                ]
            }
        }
        result = EuropePMCPDFProvider(client).find(paper())
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            ['https://europepmc.org/articles/PMC11194069?pdf=render'],
        )
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertEqual(client.get.call_args[1]['params']['query'], 'DOI:"10.1234/example"')

    def test_europepmc_needs_a_doi(self):
        saved = dict(paper(), doi=None, records=[])
        self.assertEqual(EuropePMCPDFProvider(Mock()).find(saved).status, 'not_applicable')

    def test_openaire_offers_pdf_files_and_passes_on_landing_pages(self):
        client = Mock()
        client.get.return_value = {
            'results': [
                {
                    'mainTitle': paper()['title'],
                    'bestAccessRight': {'label': 'OPEN'},
                    'instances': [
                        {'urls': ['https://repo.example.edu/item/1', 'https://repo.example.edu/item/1/file.PDF']},
                        {'urls': ['ftp://old.example/file.pdf', None]},
                    ],
                }
            ]
        }
        result = OpenAIREPDFProvider(client).find(paper())
        self.assertEqual(
            [candidate.url for candidate in result.candidates], ['https://repo.example.edu/item/1/file.PDF']
        )
        self.assertEqual(
            [page['landing_page_url'] for page in result.landing_pages], ['https://repo.example.edu/item/1']
        )
        self.assertEqual(client.get.call_args[1]['params'], {'pid': '10.1234/example'})

    def test_openaire_ignores_works_it_does_not_rate_open(self):
        client = Mock()
        client.get.return_value = {
            'results': [{'bestAccessRight': {'label': 'CLOSED'}, 'instances': [{'urls': ['https://x.edu/a.pdf']}]}]
        }
        result = OpenAIREPDFProvider(client).find(paper())
        self.assertEqual((result.candidates, result.landing_pages), ([], []))

    def test_arxiv_reads_returned_link_and_version(self):
        saved = paper()
        saved['doi'] = None
        saved['records'] = [{'external_ids': {'arxiv': '2001.00001v2'}}]
        feed = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>https://arxiv.org/abs/2001.00001v2</id><title>Evidence for a general method</title>
        <link type="application/pdf" href="https://arxiv.org/pdf/2001.00001v2"/>
        </entry></feed>"""
        client = Mock()
        client.get.return_value = ET.fromstring(feed)
        result = ArxivPDFProvider(client).find(saved)
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertEqual(result.candidates[0].version_type, 'preprint')
        self.assertEqual(client.get.call_args[1]['params']['id_list'], '2001.00001v2')

    def test_arxiv_title_search_can_be_disabled_and_wrong_version_is_refused(self):
        client = Mock()
        self.assertEqual(
            ArxivPDFProvider(client, replace(DEFAULT_PDF, arxiv_title_search=False)).find(paper()).status,
            'not_applicable',
        )
        client.get.assert_not_called()
        saved = paper()
        saved['records'][0]['external_ids'] = {'arxiv': '2001.00001v2'}
        client.get.return_value = ET.fromstring(
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><id>http://arxiv.org/abs/2001.00001v3</id></entry></feed>'
        )
        self.assertEqual(ArxivPDFProvider(client).find(saved).status, 'error')

    def test_arxiv_observes_configured_interval(self):
        provider = ArxivPDFProvider(Mock(), replace(DEFAULT_PDF, arxiv_request_interval=3))
        sleep = Mock()
        provider.pacer = RequestPacer(3, clock=iter([10, 11]).__next__, sleep=sleep)
        provider._wait_for_interval()  # first request at t=10 starts immediately
        provider._wait_for_interval()  # next one at t=11 waits for t=13
        sleep.assert_called_once_with(2)

    def test_landing_page_uses_explicit_citation_link_and_identity(self):
        saved = paper()
        saved['records'][0]['paper_url'] = 'https://repo.example.edu/page'
        html = b"""<meta name="citation_doi" content="10.1234/example">
        <meta name="citation_title" content="Evidence for a general method">
        <meta name="citation_pdf_url" content="/files/paper.pdf">"""
        client = Mock()
        client.probe.return_value = URLProbe('https://repo.example.edu/page', 200, 'text/html', {}, html)
        result = LandingPagePDFProvider(client).find(saved)
        self.assertEqual(result.candidates[0].url, 'https://repo.example.edu/files/paper.pdf')
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertEqual(result.candidates[0].version_type, 'unknown')

    def test_landing_page_arbitrary_links_ignored_and_wrong_doi_retained(self):
        saved = paper()
        saved['records'][0]['paper_url'] = 'https://repo.example.edu/page'
        client = Mock()
        client.probe.return_value = URLProbe(
            'https://repo.example.edu/page', 200, 'text/html', {}, b'<a href="a.pdf">download</a>'
        )
        self.assertEqual(LandingPagePDFProvider(client).find(saved).candidates, [])
        client.probe.return_value.prefix = (
            b'<meta name="citation_doi" content="10.1234/wrong">' b'<meta name="citation_pdf_url" content="a.pdf">'
        )
        self.assertEqual(LandingPagePDFProvider(client).find(saved).candidates[0].identity_status, 'conflicting')

    def test_multi_paper_landing_page_requires_review(self):
        saved = paper()
        saved['records'][0]['paper_url'] = 'https://repo.example.edu/page'
        client = Mock()
        client.probe.return_value = URLProbe(
            'https://repo.example.edu/page',
            200,
            'text/html',
            {},
            b'<meta name="citation_title" content="First work">'
            b'<meta name="citation_title" content="Second work">'
            b'<meta name="citation_pdf_url" content="a.pdf">',
        )
        self.assertEqual(LandingPagePDFProvider(client).find(saved).candidates[0].identity_status, 'unverified')

    def test_oa_landing_url_must_actually_return_pdf_before_becoming_candidate(self):
        saved = paper()
        location = dict(
            openalex_work()['primary_location'],
            database='openalex',
            source_paper_id='W101',
            title=saved['title'],
            doi=saved['doi'],
        )
        saved['_pdf_landing_pages'] = [location]
        client = Mock()
        client.probe.return_value = URLProbe(location['landing_page_url'], 200, 'application/pdf', {}, b'%PDF-1.7')
        self.assertEqual(LandingPagePDFProvider(client).find(saved).candidates[0].identity_status, 'verified')
        location['is_oa'] = False
        self.assertEqual(LandingPagePDFProvider(client).find(saved).candidates, [])


class HomepageProviderTests(unittest.TestCase):
    PAGE = 'https://lab.example.edu/publications'

    def listed(self, url, citation, dois=(), arxiv_ids=()):
        return {'url': url, 'page': self.PAGE, 'citation': citation, 'dois': list(dois), 'arxiv_ids': list(arxiv_ids)}

    def provider(self, *listed, papers=()):
        provider = HomepagePDFProvider()
        provider.prepare_profile({'listed_pdfs': list(listed)})
        provider.prepare(list(papers))
        return provider

    def test_a_citation_with_the_full_title_binds_its_pdf_without_a_request(self):
        paper = {'title': 'Seawater Alkalization via an Energy-Efficient Electrochemical Process'}
        citation = 'H. Guan, Y. Cui. Seawater alkalization via an energy-efficient electrochemical process. PNAS 2024.'
        result = self.provider(self.listed('https://lab.example.edu/Guan_PNAS_2024.pdf', citation)).find(paper)
        candidate = result.candidates[0]
        self.assertEqual(candidate.url, 'https://lab.example.edu/Guan_PNAS_2024.pdf')
        self.assertEqual(candidate.identity_status, 'verified')
        self.assertIn('full title', candidate.identity_evidence[0])
        self.assertEqual(candidate.provenance[0]['page_url'], self.PAGE)

    def test_identifiers_bind_whatever_the_citation_text(self):
        paper = {'title': 'Renamed', 'doi': '10.1000/X', 'external_ids': {'ArXiv': '2504.17102'}}
        provider = self.provider(
            self.listed('https://a.example/1.pdf', 'Other words', dois=['10.1000/x']),
            self.listed('https://arxiv.org/pdf/2504.17102', 'Other words', arxiv_ids=['2504.17102']),
        )
        self.assertEqual(len(provider.find(paper).candidates), 2)

    def test_a_citation_naming_another_doi_is_another_work(self):
        paper = {'title': 'Stable Lithium Metal Anodes with Interfacial Engineering', 'doi': '10.1000/anode'}
        listed = self.listed('https://a.example/x.pdf', paper['title'] + ' (comment)', dois=['10.1000/comment'])
        self.assertEqual(self.provider(listed).find(paper).candidates, [])

    def test_short_titles_and_unlisted_papers_bind_nothing(self):
        listed = self.listed('https://a.example/x.pdf', 'Deep Learning. A survey of everything.')
        self.assertEqual(self.provider(listed).find({'title': 'Deep Learning'}).candidates, [])
        self.assertEqual(self.provider().find({'title': 'Stable Lithium Metal Anodes Today'}).candidates, [])

    def test_a_pdf_linked_from_citations_of_two_papers_is_tied_to_neither(self):
        first = {'title': 'Force-Guided Bridge Matching for Full-Atom Dynamics'}
        second = {'title': 'VidEgoThink: Assessing Egocentric Video Understanding'}
        both = self.listed('https://a.example/iclr23.pdf', first['title'] + '. ' + second['title'] + '.')
        provider = self.provider(both, papers=[first, second, dict(first)])
        self.assertEqual(provider.find(first).candidates, [])
        self.assertEqual(provider.find(first).diagnostics[0]['reason'], 'linked from citations of several papers')


class NBERProviderTests(unittest.TestCase):
    def paper(self, doi='10.3982/ecta21791', related=('10.3386/w31185',)):
        versions = [{'identifiers': [{'source': 'crossref', 'paper_id': value, 'doi': value}]} for value in related]
        return {'title': 'Can Deficits Finance Themselves?', 'doi': doi, 'related_versions': versions}

    def test_a_related_working_paper_names_its_free_pdf_without_a_request(self):
        client = Mock()
        result = NBERPDFProvider(client).find(self.paper())
        candidate = result.candidates[0]
        self.assertEqual(candidate.url, 'https://www.nber.org/system/files/working_papers/w31185/w31185.pdf')
        self.assertEqual(candidate.identity_status, 'verified')
        self.assertIn('edition deduplication related', candidate.identity_evidence[0])
        self.assertEqual(candidate.version_type, 'submitted_manuscript')
        client.get.assert_not_called()

    def test_the_papers_own_nber_doi_is_an_exact_match(self):
        result = NBERPDFProvider(Mock()).find(self.paper(doi='10.3386/W32988', related=()))
        self.assertEqual(result.candidates[0].url.rsplit('/', 1)[-1], 'w32988.pdf')
        self.assertEqual(result.candidates[0].identity_evidence, ['Exact DOI agreement with saved paper metadata'])

    def test_other_dois_are_not_applicable(self):
        result = NBERPDFProvider(Mock()).find(self.paper(related=('10.2139/ssrn.4926054', '10.3386/digest')))
        self.assertEqual((result.status, result.candidates), ('not_applicable', []))
