"""Source-scoped author IDs and three-valued authorship observations."""

from collections.abc import Mapping, Sequence
from typing import Any

from .source_traits import bare_id

# "Family, Given" splits into this many parts on its comma.
FAMILY_COMMA_GIVEN_PARTS = 2


def canonical_author_id(source: str, value: Any) -> str:
    if value is None:
        return ''
    value = str(value).strip()
    return bare_id(source, value)


def target_membership(
    source: str, author_id: Any, details: Sequence[Mapping[str, Any]], complete: bool | None = None
) -> bool | None:
    expected = canonical_author_id(source, author_id)
    for author in details:
        if expected and canonical_author_id(source, author.get('author_id')) == expected:
            return True
    if not details or complete is False:
        return None
    if all(author.get('author_id') for author in details):
        return False
    return None


def signature_names(author: Mapping[str, Any]) -> list[str]:
    # Prefer the printed byline to the resolved profile name when both exist.
    raw_name = author.get('raw_author_name')
    if raw_name:
        return byline_forms(raw_name)
    if author.get('name'):
        return byline_forms(author['name'])
    return []


def byline_forms(name: str) -> list[str]:
    forms = [name]
    parts = name.split(',')
    if len(parts) == FAMILY_COMMA_GIVEN_PARTS and all(part.strip() for part in parts):
        # Bibliographic bylines commonly put the family name before a comma.
        forms.append(parts[1].strip() + ' ' + parts[0].strip())
    return forms
