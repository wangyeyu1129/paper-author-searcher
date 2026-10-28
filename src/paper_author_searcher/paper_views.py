"""Compact views of collected papers for downstream artifacts.

``papers.json`` is the single store of full paper records (source records, raw API metadata, author details). The
PDF report and the download manifest keep only the bibliographic identity they need for matching and validation,
plus ``entry_index``, the paper's position in that collection, so the full record is always one lookup away.
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .json_types import JSONObject

# Fields that identify a publication: what truth matching, PDF identity checks and filenames read.
BIBLIOGRAPHIC_FIELDS = (
    'title',
    'year',
    'authors',
    'doi',
    'dois',
    'venue',
    'external_ids',
    'source',
    'source_paper_id',
    'source_records',
    'source_identifiers',
    'publication_type',
    'paper_type',
    # Other editions deduplication tied to this paper (the arXiv preprint of a venue paper): a PDF may be one.
    'related_versions',
)
# Paper-level copies a download history entry need not repeat: they are on the current record already.
HISTORY_EXCLUDED_FIELDS = frozenset(
    {
        'history',
        'paper_metadata',
        'ownership_evidence',
        'authors',
        'external_ids',
        'title',
        'year',
        'doi',
        'source',
        'source_paper_id',
        'source_author_id',
        'resolver_error_details',
    }
)
MAX_HISTORY = 10


def _identity(values: Mapping[str, Any]) -> JSONObject:
    return {name: deepcopy(values[name]) for name in BIBLIOGRAPHIC_FIELDS if name in values}


def bibliographic_view(paper: Mapping[str, Any]) -> JSONObject:
    """The paper's identity fields and, per source record, the same fields (no raw payloads or author details)."""
    view = _identity(paper)
    records = paper.get('records')
    if isinstance(records, list):
        view['records'] = [_identity(record) for record in records if isinstance(record, Mapping)]
    return view


def history_entry(record: Mapping[str, Any]) -> JSONObject:
    """What a previous download attempt established, without repeating the paper's own metadata."""
    return {name: deepcopy(value) for name, value in record.items() if name not in HISTORY_EXCLUDED_FIELDS}


def extend_history(previous: Mapping[str, Any]) -> list[JSONObject]:
    """History for a new record: the previous record's history plus the previous record, newest last, bounded."""
    history = [dict(entry) for entry in previous.get('history') or []]
    history.append(history_entry(previous))
    return history[-MAX_HISTORY:]


def full_paper_metadata(entry: Mapping[str, Any], collection_papers: Sequence[Mapping[str, Any]]) -> JSONObject | None:
    """The full collected record behind a PDF-report or manifest entry, or ``None`` if it no longer matches."""
    index = entry.get('entry_index')
    if not isinstance(index, int) or not 0 <= index < len(collection_papers):
        return None
    paper = collection_papers[index]
    if (paper.get('title') or '') != (entry.get('title') or '') or paper.get('doi') != entry.get('doi'):
        return None
    return dict(paper)


def collection_papers(papers_artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    collection = papers_artifact.get('collection', papers_artifact)
    return list(collection.get('all_papers', collection.get('papers')) or [])
