"""LLM publication-list extraction with a fake completion and a fake Anthropic client (no network)."""

import json
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import DEFAULT_EXTRACTION
from paper_author_searcher.homepage_citations import HomepageCitation, HtmlCitationParser
from paper_author_searcher.publication_extraction import (
    PUBLICATION_SCHEMA,
    SYSTEM_PROMPT,
    CachedJsonCompletion,
    ClaudeJsonCompletion,
    LlmHtmlParser,
    LlmPdfParser,
    LlmPublicationExtractor,
    MergedCitationParser,
    chunk_lines,
    html_text,
)
from paper_author_searcher.settings import Credentials, Settings


def entry(title='A Study of Learned Index Structures', doi='', arxiv='', authors=('M. Lin', 'A. Other')):
    return {'title': title, 'authors': list(authors), 'year': '2024', 'venue': 'Conf', 'doi': doi, 'arxiv_id': arxiv}


class Completion:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete(self, system, prompt, schema):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else None


class ChunkingTests(unittest.TestCase):
    def test_lines_are_packed_greedily_within_the_limit(self):
        text = '\n'.join(['a' * 40] * 5)
        chunks = chunk_lines(text, 100)
        self.assertEqual([len(chunk.splitlines()) for chunk in chunks], [2, 2, 1])
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))

    def test_a_line_longer_than_the_limit_is_split_and_blank_lines_dropped(self):
        chunks = chunk_lines('x' * 250 + '\n\n  \nend', 100)
        self.assertEqual([len(chunk) for chunk in chunks], [100, 100, 50 + 1 + 3])


class HtmlTextTests(unittest.TestCase):
    def test_block_elements_become_lines_and_scripts_are_dropped(self):
        html = (
            '<html><head><style>p{}</style><script>var x=1</script></head><body>'
            '<h2>Papers</h2><ul><li>M. Lin. First paper title here.</li>'
            '<li>Second <b>bold</b> one <a href="https://doi.org/10.1000/xyz">pdf</a></li></ul></body></html>'
        )
        lines = html_text(html).splitlines()
        self.assertEqual(lines[0], 'Papers')
        self.assertEqual(lines[1], 'M. Lin. First paper title here.')
        self.assertIn('https://doi.org/10.1000/xyz', lines[2])
        self.assertNotIn('var x', html_text(html))


class ExtractorTests(unittest.TestCase):
    def test_entries_become_validated_citations(self):
        completion = Completion(
            {
                'publications': [
                    entry(doi='https://doi.org/10.1000/ABC.'),
                    entry(title='Short', arxiv='arXiv:2401.01234v2'),
                    entry(title='Talk', doi='not a doi'),
                ]
            }
        )
        citations = LlmPublicationExtractor(completion).extract('page text')
        self.assertEqual(len(citations), 2)
        self.assertEqual(citations[0].dois, ('10.1000/ABC',))
        self.assertEqual(citations[0].titles, ('A Study of Learned Index Structures',))
        self.assertIn('M. Lin, A. Other', citations[0].text)
        self.assertEqual((citations[1].titles, citations[1].arxiv_ids), ((), ('2401.01234',)))

    def test_every_chunk_is_one_request_up_to_the_configured_maximum(self):
        config = replace(DEFAULT_EXTRACTION, chunk_characters=1000, maximum_chunks=2)
        completion = Completion({'publications': [entry()]}, {}, {'publications': [entry()]})
        text = '\n'.join(['y' * 900] * 3)
        citations = LlmPublicationExtractor(completion, config).extract(text)
        self.assertEqual(len(completion.prompts), 2)
        self.assertEqual(len(citations), 1)

    def test_html_and_pdf_parsers_feed_the_extractor_text(self):
        completion = Completion({'publications': [entry()]}, {'publications': [entry()]})
        extractor = LlmPublicationExtractor(completion)
        LlmHtmlParser(extractor).parse('<p>Mei Lin. A paper.</p>')
        LlmPdfParser(extractor, text=lambda document: document.decode()).parse(b'CV text')
        self.assertEqual(completion.prompts, ['Mei Lin. A paper.', 'CV text'])


