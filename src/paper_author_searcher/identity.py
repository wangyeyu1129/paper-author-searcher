"""Cross-source identity evidence, separate from query ranking."""

from dataclasses import dataclass, field
from typing import List, Set, Tuple

from .config import DEFAULT_MATCHING, MatchingConfig
from .matching import (
    best_similarity,
    canonical_doi,
    canonical_orcid,
    institution_similarity,
    name_similarity,
    name_similarity_upper_bound,
    normalize,
)
from .models import AuthorCandidate


@dataclass
class IdentityLink:
    matched: bool = False
    shared_orcid: bool = False
    shared_dois: int = 0
    evidence: List[str] = field(default_factory=list)
    reason: str = 'Insufficient shared identity evidence'
    split_profile: bool = False
    conflict: bool = False


def compare_identity(
    left: AuthorCandidate, right: AuthorCandidate, config: MatchingConfig = DEFAULT_MATCHING
) -> IdentityLink:
    if left.source == right.source:
        if left.author_id == right.author_id:
            return IdentityLink(matched=True, evidence=['same source ID'])
        # Distinct IDs from one source stay separate unless ORCID agrees or the split-profile rule holds.
        if not left.orcid or not right.orcid:
            return split_profile_link(left, right, config)

    names = best_similarity(
        [left.name, *left.aliases],
        [right.name, *right.aliases],
        lambda a, b: name_similarity(a, b, config),
    )
    if names < config.compatible_name:
        return IdentityLink(reason='Names are incompatible', conflict=True)

    if left.orcid and right.orcid:
        if canonical_orcid(left.orcid) != canonical_orcid(right.orcid):
            return IdentityLink(reason='Conflicting ORCID identifiers', conflict=True)
        return IdentityLink(
            matched=True,
            shared_orcid=True,
            evidence=['shared ORCID'],
        )

    left_dois = set(map(canonical_doi, left.paper_ids))
    right_dois = set(map(canonical_doi, right.paper_ids))
    shared_dois = len(left_dois & right_dois)
    shared_coauthors = set(map(normalize, left.coauthors)) & set(map(normalize, right.coauthors))
    author_names = [left.name, right.name, *left.aliases, *right.aliases]
    shared_coauthors.difference_update(map(normalize, author_names))

    sufficient_dois = shared_dois >= config.minimum_shared_dois
    doi_with_coauthors = shared_dois > 0 and len(shared_coauthors) >= config.coauthors_with_doi
    if sufficient_dois or doi_with_coauthors:
        return IdentityLink(
            matched=True,
            shared_dois=shared_dois,
            evidence=[
                f'shared DOI count: {shared_dois}',
                f'shared coauthor count: {len(shared_coauthors)}',
            ],
        )

    school_similarity = best_similarity(
        left.affiliations + left.current_affiliations,
        right.affiliations + right.current_affiliations,
        lambda a, b: institution_similarity(a, b, config),
    )
    shared_topics = set(map(normalize, left.topics)) & set(map(normalize, right.topics))
    contextual_match = (
        school_similarity >= config.shared_school
        and len(shared_coauthors) >= config.coauthors_with_school
        and len(shared_topics) >= config.topics_with_school
    )
    if contextual_match:
        return IdentityLink(
            matched=True,
            shared_dois=shared_dois,
            evidence=['shared school, coauthors and research topics'],
        )
    return IdentityLink()


def _shared_context(
    left: AuthorCandidate, right: AuthorCandidate, config: MatchingConfig
) -> Tuple[Set[str], Set[str], float]:
    shared_coauthors = set(map(normalize, left.coauthors)) & set(map(normalize, right.coauthors))
    shared_coauthors.difference_update(map(normalize, [left.name, right.name, *left.aliases, *right.aliases]))
    shared_topics = set(map(normalize, left.topics)) & set(map(normalize, right.topics))
    school_similarity = best_similarity(
        left.affiliations + left.current_affiliations,
        right.affiliations + right.current_affiliations,
        lambda a, b: institution_similarity(a, b, config),
    )
    return shared_coauthors, shared_topics, school_similarity


def split_profile_link(
    left: AuthorCandidate, right: AuthorCandidate, config: MatchingConfig = DEFAULT_MATCHING
) -> IdentityLink:
    """One person indexed twice by the same source: compatible names, a shared school, and both
    collaborators and topics in common. Different namesakes at one school rarely share several coauthors."""
    refusal = IdentityLink(reason='Distinct source IDs without shared ORCID')
    if not config.merge_split_profiles:
        return refusal
    names = best_similarity(
        [left.name, *left.aliases],
        [right.name, *right.aliases],
        lambda a, b: name_similarity(a, b, config),
    )
    if names < config.compatible_name:
        return IdentityLink(reason='Names are incompatible', conflict=True)
    # Cheap set overlaps first; the fuzzy school comparison runs only when they already pass.
    own_names = set(map(normalize, [left.name, right.name, *left.aliases, *right.aliases]))
    left_coauthors = set(map(normalize, left.coauthors)) - own_names
    right_coauthors = set(map(normalize, right.coauthors)) - own_names
    coauthors = left_coauthors & right_coauthors
    topics = set(map(normalize, left.topics)) & set(map(normalize, right.topics))
    if len(coauthors) < config.split_profile_coauthors or len(topics) < config.split_profile_topics:
        return refusal
    overlap = len(coauthors) / min(len(left_coauthors), len(right_coauthors))
    if overlap < config.split_profile_coauthor_overlap:
        return refusal
    _, _, school = _shared_context(left, right, config)
    if school >= config.shared_school:
        return IdentityLink(
            matched=True,
            split_profile=True,
            evidence=[
                f'same-source split profile: shared school, {len(coauthors)} shared coauthors '
                f'(overlap {overlap:.2f}), '
                f'{len(topics)} shared topics'
            ],
        )
    return refusal


def link_evidence(
    left: AuthorCandidate, right: AuthorCandidate, config: MatchingConfig = DEFAULT_MATCHING
) -> List[str]:
    return compare_identity(left, right, config).evidence


def names_may_match(left: AuthorCandidate, right: AuthorCandidate, config: MatchingConfig = DEFAULT_MATCHING) -> bool:
    """False only when ``compare_identity`` is certain to find the names incompatible (exact, never a guess)."""
    if left.source == right.source and left.author_id == right.author_id:
        return True
    left_names = [left.name, *left.aliases]
    right_names = [right.name, *right.aliases]
    return any(
        name_similarity_upper_bound(a, b, config) >= config.compatible_name for a in left_names for b in right_names
    )


def linked(left: AuthorCandidate, right: AuthorCandidate, config: MatchingConfig = DEFAULT_MATCHING) -> bool:
    return compare_identity(left, right, config).matched
