"""Pure comparison helpers shared by the resolver and source adapters."""

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from .config import DEFAULT_MATCHING, MatchingConfig


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize('NFKD', value).casefold()
    characters = []
    for character in decomposed:
        if not unicodedata.combining(character):
            characters.append(character)
    return ' '.join(re.findall(r'[^\W_]+', ''.join(characters)))


def best_similarity(left: Iterable[str], right: Iterable[str], compare: Callable) -> float:
    right_values = list(right)
    best = 0.0
    for left_value in left:
        for right_value in right_values:
            best = max(best, compare(left_value, right_value))
    return best


def _compatible_middle_names(left: Sequence[str], right: Sequence[str]) -> bool:
    if not left or not right:
        return True
    if len(left) != len(right):
        return False
    for left_name, right_name in zip(left, right, strict=True):
        if left_name == right_name:
            continue
        same_initial = left_name[0] == right_name[0]
        contains_initial = len(left_name) == 1 or len(right_name) == 1
        if not same_initial or not contains_initial:
            return False
    return True


@lru_cache(maxsize=200_000)
def name_similarity(left: str, right: str, config: MatchingConfig = DEFAULT_MATCHING) -> float:
    left_words = normalize(left).split()
    right_words = normalize(re.sub(r'\s+\d{4}$', '', right)).split()
    if not left_words or not right_words:
        return 0.0
    if sorted(left_words) == sorted(right_words):
        return 1.0

    has_full_names = len(left_words) >= 2 and len(right_words) >= 2
    if has_full_names and left_words[-1] == right_words[-1]:
        first_left = left_words[0]
        first_right = right_words[0]
        if first_left == first_right:
            if _compatible_middle_names(left_words[1:-1], right_words[1:-1]):
                return config.middle_name_similarity
            # Explicit conflicting middle names must not pass via fuzzy matching.
            return 0.0
        same_initial = first_left[0] == first_right[0]
        contains_initial = len(first_left) == 1 or len(first_right) == 1
        if same_initial and contains_initial:
            return config.initial_similarity

    return SequenceMatcher(None, ' '.join(left_words), ' '.join(right_words)).ratio()


@lru_cache(maxsize=200_000)
def name_similarity_upper_bound(left: str, right: str, config: MatchingConfig = DEFAULT_MATCHING) -> float:
    """Never below ``name_similarity(left, right)``, at linear cost.

    Mirrors ``name_similarity`` rule for rule and replaces only the closing ``SequenceMatcher.ratio()`` (quadratic)
    with ``quick_ratio()``, which difflib guarantees is an upper bound of it. Lets grouping rule out a pair of
    incompatible names without the fuzzy comparison.
    """
    left_words = normalize(left).split()
    right_words = normalize(re.sub(r'\s+\d{4}$', '', right)).split()
    if not left_words or not right_words:
        return 0.0
    if sorted(left_words) == sorted(right_words):
        return 1.0
    has_full_names = len(left_words) >= 2 and len(right_words) >= 2
    if has_full_names and left_words[-1] == right_words[-1]:
        first_left = left_words[0]
        first_right = right_words[0]
        if first_left == first_right:
            if _compatible_middle_names(left_words[1:-1], right_words[1:-1]):
                return config.middle_name_similarity
            return 0.0
        same_initial = first_left[0] == first_right[0]
        contains_initial = len(first_left) == 1 or len(first_right) == 1
        if same_initial and contains_initial:
            return config.initial_similarity
    return SequenceMatcher(None, ' '.join(left_words), ' '.join(right_words)).quick_ratio()


@lru_cache(maxsize=200_000)
def institution_similarity(left: str, right: str, config: MatchingConfig = DEFAULT_MATCHING) -> float:
    left_name = normalize(left)
    right_name = normalize(right)
    if left_name == right_name:
        return 1.0
    left_words = set(left_name.split()) - set(config.school_stop_words)
    right_words = set(right_name.split()) - set(config.school_stop_words)
    if left_words and left_words == right_words:
        return config.reordered_school_similarity
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def research_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def canonical_orcid(value: str | None) -> str | None:
    if value:
        return value.rstrip('/').rsplit('/', 1)[-1]
    return None


def canonical_doi(value: str) -> str:
    value = value.strip().lower()
    prefixes = (
        'https://doi.org/',
        'http://doi.org/',
        'http://dx.doi.org/',
        'https://dx.doi.org/',
        'doi:',
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return 'doi:' + value


# arXiv registers a DataCite DOI for every preprint; it names the arXiv identifier, not a separate publication.
ARXIV_DOI_PREFIX = '10.48550/arxiv.'
ARXIV_IDENTIFIER = re.compile(r'(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?$', re.IGNORECASE)


def canonical_arxiv(value: str) -> str | None:
    """'arXiv:2401.01234v2', 'https://arxiv.org/abs/2401.01234' and '2401.01234' all become '2401.01234'."""
    text = value.strip().lower()
    for prefix in ('https://arxiv.org/abs/', 'http://arxiv.org/abs/', 'https://arxiv.org/pdf/', 'arxiv:'):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    match = ARXIV_IDENTIFIER.match(text.removesuffix('.pdf'))
    return match.group(1) if match else None


def arxiv_from_doi(value: str) -> str | None:
    """The arXiv identifier behind an arXiv DataCite DOI, or None for any other DOI."""
    doi = canonical_doi(value).removeprefix('doi:')
    return canonical_arxiv(doi[len(ARXIV_DOI_PREFIX) :]) if doi.startswith(ARXIV_DOI_PREFIX) else None


def work_keys(dois: Iterable[str] = (), arxiv_ids: Iterable[str] = (), titles: Iterable[str] = ()) -> set[str]:
    """Comparable keys for one work across sources and homepages: canonical DOI, arXiv ID, normalized title."""
    keys = set()
    for doi in dois:
        arxiv = arxiv_from_doi(doi)
        keys.add('arxiv:' + arxiv if arxiv else canonical_doi(doi))
    keys.update('arxiv:' + identifier for identifier in filter(None, map(canonical_arxiv, arxiv_ids)))
    keys.update('title:' + normalize(title) for title in titles if normalize(title))
    return keys


# Process-wide memo caches of the pure similarity functions (bounded LRU). A long-running service reports them
# as metrics and may clear them, e.g. after changing matching configuration.
SIMILARITY_CACHES = {
    'name_similarity': name_similarity,
    'name_similarity_upper_bound': name_similarity_upper_bound,
    'institution_similarity': institution_similarity,
}


def similarity_cache_statistics() -> dict[str, dict[str, Any]]:
    statistics: dict[str, dict[str, Any]] = {}
    for name, function in SIMILARITY_CACHES.items():
        info = function.cache_info()
        lookups = info.hits + info.misses
        statistics[name] = {
            'hits': info.hits,
            'misses': info.misses,
            'entries': info.currsize,
            'capacity': info.maxsize,
            'hit_rate': round(info.hits / lookups, 4) if lookups else None,
        }
    return statistics


def clear_similarity_caches() -> None:
    for function in SIMILARITY_CACHES.values():
        function.cache_clear()
