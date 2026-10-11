"""Cross-source identity evidence, separate from query ranking."""
from dataclasses import dataclass, field
from typing import List

from .config import DEFAULT_MATCHING
from .matching import (
    best_similarity, canonical_doi, canonical_orcid,
    institution_similarity, name_similarity, normalize,
)


@dataclass
class IdentityLink:
    matched: bool = False
    shared_orcid: bool = False
    shared_dois: int = 0
    evidence: List[str] = field(default_factory=list)
    reason: str = "Insufficient shared identity evidence"


def compare_identity(left, right, config=DEFAULT_MATCHING):
    if left.source == right.source:
        if left.author_id == right.author_id:
            return IdentityLink(matched=True, evidence=["same source ID"])
        # Distinct IDs from one source stay separate unless ORCID agrees.
        if not left.orcid or not right.orcid:
            return IdentityLink(reason="Distinct source IDs without shared ORCID")

    names = best_similarity(
        [left.name] + left.aliases,
        [right.name] + right.aliases,
        lambda a, b: name_similarity(a, b, config),
    )
    if names < config.compatible_name:
        return IdentityLink(reason="Names are incompatible")

    if left.orcid and right.orcid:
        if canonical_orcid(left.orcid) != canonical_orcid(right.orcid):
            return IdentityLink(reason="Conflicting ORCID identifiers")
        return IdentityLink(
            matched=True, shared_orcid=True, evidence=["shared ORCID"],
        )

    left_dois = set(map(canonical_doi, left.paper_ids))
    right_dois = set(map(canonical_doi, right.paper_ids))
    shared_dois = len(left_dois & right_dois)
    shared_coauthors = set(map(normalize, left.coauthors)) & set(map(normalize, right.coauthors))
    author_names = [left.name, right.name] + left.aliases + right.aliases
    shared_coauthors.difference_update(map(normalize, author_names))

    sufficient_dois = shared_dois >= config.minimum_shared_dois
    doi_with_coauthors = shared_dois > 0 and len(shared_coauthors) >= config.coauthors_with_doi
    if sufficient_dois or doi_with_coauthors:
        return IdentityLink(
            matched=True,
            shared_dois=shared_dois,
            evidence=[
                "shared DOI count: {}".format(shared_dois),
                "shared coauthor count: {}".format(len(shared_coauthors)),
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
            evidence=["shared school, coauthors and research topics"],
        )
    return IdentityLink()


def link_evidence(left, right, config=DEFAULT_MATCHING):
    return compare_identity(left, right, config).evidence


def linked(left, right, config=DEFAULT_MATCHING):
    return compare_identity(left, right, config).matched
