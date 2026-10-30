"""LLM extraction of a structured publication list from a researcher's page or CV.

The rule-based parsers (homepage_citations) find identifiers and emphasised or quoted titles; pages that print
plain-text citations (no links, no markup around titles) defeat them. Here the page text is split into chunks on
line boundaries and each chunk is sent once to a ``JsonCompletion`` constrained to ``PUBLICATION_SCHEMA``. Extracted
entries become ``HomepageCitation``s, are checked like parsed ones (identifier patterns, title length) and are merged
with the rule-based citations by ``MergedCitationParser``. The extraction only proposes a list; every listed work
is still looked up and verified like any other.
"""

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

from .config import DEFAULT_ANCHORS, DEFAULT_EXTRACTION, AnchorConfig, ExtractionConfig
from .homepage_citations import ARXIV_IN_TEXT, DOI_IN_TEXT, DocumentParser, HomepageCitation, pdf_text, title_like
from .http_budget import CostLedger
from .logging_config import log_isolated_failure
from .matching import canonical_arxiv, work_keys

logger = logging.getLogger(__name__)

PUBLICATION_SCHEMA = {
    'type': 'object',
    'properties': {
        'publications': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'authors': {'type': 'array', 'items': {'type': 'string'}},
                    'year': {'type': 'string'},
                    'venue': {'type': 'string'},
                    'doi': {'type': 'string'},
                    'arxiv_id': {'type': 'string'},
                },
                'required': ['title', 'authors', 'year', 'venue', 'doi', 'arxiv_id'],
                'additionalProperties': False,
            },
        }
    },
    'required': ['publications'],
    'additionalProperties': False,
}

TOKENS_PER_MTOK = 1_000_000
SYSTEM_PROMPT = (
    "You extract the publication list from text taken from a researcher's homepage, publication page or CV. "
    'Return every scholarly publication the text lists (articles, conference papers, preprints, book chapters), '
    'each once, with its title exactly as printed, its authors in printed order, its year, its venue, and a DOI or '
    'arXiv ID only when the text contains one. Use an empty string for anything the text does not state. Skip '
    'talks, courses, grants, awards, patents, software and news items. Never invent or complete an entry from '
    'memory; the text may end mid-list.'
)


class JsonCompletion(Protocol):
    """One request whose answer is a JSON object valid against ``schema``; None when there is no usable answer."""

    def complete(self, system: str, prompt: str, schema: dict) -> dict | None: ...


class ClaudeJsonCompletion:
    """Claude through the Anthropic SDK (optional extra ``llm``), with structured output and refusal fallback."""

    def __init__(
        self,
        config: ExtractionConfig = DEFAULT_EXTRACTION,
        client: Any = None,
        api_key: str | None = None,
        ledger: CostLedger | None = None,
    ):
        self._client = client
        self.api_key = api_key
        self.config = config
        self.ledger = ledger

    def _cost(self, usage: Any) -> float:
        config = self.config
        tokens = (
            (getattr(usage, 'input_tokens', 0) or 0, config.input_usd_per_mtok),
            (getattr(usage, 'output_tokens', 0) or 0, config.output_usd_per_mtok),
            (getattr(usage, 'cache_read_input_tokens', 0) or 0, config.cache_read_usd_per_mtok),
            (getattr(usage, 'cache_creation_input_tokens', 0) or 0, config.cache_write_usd_per_mtok),
        )
        return sum(count * price for count, price in tokens) / TOKENS_PER_MTOK

    @property
    def client(self) -> Any:
        """Built on first use, so a missing ``llm`` extra fails one request (the page keeps its rule-based parse)."""
        if self._client is None:
            import anthropic  # deferred: the core package has no third-party dependencies

            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def complete(self, system: str, prompt: str, schema: dict) -> dict | None:
        if self.ledger is not None and self.ledger.exhausted():
            logger.warning(
                'Claude budget of $%.2f reached; page left to the rule-based parser', self.config.maximum_cost_usd
            )
            return None
        try:
            response = self.client.beta.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                # The system prompt is identical for every chunk: cache it.
                system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
                messages=[{'role': 'user', 'content': prompt}],
                output_config={'effort': self.config.effort, 'format': {'type': 'json_schema', 'schema': schema}},
                # Route policy declines to a fallback model instead of failing the call.
                betas=['server-side-fallback-2026-07-01'],
                fallbacks='default',
            )
        except Exception as exc:  # network, auth or API errors leave the page to the rule-based parser
            log_isolated_failure(logger, 'Publication extraction request failed: %s', type(exc).__name__)
            return None
        if self.ledger is not None:
            self.ledger.record('claude', self._cost(getattr(response, 'usage', None)))
        if response.stop_reason in ('refusal', 'max_tokens'):
            logger.warning('Publication extraction stopped: %s', response.stop_reason)
            return None
        text = next((block.text for block in response.content if block.type == 'text'), '')
        try:
            data = json.loads(text)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None


class CachedJsonCompletion:
    """Keeps each answer in a file named by the hash of its request, so reruns and replays cost nothing."""

    def __init__(self, completion: JsonCompletion, directory: str | Path, namespace: str = '') -> None:
        self.completion = completion
        self.directory = Path(directory)
        self.namespace = namespace

    def complete(self, system: str, prompt: str, schema: dict) -> dict | None:
        request = json.dumps([self.namespace, system, prompt, schema], sort_keys=True, ensure_ascii=False)
        path = self.directory / (hashlib.sha256(request.encode('utf-8')).hexdigest() + '.json')
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        answer = self.completion.complete(system, prompt, schema)
        if answer is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(answer, ensure_ascii=False), encoding='utf-8')
        return answer


