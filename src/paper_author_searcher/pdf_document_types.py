"""Classify document form independently of its association with a paper."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .config import DEFAULT_DOWNLOAD, DownloadConfig
from .pdf_front_matter import front_matter

NON_FULL_DOCUMENTS = {'poster', 'slides', 'supplementary_material'}


def classify_document(
    metadata: Mapping[str, Any],
    first_page_text: str,
    page_count: int,
    page_size: Sequence[float],
    version_type: str | None = None,
    config: DownloadConfig = DEFAULT_DOWNLOAD,
) -> tuple[str, list[str]]:
    title = str(metadata.get('/Title') or '')
    subject = str(metadata.get('/Subject') or '')
    creator = str(metadata.get('/Creator') or '')
    heading = front_matter('\n'.join(first_page_text.splitlines()[: config.title_lines]))
    explicit = title + '\n' + subject + '\n' + heading
    if re.search(
        r'(?im)^\s*(?:supplementary (?:material|information)|supporting information|supplement to)\b', explicit
    ):
        return 'supplementary_material', ['Explicit supplementary/supporting-material heading in the PDF']
    oversized = max(page_size) >= config.poster_minimum_edge_points
    if page_count == 1 and oversized and re.search(r'(?i)\bposter\b|[_-]poster', title + ' ' + subject):
        return 'poster', ['Poster metadata and oversized single-page layout agree']
    if re.search(r'(?im)^\s*poster\s*[:\-]', explicit):
        return 'poster', ['Explicit poster heading in document metadata/front matter']
    presentation = bool(re.search(r'(?i)powerpoint|beamer|impress|\.pptx?\b', title + ' ' + creator))
    landscape = page_size[0] / max(page_size[1], 1) >= config.slide_minimum_aspect_ratio
    if presentation and (page_count > 1 or landscape):
        return 'slides', ['Presentation-producing metadata and slide layout/page sequence agree']
    if re.search(r'(?im)^\s*(?:presentation slides|slides for|slide deck)\b', explicit):
        return 'slides', ['Explicit slide-deck heading']
    article = bool(re.search(r'(?im)^\s*abstract\b', first_page_text))
    body_section = bool(re.search(r'(?im)^\s*(?:\d+\.?\s+)?introduction\b', first_page_text))
    article_structure = article and not oversized and not landscape and (page_count > 1 or body_section)
    if article_structure and version_type == 'preprint':
        return 'preprint', ['Article front matter and independently supplied preprint version metadata agree']
    if article_structure:
        return 'full_text', [
            'Article front matter and document structure agree; publication version retained separately'
        ]
    return 'unknown', ['Document form lacks sufficient explicit evidence; page count/domain alone is not proof']
