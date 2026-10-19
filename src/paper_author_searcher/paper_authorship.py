"""Source-scoped author IDs and three-valued authorship observations."""

from typing import Any, List, Mapping, Optional, Sequence


def canonical_author_id(source: str, value: Any) -> str:
    if value is None:
        return ''
    value = str(value).strip()
    if source == 'openalex':
        return value.rstrip('/').rsplit('/', 1)[-1]
    return value


def target_membership(
    source: str, author_id: Any, details: Sequence[Mapping[str, Any]], complete: Optional[bool] = None
) -> Optional[bool]:
    expected = canonical_author_id(source, author_id)
    for author in details:
        if expected and canonical_author_id(source, author.get('author_id')) == expected:
            return True
    if not details or complete is False:
        return None
    if all(author.get('author_id') for author in details):
        return False
    return None


def signature_names(author: Mapping[str, Any]) -> List[str]:
    # Prefer the printed byline to the resolved profile name when both exist.
    raw_name = author.get('raw_author_name')
    if raw_name:
        return byline_forms(raw_name)
    if author.get('name'):
        return byline_forms(author['name'])
    return []


def byline_forms(name: str) -> List[str]:
    forms = [name]
    parts = name.split(',')
    if len(parts) == 2 and all(part.strip() for part in parts):
        # Bibliographic bylines commonly put the family name before a comma.
        forms.append(parts[1].strip() + ' ' + parts[0].strip())
    return forms