def chunk_lines(text: str, limit: int) -> list[str]:
    """Greedy packing of whole lines into chunks of at most ``limit`` characters (a longer line is split)."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        line = line.strip()
        pieces = [line[start : start + limit] for start in range(0, len(line), limit)] if line else []
        for piece in pieces:
            if current and size + len(piece) + 1 > limit:
                chunks.append('\n'.join(current))
                current, size = [], 0
            current.append(piece)
            size += len(piece) + 1
    if current:
        chunks.append('\n'.join(current))
    return chunks


class _TextCollector(HTMLParser):
    """Visible text with one line per block element; scripts and styles dropped."""

    SKIPPED = frozenset({'script', 'style', 'noscript', 'template'})

    def __init__(self, block_tags: Iterable[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.block_tags = frozenset(block_tags) | {'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        if tag in self.SKIPPED:
            self.skipping += 1
        elif tag in self.block_tags:
            self.parts.append('\n')
        if tag == 'a':
            # Keep DOI and arXiv links: they are identifiers the model may copy.
            self.parts.extend(f' {value} ' for name, value in attrs if name == 'href' and value and _identifier(value))

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIPPED and self.skipping:
            self.skipping -= 1
        elif tag in self.block_tags:
            self.parts.append('\n')

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def _identifier(value: str) -> bool:
    return bool(DOI_IN_TEXT.search(value) or ARXIV_IN_TEXT.search(value))


def html_text(html: str, config: AnchorConfig = DEFAULT_ANCHORS) -> str:
    collector = _TextCollector(config.citation_block_tags)
    collector.feed(html)
    collector.close()
    lines = (' '.join(line.split()) for line in ''.join(collector.parts).splitlines())
    return '\n'.join(line for line in lines if line)


class LlmPublicationExtractor:
    def __init__(
        self,
        completion: JsonCompletion,
        config: ExtractionConfig = DEFAULT_EXTRACTION,
        anchor_config: AnchorConfig = DEFAULT_ANCHORS,
    ) -> None:
        self.completion, self.config, self.anchor_config = completion, config, anchor_config

    def extract(self, text: str) -> list[HomepageCitation]:
        citations: list[HomepageCitation] = []
        for chunk in chunk_lines(text, self.config.chunk_characters)[: self.config.maximum_chunks]:
            answer = self.completion.complete(SYSTEM_PROMPT, chunk, PUBLICATION_SCHEMA) or {}
            citations.extend(filter(None, map(self.citation, answer.get('publications') or [])))
        return citations

    def citation(self, entry: dict) -> HomepageCitation | None:
        """An extracted entry as a citation; identifiers must match their patterns, the title must look like one."""
        title = ' '.join(str(entry.get('title') or '').split()).strip(' .,;:')
        doi = DOI_IN_TEXT.search(str(entry.get('doi') or ''))
        arxiv = canonical_arxiv(str(entry.get('arxiv_id') or ''))
        titles = (title,) if title_like(title, self.anchor_config) else ()
        if not (titles or doi or arxiv):
            return None
        authors = ', '.join(str(name) for name in entry.get('authors') or [])
        venue, year = str(entry.get('venue') or ''), str(entry.get('year') or '')
        text = '. '.join(part for part in (authors, title, venue, year) if part)
        return HomepageCitation(
            text,
            (doi.group(0).rstrip('.,;'),) if doi else (),
            (arxiv,) if arxiv else (),
            titles,
        )


class LlmHtmlParser:
    def __init__(self, extractor: LlmPublicationExtractor, anchor_config: AnchorConfig = DEFAULT_ANCHORS) -> None:
        self.extractor, self.anchor_config = extractor, anchor_config

    def parse(self, html: str) -> list[HomepageCitation]:
        return self.extractor.extract(html_text(html, self.anchor_config))


class LlmPdfParser:
    def __init__(self, extractor: LlmPublicationExtractor, text: Callable[[bytes], str] = pdf_text) -> None:
        self.extractor, self.text = extractor, text

    def parse(self, document: bytes) -> list[HomepageCitation]:
        return self.extractor.extract(self.text(document))


def citation_key(citation: HomepageCitation) -> frozenset[str]:
    return frozenset(work_keys(citation.dois, citation.arxiv_ids, citation.titles))


class MergedCitationParser:
    """Union of several parsers' citations in parser order; a citation sharing a work key with an earlier one is
    dropped. A failing parser is skipped, so the rule-based parser alone still answers."""

    def __init__(self, parsers: Sequence[DocumentParser]) -> None:
        self.parsers = list(parsers)

    def parse(self, document: Any) -> list[HomepageCitation]:
        seen: set[str] = set()
        merged: list[HomepageCitation] = []
        for parser in self.parsers:
            try:
                found = parser.parse(document)
            except ValueError as exc:
                log_isolated_failure(logger, 'Citation parser %s failed: %s', type(parser).__name__, exc)
                continue
            for citation in found:
                keys = citation_key(citation)
                if keys and keys & seen:
                    continue
                seen |= keys
                merged.append(citation)
        return merged
