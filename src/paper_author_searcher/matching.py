"""Pure comparison helpers shared by the resolver and source adapters."""

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Callable, Iterable

from .config import DEFAULT_MATCHING


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


def _compatible_middle_names(left, right):
    if not left or not right:
        return True
    if len(left) != len(right):
        return False
    for left_name, right_name in zip(left, right):
        if left_name == right_name:
            continue
        same_initial = left_name[0] == right_name[0]
        contains_initial = len(left_name) == 1 or len(right_name) == 1
        if not same_initial or not contains_initial:
            return False
    return True


@lru_cache(maxsize=200_000)
def name_similarity(left: str, right: str, config=DEFAULT_MATCHING) -> float:
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
def institution_similarity(left: str, right: str, config=DEFAULT_MATCHING) -> float:
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


def canonical_orcid(value):
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
