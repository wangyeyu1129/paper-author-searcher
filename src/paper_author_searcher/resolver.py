"""Collect candidates, group verified identities, and rank against the query."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import combinations
from typing import List

from .config import DEFAULT_MATCHING
from .identity import IdentityLink, compare_identity, link_evidence, linked
from .matching import (
    best_similarity, canonical_orcid, institution_similarity,
    name_similarity, normalize, research_similarity,
)
from .models import ProfessorProfile, ResolutionResult
from .providers import OpenAlexProvider, SemanticScholarProvider, DBLPProvider


@dataclass
class ProfileSignals:
    name: float
    school: float
    current_school: float
    department: float
    research: float
    university_status: str
    affiliations: List[str]
    topics: List[str]
    links: List[IdentityLink]


class ProfessorResolver:
    def __init__(self, providers=None, config=DEFAULT_MATCHING):
        self.config = config
        if providers is None:
            providers = [
                OpenAlexProvider(matching_config=config),
                SemanticScholarProvider(),
                DBLPProvider(),
            ]
        self.providers = providers

    def resolve(self, query):
        records, errors = self._collect_candidates(query)
        groups = self._group_candidates(records)
        profiles = []
        for group in groups:
            profiles.append(self._score(query, group))
        profiles.sort(key=self._ranking_key)
        if not profiles:
            return ResolutionResult(profile=None, candidates=[], source_errors=errors)
        ambiguity = self._mark_ambiguity(profiles, groups)
        return ResolutionResult(
            profile=profiles[0], candidates=profiles, source_errors=errors,
            ambiguity=ambiguity,
        )

    def _collect_candidates(self, query):
        records = []
        errors = {}
        with ThreadPoolExecutor(max_workers=max(1, len(self.providers))) as pool:
            futures = {}
            for provider in self.providers:
                future = pool.submit(provider.search, query)
                futures[future] = provider.name
            for future in as_completed(futures):
                source = futures[future]
                try:
                    candidates = future.result()
                    records.extend(candidates)
                    warnings = set()
                    for candidate in candidates:
                        warnings.update(candidate.metadata_errors)
                    if warnings:
                        errors[source] = "; ".join(sorted(warnings))
                except Exception as exc:
                    # Isolate source failures so available evidence is preserved.
                    errors[source] = str(exc)
        records.sort(key=lambda candidate: (candidate.source, candidate.author_id))
        return records, errors

    def _group_candidates(self, records):
        groups = []
        for candidate in records:
            matching_group = None
            for group in groups:
                if self._can_join(candidate, group):
                    matching_group = group
                    break
            if matching_group is None:
                groups.append([candidate])
            else:
                matching_group.append(candidate)
        return groups

    def _can_join(self, candidate, group):
        # Complete-link prevents incompatible records merging through a bridge.
        for member in group:
            if not linked(candidate, member, self.config):
                return False
        return True

    @staticmethod
    def _ranking_key(profile):
        return (-profile.match_score, profile.name, sorted(profile.author_ids.items()))

    def _mark_ambiguity(self, profiles, groups):
        if len(profiles) < 2:
            return
        best = profiles[0]
        margin = best.match_score - profiles[1].match_score
        if margin >= self.config.ambiguity_margin:
            return
        best.identity_confidence = round(
            min(best.identity_confidence, self.config.review_cap),
            self.config.confidence_precision,
        )
        best.status = "needs_review"
        if best.university_status == "matched":
            best.status = "ambiguous"
        best.evidence.append("Competing identity has a similar score; manual review required")
        reasons = set()
        first_records = self._profile_records(best, groups)
        second_records = self._profile_records(profiles[1], groups)
        for left in first_records:
            for right in second_records:
                comparison = compare_identity(left, right, self.config)
                if not comparison.matched:
                    reasons.add(comparison.reason)
        contenders = []
        for profile in profiles[:2]:
            contenders.append({
                "name": profile.name,
                "author_ids": profile.author_id_aliases,
                "match_score": profile.match_score,
                "university_status": profile.university_status,
                "matched_affiliations": profile.matched_affiliations,
            })
        return {
            "score_margin": round(margin, self.config.confidence_precision),
            "required_margin": self.config.ambiguity_margin,
            "reasons": sorted(reasons),
            "contenders": contenders,
        }

    @staticmethod
    def _profile_records(profile, groups):
        records = []
        for group in groups:
            for record in group:
                ids = profile.author_id_aliases.get(record.source, [])
                if record.author_id in ids:
                    records.append(record)
        return records

    def _extract_signals(self, query, group):
        names = []
        affiliations = set()
        current_affiliations = set()
        topics = set()
        departments = []
        for candidate in group:
            names.extend([candidate.name] + candidate.aliases)
            affiliations.update(candidate.affiliations)
            affiliations.update(candidate.current_affiliations)
            current_affiliations.update(candidate.current_affiliations)
            topics.update(candidate.topics)
            departments.extend(candidate.departments)
        departments.extend(sorted(affiliations))
        universities = [query.university] + query.university_aliases
        school_compare = lambda a, b: institution_similarity(a, b, self.config)
        school = best_similarity(universities, sorted(affiliations), school_compare)
        department = 0.0
        if query.department:
            department = best_similarity([query.department], departments, school_compare)
        links = []
        for left, right in combinations(group, 2):
            if left.source != right.source:
                links.append(compare_identity(left, right, self.config))
        return ProfileSignals(
            name=best_similarity(
                [query.name], names, lambda a, b: name_similarity(a, b, self.config),
            ),
            school=school,
            current_school=best_similarity(
                universities, sorted(current_affiliations), school_compare,
            ),
            department=department,
            research=best_similarity(query.research_interests, sorted(topics), research_similarity),
            university_status=self._university_status(affiliations, school),
            affiliations=sorted(affiliations),
            topics=sorted(topics),
            links=links,
        )

    def _university_status(self, affiliations, similarity):
        if not affiliations:
            return "unknown"
        if similarity >= self.config.matched_school:
            return "matched"
        return "mismatch"

    def _link_bonus(self, links):
        verified_links = []
        for link in links:
            if link.matched:
                verified_links.append(link)
        if not verified_links:
            return 0.0
        if any(link.shared_orcid for link in verified_links):
            return self.config.orcid_bonus
        shared_dois = max(link.shared_dois for link in verified_links)
        if shared_dois >= self.config.minimum_shared_dois:
            coverage = min(shared_dois / self.config.doi_coverage_target, 1.0)
            return self.config.doi_base_bonus + self.config.doi_coverage_bonus * coverage
        if shared_dois == 0:
            return self.config.contextual_link_bonus
        # One paper supports grouping but cannot elevate a fragment's rank.
        return 0.0

    def _match_score(self, signals):
        config = self.config
        score = config.name_weight * signals.name
        if signals.university_status == "matched":
            score += config.school_weight * signals.school
            score += config.current_school_weight * signals.current_school
        elif signals.university_status == "mismatch":
            score -= config.school_mismatch_penalty
        # Missing school information is neutral, not a mismatch.
        score += self._link_bonus(signals.links)
        score += config.department_weight * signals.department
        score += config.research_weight * signals.research
        return round(max(0, min(score, config.confidence_cap)), config.confidence_precision)

    def _evidence(self, query, group, signals):
        evidence = ["Name similarity: {:.3f}".format(signals.name)]
        if signals.university_status == "unknown":
            evidence.append("University information missing (neutral); identity unverified")
        elif signals.university_status == "mismatch":
            evidence.append("Observed universities do not match requested university")
        else:
            evidence.append("University matched: {:.3f}".format(signals.school))
            if signals.current_school < self.config.matched_school:
                evidence.append("Affiliation is historical or its recency is unknown")
        link_messages = set()
        for link in signals.links:
            link_messages.update(link.evidence)
        evidence.extend(sorted(link_messages))
        if query.department:
            evidence.append("Department similarity: {:.3f}".format(signals.department))
        if query.research_interests:
            evidence.append("Research similarity: {:.3f}".format(signals.research))
        sources = sorted({candidate.source for candidate in group})
        evidence.append("Sources: " + ", ".join(sources))
        return evidence

    def _score(self, query, group):
        signals = self._extract_signals(query, group)
        match_score = self._match_score(signals)
        confidence = match_score
        if signals.name < self.config.compatible_name or signals.university_status != "matched":
            confidence = min(confidence, self.config.review_cap)
        status = "needs_review"
        university = None
        if signals.university_status == "matched":
            university = query.university
            if confidence >= self.config.resolved_confidence:
                status = "resolved"
        author_ids = {}
        author_id_aliases = {}
        matched_affiliations = []
        universities = [query.university] + query.university_aliases
        for affiliation in signals.affiliations:
            similarity = best_similarity(
                universities, [affiliation],
                lambda a, b: institution_similarity(a, b, self.config),
            )
            if similarity >= self.config.matched_school:
                matched_affiliations.append(affiliation)
        if matched_affiliations:
            university = matched_affiliations[0]
        orcid = None
        for candidate in group:
            author_ids.setdefault(candidate.source, candidate.author_id)
            author_id_aliases.setdefault(candidate.source, []).append(candidate.author_id)
            if orcid is None and candidate.orcid:
                orcid = canonical_orcid(candidate.orcid)
        return ProfessorProfile(
            name=group[0].name,
            affiliations=signals.affiliations,
            author_ids=author_ids,
            identity_confidence=confidence,
            status=status,
            evidence=self._evidence(query, group, signals),
            orcid=orcid,
            research_interests=signals.topics,
            university=university,
            university_status=signals.university_status,
            match_score=match_score,
            requested_university=query.university,
            matched_affiliations=matched_affiliations,
            author_id_aliases=author_id_aliases,
            name_aliases=sorted({candidate.name for candidate in group}),
        )