class Response(SimpleNamespace):
    pass


class FakeAnthropic:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response, self.error = response, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.response


def text_response(text, stop_reason='end_turn'):
    return Response(stop_reason=stop_reason, content=[SimpleNamespace(type='text', text=text)])


class ClaudeCompletionTests(unittest.TestCase):
    def test_request_uses_structured_output_a_cached_system_prompt_and_fallbacks(self):
        client = FakeAnthropic(text_response(json.dumps({'publications': []})))
        answer = ClaudeJsonCompletion(client=client).complete(SYSTEM_PROMPT, 'chunk', PUBLICATION_SCHEMA)
        self.assertEqual(answer, {'publications': []})
        request = client.calls[0]
        self.assertEqual(request['model'], DEFAULT_EXTRACTION.model)
        self.assertEqual(request['output_config']['format'], {'type': 'json_schema', 'schema': PUBLICATION_SCHEMA})
        self.assertEqual(request['system'][0]['cache_control'], {'type': 'ephemeral'})
        self.assertEqual(request['fallbacks'], 'default')

    def test_refusals_errors_and_invalid_json_give_no_answer(self):
        for client in (
            FakeAnthropic(text_response('{}', stop_reason='refusal')),
            FakeAnthropic(text_response('{}', stop_reason='max_tokens')),
            FakeAnthropic(text_response('not json')),
            FakeAnthropic(text_response('[1]')),
            FakeAnthropic(error=RuntimeError('network')),
        ):
            self.assertIsNone(ClaudeJsonCompletion(client=client).complete('s', 'p', {}))


class CacheTests(unittest.TestCase):
    def test_answers_are_reused_and_failures_are_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            inner = Completion(None, {'publications': [entry()]})
            cached = CachedJsonCompletion(inner, directory, 'model')
            self.assertIsNone(cached.complete('s', 'p', {}))
            self.assertEqual(cached.complete('s', 'p', {}), {'publications': [entry()]})
            self.assertEqual(cached.complete('s', 'p', {}), {'publications': [entry()]})
            self.assertEqual(len(inner.prompts), 2)
            other = CachedJsonCompletion(Completion(), directory, 'other-model')
            self.assertIsNone(other.complete('s', 'p', {}))


class MergeTests(unittest.TestCase):
    def test_later_parsers_add_only_works_not_already_found_and_failures_are_skipped(self):
        class Fixed:
            def __init__(self, citations):
                self.citations = citations

            def parse(self, document):
                return self.citations

        class Broken:
            def parse(self, document):
                raise ValueError('bad')

        rule = Fixed([HomepageCitation('a', ('10.1000/a',))])
        llm = Fixed(
            [
                HomepageCitation('a again', ('10.1000/A',)),
                HomepageCitation('b', titles=('A Study of Learned Index Structures',)),
            ]
        )
        merged = MergedCitationParser([rule, Broken(), llm]).parse('page')
        self.assertEqual([citation.text for citation in merged], ['a', 'b'])


class CompositionTests(unittest.TestCase):
    def test_extraction_joins_the_rule_based_parsers_only_with_a_key(self):
        plain = ServiceContainer(Settings(), RunOptions(mode='metadata')).citation_parsers
        self.assertIsInstance(plain[0], HtmlCitationParser)
        base = Settings()
        keyed = replace(
            base, credentials=Credentials(anthropic_api_key='k'), extraction=replace(base.extraction, enabled=True)
        )
        with tempfile.TemporaryDirectory() as directory:
            container = ServiceContainer(keyed, RunOptions(mode='metadata', http_cache_dir=directory))
            html, pdf = container.citation_parsers
            self.assertIsInstance(html, MergedCitationParser)
            self.assertIsInstance(pdf, MergedCitationParser)
            self.assertIsInstance(container.extraction_completion, CachedJsonCompletion)
            self.assertIs(container.anchor_finder.citation_parser, html)


if __name__ == '__main__':
    unittest.main()
