"""What differs between sources' identifiers, in one table, so adding a source touches no matching code."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceTraits:
    # IDs may arrive as a URL whose last path segment is the ID (https://openalex.org/A123).
    url_shaped_ids: bool = False
    # Paper IDs compare case-insensitively.
    case_insensitive_paper_ids: bool = False
    # Paper IDs name the same work across every record of this source, so they prove two records are duplicates.
    shared_paper_namespace: str | None = None


OPENALEX = 'openalex'
SEMANTIC_SCHOLAR = 'semantic_scholar'
DBLP = 'dblp'
ARXIV = 'arxiv'

SOURCE_TRAITS: dict[str, SourceTraits] = {
    OPENALEX: SourceTraits(url_shaped_ids=True, case_insensitive_paper_ids=True),
    DBLP: SourceTraits(shared_paper_namespace=DBLP),
}
NO_TRAITS = SourceTraits()


def traits_of(source: str) -> SourceTraits:
    return SOURCE_TRAITS.get(source, NO_TRAITS)


def bare_id(source: str, value: str) -> str:
    """The ID without the URL some sources wrap it in."""
    return value.rstrip('/').rsplit('/', 1)[-1] if traits_of(source).url_shaped_ids else value
