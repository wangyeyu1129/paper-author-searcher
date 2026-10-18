"""Collect candidates, group verified identities, and rank against the query."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import combinations
from typing import List

from .config import DEFAULT_MATCHING
from .identity import IdentityLink, compare_identity, link_evidence, linked
from .identity_adjudication import accepted
from .identity_anchors import IdentityAnchors, ScholarlyAnchorFinder
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
    anchors: List[str]
    orcid_conflict: bool


class ProfessorResolver:
    def __init__(self, providers=None, config=DEFAULT_MATCHING, anchor_finder=None, adjudicator=None):
        self.config = config
        if providers is None:
            providers = [
                OpenAlexProvider(matching_config=config),
                SemanticScholarProvider(),
                DBLPProvider(),
            ]
        self.providers = providers
        # Anchors only cost requests when the query carries hints (ORCID, homepage, known papers).
        self.anchor_finder = anchor_finder or ScholarlyAnchorFinder(matching_config=config)
        # Off unless injected; see identity_adjudication.ClaudeIdentityAdjudicator.
        self.adjudicator = adjudicator

    def rank(self, query):
        """Scored identity profiles, the candidate groups behind them, and per-source errors."""
        records, errors = self._collect_candidates(query)
        anchors = self.anchor_finder.find(query) if query.has_hints() else IdentityAnchors()
        if anchors.errors:
            errors["anchors"] = "; ".join(anchors.errors)
        records.extend(self._anchored_records(anchors, records, errors))
        groups = self._merge_anchored(self._group_candidates(records), anchors)
        scored = sorted(((self._score(query, group, anchors), group) for group in groups),
                        key=lambda item: self._ranking_key(item[0]))
        scored = self._adjudicate(query, scored, anchors)
        return [profile for profile, _ in scored], [group for _, group in scored], errors

    def _adjudicate(self, query, scored, anchors):
        """Ask the adjudicator about a close, unanchored contest between the top two profiles."""
        if self.adjudicator is None or len(scored) < 2:
            return scored
        (first, first_group), (second, second_group) = scored[0], scored[1]
        if first.match_score - second.match_score >= self.config.ambiguity_margin:
            return scored
        if first.identity_anchors or second.identity_anchors:
            return scored
        verdict = self.adjudicator.judge(query, first_group, second_group)
        if not accepted(verdict, self.config.adjudication_minimum_confidence):
            return scored
        note = "LLM adjudication ({}, confidence {:.2f}): {}".format(verdict.model, verdict.confidence,
                                                                     verdict.rationale)
        if verdict.same_person == "yes" and verdict.target in ("first", "second", "both"):
            merged = first_group + second_group
            profile = self._score(query, merged, anchors)
            profile.adjudication = note
            profile.evidence.append(note)
            rest = sorted(scored[2:], key=lambda item: self._ranking_key(item[0]))
            return [(profile, merged)] + rest
        if verdict.same_person == "no" and verdict.target in ("first", "second"):
            chosen = 0 if verdict.target == "first" else 1
            profile, group = scored[chosen]
            profile.adjudication = note
            profile.evidence.append(note)
            return [scored[chosen], scored[1 - chosen]] + scored[2:]
        return scored

    def _anchored_records(self, anchors, records, errors):
        """Fetch anchored profiles that the name search did not return."""
        present = {(record.source, record.author_id) for record in records}
        added = []
        for provider in self.providers:
            lookup = getattr(provider, "lookup", None)
            for author_id in sorted(anchors.author_ids.get(provider.name, ())):
                if lookup is None or (provider.name, author_id) in present:
                    continue
                try:
                    added.append(lookup(author_id))
                except Exception as exc:
                    errors.setdefault(provider.name, str(exc))
        return added

    @staticmethod
    def _is_anchored(record, anchors):
        if anchors.contains(record.source, record.author_id):
            return True
        return bool(anchors.orcid and record.orcid and canonical_orcid(record.orcid) == anchors.orcid)

    def _merge_anchored(self, groups, anchors):
        """Every group holding an anchored profile belongs to the requested person."""
        if not anchors:
            return groups
        anchored = [group for group in groups if any(self._is_anchored(record, anchors) for record in group)]
        if len(anchored) < 2:
            return groups
        merged = [record for group in anchored for record in group]
        return [merged] + [group for group in groups if group not in anchored]

    def resolve(self, query):
        profiles, groups, errors = self.rank(query)
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
        # Complete-link prevents incompatible records merging through a bridge. Split-profile fragments of one
        # person are often only chained (A~B, A~C), so one split link suffices when no member conflicts.
        links = [compare_identity(candidate, member, self.config) for member in group]
        if all(link.matched for link in links):
            return True
        return any(link.split_profile for link in links) and not any(link.conflict for link in links)

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
        if best.identity_anchors and not profiles[1].identity_anchors:
            # Independent anchors outweigh a close name/school score.
            return
        if best.adjudication and not profiles[1].adjudication:
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

    def _extract_signals(self, query, group, anchors=None):
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
        anchors = anchors or IdentityAnchors()
        anchor_evidence = []
        for record in group:
            if self._is_anchored(record, anchors):
                anchor_evidence.extend(line for line in anchors.evidence
                                       if line.startswith("{} {}:".format(record.source, record.author_id)))
                if anchors.orcid and record.orcid and canonical_orcid(record.orcid) == anchors.orcid:
                    anchor_evidence.append("{} {}: ORCID matches the supplied ORCID".format(
                        record.source, record.author_id))
        orcid_conflict = bool(anchors.orcid and any(
            record.orcid and canonical_orcid(record.orcid) != anchors.orcid for record in group))
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
            anchors=sorted(set(anchor_evidence)),
            orcid_conflict=orcid_conflict,
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
        if signals.anchors:
            score += config.anchor_bonus
        if signals.orcid_conflict:
            score -= config.orcid_conflict_penalty
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
        evidence.extend("Identity anchor: " + line for line in signals.anchors)
        if signals.orcid_conflict:
            evidence.append("ORCID conflicts with the supplied ORCID")
        if query.department:
            evidence.append("Department similarity: {:.3f}".format(signals.department))
        if query.research_interests:
            evidence.append("Research similarity: {:.3f}".format(signals.research))
        sources = sorted({candidate.source for candidate in group})
        evidence.append("Sources: " + ", ".join(sources))
        return evidence

    def _score(self, query, group, anchors=None):
        signals = self._extract_signals(query, group, anchors)
        match_score = self._match_score(signals)
        confidence = match_score
        anchored = bool(signals.anchors) and not signals.orcid_conflict
        # A caller-supplied anchor also covers people whose indexed affiliation is stale or missing.
        school_ok = signals.university_status == "matched" or anchored
        if signals.name < self.config.compatible_name or not school_ok:
            confidence = min(confidence, self.config.review_cap)
        status = "needs_review"
        university = None
        if signals.university_status == "matched":
            university = query.university
        if school_ok and confidence >= self.config.resolved_confidence:
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
            identity_anchors=signals.anchors,
        )
