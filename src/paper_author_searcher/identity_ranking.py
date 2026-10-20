"""How scored identity candidates are ordered, when the leader is contested, and when an anchor alone resolves.

A name/school score cannot separate namesakes at one school: Wei Wang's ORCID-anchored profile (0.725) lost to an
unanchored "Chia-Wei Wang" (0.744), and the anchor bonus cannot be tuned to fix every such case. Anchor kinds named
in ``MatchingConfig.precedence_anchor_kinds`` therefore act as a rule, not a weight.
"""

from collections.abc import Iterable

from .config import DEFAULT_MATCHING, MatchingConfig
from .models import ProfessorProfile


class IdentityRanking:
    def __init__(self, config: MatchingConfig = DEFAULT_MATCHING) -> None:
        self.config = config

    def has_precedence(self, profile: ProfessorProfile) -> bool:
        """Holds an anchor of a precedence kind (the supplied ORCID by default) and no conflicting ORCID."""
        if profile.orcid_conflict:
            return False
        return any(anchor.kind in self.config.precedence_anchor_kinds for anchor in profile.identity_anchors)

    def key(self, profile: ProfessorProfile) -> tuple:
        return (
            not self.has_precedence(profile),
            -profile.match_score,
            profile.name,
            sorted(profile.author_ids.items()),
        )

    def order(self, profiles: Iterable[ProfessorProfile]) -> list[ProfessorProfile]:
        return sorted(profiles, key=self.key)

    def uncontested(self, best: ProfessorProfile, runner_up: ProfessorProfile) -> bool:
        """The leader needs no review against the runner-up."""
        if best.match_score - runner_up.match_score >= self.config.ambiguity_margin:
            return True
        if self.has_precedence(best) and not self.has_precedence(runner_up):
            return True
        # Independent anchors and an accepted adjudication outweigh a close name/school score.
        if best.identity_anchors and not runner_up.identity_anchors:
            return True
        return bool(best.adjudication and not runner_up.adjudication)

    def resolves(self, profile: ProfessorProfile, name_similarity: float) -> bool:
        """A precedence anchor with a compatible name confirms the identity whatever the school score says."""
        return self.has_precedence(profile) and name_similarity >= self.config.compatible_name
