"""Conservative, source-scoped ownership decisions with traceable evidence."""

from collections import Counter

from .config import DEFAULT_OWNERSHIP
from .matching import canonical_orcid, institution_similarity, name_similarity, normalize
from .models import AnchorEvidence
from .paper_authorship import byline_forms, canonical_author_id, signature_names
from .paper_deduplication import author_signatures_match, duplicate_evidence
from .status import OwnershipStatus


class PaperOwnershipValidator:
    def __init__(self, config=DEFAULT_OWNERSHIP):
        self.config = config

    @staticmethod
    def _selected_ids(profile, source):
        values = list(profile.author_id_aliases.get(source) or [])
        if profile.author_ids.get(source):
            values.append(profile.author_ids[source])
        return {canonical_author_id(source, value) for value in values}

    @staticmethod
    def _anchored_ids(profile, source):
        anchors = (AnchorEvidence.coerce(item) for item in profile.identity_anchors)
        return {canonical_author_id(source, anchor.author_id) for anchor in anchors if anchor.source == source}

    def _corroborating_ids(self, profile, source):
        """IDs whose source attribution may corroborate another database's attribution.

        With caller anchors, a split profile merged in without an anchor of its own (even the representative)
        rests on resolver similarity alone, so two databases agreeing on it is not independent evidence.
        """
        selected = self._selected_ids(profile, source)
        if not profile.identity_anchors:
            return selected
        return selected & self._anchored_ids(profile, source)

    def _target_details(self, record, profile=None, expected=None):
        if expected is None:
            expected = {canonical_author_id(record.source, record.source_author_id)}
            if profile is not None:
                expected = self._selected_ids(profile, record.source)
        details = []
        for author in record.author_details:
            actual = canonical_author_id(record.source, author.get('author_id'))
            if actual and actual in expected:
                details.append(author)
        return details

    def _signature_score(self, details, record, profile):
        signatures = []
        for author in details:
            signatures.extend(signature_names(author))
        if not details:
            for author in record.authors:
                signatures.extend(byline_forms(author))
        best = 0.0
        for signature in signatures:
            for name in [profile.name, *profile.name_aliases]:
                best = max(best, name_similarity(name, signature))
        return best, signatures

    def _observed_orcids(self, details, paper_level=False):
        values = set()
        for author in details:
            if paper_level and author.get('orcid_scope') in self.config.author_level_orcid_scopes:
                continue
            orcids = list(author.get('orcids') or [])
            if author.get('orcid'):
                orcids.append(author['orcid'])
            for orcid in orcids:
                values.add(canonical_orcid(orcid))
        return values

    def _nonstandard_reasons(self, record):
        reasons = []
        if record.paper_type in self.config.nonstandard_types:
            reasons.append('Nonstandard publication type: ' + record.paper_type)
        title = normalize(record.title)
        for prefix in self.config.nonstandard_title_prefixes:
            if title == prefix or title.startswith(prefix + ' '):
                reasons.append('Possible nonstandard contributor list: title prefix ' + prefix)
                break
        return reasons

    def _coauthor_blockers(self, record):
        reasons = self._nonstandard_reasons(record)
        count = max(len(record.authors), len(record.author_details))
        if count > self.config.maximum_coauthor_authors:
            reasons.append(f'Large author list ({count}); coauthor overlap cannot verify ownership')
        if record.authors_complete is False:
            reasons.append('Incomplete author list; coauthor evidence disabled')
        return reasons

    def _institution_agrees(self, details, profile):
        confirmed = profile.matched_affiliations
        if not confirmed and profile.university:
            confirmed = [profile.university]
        if not confirmed and not profile.requested_university:
            # Library callers may explicitly supply confirmed institutions without a query.
            confirmed = profile.affiliations
        for author in details:
            if author.get('affiliation_scope') == 'author_profile':
                continue
            for institution in author.get('institutions') or []:
                observed = institution.get('display_name')
                if not observed:
                    continue
                for expected in confirmed:
                    if institution_similarity(observed, expected) >= self.config.institution_similarity:
                        return observed
        return None

    def _evaluate_record(self, record, profile):
        evidence = list(record.attribution_evidence)
        evidence.extend('Metadata warning: ' + message for message in record.metadata_errors)
        requested_id = canonical_author_id(record.source, record.source_author_id)
        if requested_id not in self._selected_ids(profile, record.source):
            return OwnershipStatus.NEEDS_REVIEW, [*evidence, 'Source Author ID is not part of the selected identity']
        if not record.title or not record.source_paper_id:
            return OwnershipStatus.NEEDS_REVIEW, [*evidence, 'Insufficient publication metadata']
        details = self._target_details(record, profile)
        if record.target_author_present is False and not details:
            return OwnershipStatus.REJECTED, [*evidence, 'Source metadata explicitly contradicts target authorship']
        if record.author_details and not details:
            all_ids = all(author.get('author_id') for author in record.author_details)
            if all_ids and record.authors_complete is not False:
                return OwnershipStatus.REJECTED, [*evidence, 'Target ID absent from explicit authorship metadata']
            return OwnershipStatus.NEEDS_REVIEW, [
                *evidence,
                'Author IDs missing or truncated; cannot verify target authorship',
            ]
        for author in details:
            if author.get('role', 'author') != 'author':
                return OwnershipStatus.REJECTED, [
                    *evidence,
                    'Target is explicitly a contributor, editor or reviewer, not an author',
                ]
        if details:
            evidence.append('Confirmed source-scoped Author ID observed in the paper author list')
        else:
            evidence.append('Explicit target author details unavailable; using source author list')
        evidence.append('Source Author ID association alone does not verify ownership')
        best, signatures = self._signature_score(details, record, profile)
        evidence.append(f'Target signature similarity: {best:.3f}')
        if details and signatures and best < self.config.incompatible_name:
            return OwnershipStatus.REJECTED, [*evidence, 'Explicit target author signature is incompatible']
        observed_orcids = self._observed_orcids(details)
        if len(observed_orcids) > 1:
            return OwnershipStatus.NEEDS_REVIEW, [
                *evidence,
                'Target authorship ORCID conflict: multiple identities reported',
            ]
        if profile.orcid and observed_orcids != {canonical_orcid(profile.orcid)}:
            # An author-profile ORCID is constant across a source author ID, so it says nothing about this paper;
            # merged split profiles legitimately carry different ones. Only paper-level or unscoped ORCIDs reject.
            if self._observed_orcids(details, paper_level=True) - {canonical_orcid(profile.orcid)}:
                return OwnershipStatus.REJECTED, [
                    *evidence,
                    'Target authorship ORCID conflicts with confirmed professor ORCID',
                ]
            if observed_orcids:
                evidence.append('Author-profile ORCID differs from the identity ORCID; not paper-level evidence')
        nonstandard = self._nonstandard_reasons(record)
        evidence.extend(self._coauthor_blockers(record))
        if nonstandard:
            return OwnershipStatus.NEEDS_REVIEW, [
                *evidence,
                'Authorship role requires manual confirmation; metadata was retained',
            ]
        if not signatures or best < self.config.name_similarity:
            return OwnershipStatus.NEEDS_REVIEW, [*evidence, 'Insufficient compatible target byline evidence']
        if profile.orcid and observed_orcids == {canonical_orcid(profile.orcid)}:
            scopes = sorted({author.get('orcid_scope') or 'unspecified' for author in details})
            paper_details = [
                author for author in details if author.get('orcid_scope') in self.config.paper_orcid_scopes
            ]
            if self._observed_orcids(paper_details) == {canonical_orcid(profile.orcid)}:
                return OwnershipStatus.VERIFIED, [
                    *evidence,
                    'Target authorship ORCID agrees with confirmed professor ORCID (scope: {})'.format(
                        ', '.join(scopes)
                    ),
                ]
            evidence.append(
                'Matching author-profile or unscoped ORCID is not independent paper authorship evidence '
                '(scope: {})'.format(', '.join(scopes))
            )
        institution = self._institution_agrees(details, profile)
        if details and institution:
            return OwnershipStatus.VERIFIED, [
                *evidence,
                'Explicit target Author ID, compatible byline and paper-level institution agree: ' + institution,
            ]
        return OwnershipStatus.NEEDS_REVIEW, evidence

    def _compatible_signature(self, record, profile):
        details = self._target_details(record, profile)
        score, signatures = self._signature_score(details, record, profile)
        return bool(signatures) and score >= self.config.name_similarity

    def _coauthor_eligible(self, record, profile):
        if not self._identity_eligible(record, profile):
            return False
        if self._coauthor_blockers(record):
            return False
        if record.author_details and not self._target_details(record, profile):
            return False
        return self._compatible_signature(record, profile)

    def _identity_eligible(self, record, profile):
        requested = canonical_author_id(record.source, record.source_author_id)
        if requested not in self._selected_ids(profile, record.source):
            return False
        for message in record.ownership_evidence:
            if 'ORCID conflict' in message or 'Nonstandard source classification in merged publication' in message:
                return False
        return True

    def _coauthors(self, record, profile):
        names = set()
        excluded = set()
        for detail in self._target_details(record, profile):
            excluded.update(normalize(name) for name in signature_names(detail))
            if detail.get('name'):
                excluded.add(normalize(detail['name']))
        for detail in record.author_details:
            if detail.get('role', 'author') != 'author':
                excluded.update(normalize(name) for name in signature_names(detail))
        for author in record.authors:
            if normalize(author) in excluded:
                continue
            is_target = any(
                author_signatures_match(name, form)
                for name in [profile.name, *profile.name_aliases]
                for form in byline_forms(author)
            )
            if not is_target:
                names.add(normalize(author))
        return names

    def validate(self, papers, profile):
        for paper in papers:
            for record in paper.records:
                status, evidence = self._evaluate_record(record, profile)
                record.ownership_status = status
                record.ownership_evidence = evidence
        for paper in papers:
            self._cross_validate(paper, profile)
        known_coauthors = self._trusted_coauthors(papers, profile)
        # A fixed seed set prevents unverified records from reinforcing one another.
        for paper in papers:
            for record in paper.records:
                if record.ownership_status != OwnershipStatus.NEEDS_REVIEW or not self._coauthor_eligible(
                    record, profile
                ):
                    continue
                overlap = self._coauthors(record, profile) & known_coauthors
                if len(overlap) >= self.config.minimum_known_coauthors:
                    record.ownership_status = OwnershipStatus.VERIFIED
                    record.ownership_evidence.append(
                        'Compatible signature plus recurring independently verified coauthors: '
                        + ', '.join(sorted(overlap))
                    )
            self._aggregate(paper)
        return papers

    def _trusted_coauthors(self, papers, profile):
        counts = Counter()
        seed_titles = set()
        for paper in papers:
            statuses = {record.ownership_status for record in paper.records}
            if OwnershipStatus.VERIFIED not in statuses or OwnershipStatus.REJECTED in statuses:
                continue
            title = normalize(paper.title)
            if title in seed_titles:
                continue
            seed_titles.add(title)
            coauthors = set()
            for record in paper.records:
                if record.ownership_status == OwnershipStatus.VERIFIED and self._coauthor_eligible(record, profile):
                    coauthors.update(self._coauthors(record, profile))
            counts.update(coauthors)
        return {name for name, count in counts.items() if count >= self.config.seed_papers_per_coauthor}

    def _cross_source_evidence(self, left, right, profile):
        links = duplicate_evidence(left, right)
        if not any(message.startswith('Shared stable identifier:') for message in links):
            return None
        left_details = self._target_details(left, profile, self._corroborating_ids(profile, left.source))
        right_details = self._target_details(right, profile, self._corroborating_ids(profile, right.source))
        uncorroborated = [
            record
            for record, details in ((left, left_details), (right, right_details))
            if not details and self._target_details(record, profile)
        ]
        if uncorroborated:
            return None
        if left_details and right_details:
            large = max(len(left.authors), len(right.authors), len(left.author_details), len(right.author_details))
            independent = (
                left.ownership_status == OwnershipStatus.VERIFIED or right.ownership_status == OwnershipStatus.VERIFIED
            )
            if large > self.config.maximum_coauthor_authors and not independent:
                return None
            # IDs from different databases are compared to their own verified namespace.
            return (
                'Cross-source stable work identity and confirmed source-scoped target Author IDs '
                'with compatible bylines'
            )
        if self._coauthor_eligible(left, profile) and self._coauthor_eligible(right, profile):
            coauthors = self._coauthors(left, profile) & self._coauthors(right, profile)
            if len(coauthors) >= self.config.minimum_shared_coauthors:
                return 'Cross-source stable work identity, compatible target signatures and shared coauthors'
        return None

    def _cross_validate(self, paper, profile):
        if any(self._nonstandard_reasons(record) for record in paper.records):
            for record in paper.records:
                if record.ownership_status != OwnershipStatus.REJECTED:
                    record.ownership_status = OwnershipStatus.NEEDS_REVIEW
                    record.ownership_evidence.append(
                        'Nonstandard source classification in merged publication; automatic verification blocked'
                    )
            return
        eligible = []
        for record in paper.records:
            if record.ownership_status == OwnershipStatus.REJECTED or self._nonstandard_reasons(record):
                continue
            if not self._identity_eligible(record, profile):
                continue
            if self._compatible_signature(record, profile):
                eligible.append(record)
        upgrades = {}
        for index, left in enumerate(eligible):
            for right in eligible[index + 1 :]:
                if left.source == right.source:
                    continue
                left_orcids = self._observed_orcids(self._target_details(left, profile), paper_level=True)
                right_orcids = self._observed_orcids(self._target_details(right, profile), paper_level=True)
                if left_orcids and right_orcids and left_orcids != right_orcids:
                    for record in (left, right):
                        record.ownership_status = OwnershipStatus.NEEDS_REVIEW
                        record.ownership_evidence.append(
                            'Cross-source target ORCID conflict; automatic verification blocked'
                        )
                    continue
                message = self._cross_source_evidence(left, right, profile)
                if message:
                    upgrades.setdefault(id(left), set()).add(message)
                    upgrades.setdefault(id(right), set()).add(message)
        for record in eligible:
            conflict = any('Cross-source target ORCID conflict' in message for message in record.ownership_evidence)
            if id(record) in upgrades and not conflict:
                record.ownership_status = OwnershipStatus.VERIFIED
                record.ownership_evidence.extend(sorted(upgrades[id(record)]))

    @staticmethod
    def _aggregate(paper):
        statuses = {record.ownership_status for record in paper.records}
        if statuses == {OwnershipStatus.REJECTED}:
            paper.ownership_status = OwnershipStatus.REJECTED
        elif OwnershipStatus.VERIFIED in statuses and OwnershipStatus.REJECTED not in statuses:
            paper.ownership_status = OwnershipStatus.VERIFIED
        else:
            paper.ownership_status = OwnershipStatus.NEEDS_REVIEW
        evidence = set()
        for record in paper.records:
            for message in record.ownership_evidence:
                evidence.add(f'{record.source}:{record.source_paper_id}: {message}')
        if OwnershipStatus.VERIFIED in statuses and OwnershipStatus.REJECTED in statuses:
            evidence.add('Conflicting record-level ownership decisions; review required')
        paper.ownership_evidence = sorted(evidence)
