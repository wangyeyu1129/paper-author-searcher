"""Conservative, source-scoped ownership decisions with traceable evidence."""
from collections import Counter

from .config import DEFAULT_OWNERSHIP
from .matching import canonical_orcid, institution_similarity, name_similarity, normalize
from .paper_authorship import byline_forms, canonical_author_id, signature_names
from .paper_deduplication import duplicate_evidence, author_signatures_match


class PaperOwnershipValidator:
    def __init__(self, config=DEFAULT_OWNERSHIP):
        self.config = config

    @staticmethod
    def _selected_ids(profile, source):
        values = list(profile.author_id_aliases.get(source) or [])
        if profile.author_ids.get(source):
            values.append(profile.author_ids[source])
        return {canonical_author_id(source, value) for value in values}

    def _target_details(self, record, profile=None):
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
            for name in [profile.name] + profile.name_aliases:
                best = max(best, name_similarity(name, signature))
        return best, signatures

    @staticmethod
    def _observed_orcids(details):
        values = set()
        for author in details:
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
            reasons.append('Large author list ({}); coauthor overlap cannot verify ownership'.format(count))
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
            return 'needs_review', evidence + ['Source Author ID is not part of the selected identity']
        if not record.title or not record.source_paper_id:
            return 'needs_review', evidence + ['Insufficient publication metadata']
        details = self._target_details(record, profile)
        if record.target_author_present is False and not details:
            return 'rejected', evidence + ['Source metadata explicitly contradicts target authorship']
        if record.author_details and not details:
            all_ids = all(author.get('author_id') for author in record.author_details)
            if all_ids and record.authors_complete is not False:
                return 'rejected', evidence + ['Target ID absent from explicit authorship metadata']
            return 'needs_review', evidence + ['Author IDs missing or truncated; cannot verify target authorship']
        for author in details:
            if author.get('role', 'author') != 'author':
                return 'rejected', evidence + ['Target is explicitly a contributor, editor or reviewer, not an author']
        if details:
            evidence.append('Confirmed source-scoped Author ID observed in the paper author list')
        else:
            evidence.append('Explicit target author details unavailable; using source author list')
        evidence.append('Source Author ID association alone does not verify ownership')
        best, signatures = self._signature_score(details, record, profile)
        evidence.append('Target signature similarity: {:.3f}'.format(best))
        if details and signatures and best < self.config.incompatible_name:
            return 'rejected', evidence + ['Explicit target author signature is incompatible']
        observed_orcids = self._observed_orcids(details)
        if len(observed_orcids) > 1:
            return 'needs_review', evidence + ['Target authorship ORCID conflict: multiple identities reported']
        if profile.orcid and observed_orcids:
            if observed_orcids != {canonical_orcid(profile.orcid)}:
                return 'rejected', evidence + ['Target authorship ORCID conflicts with confirmed professor ORCID']
        nonstandard = self._nonstandard_reasons(record)
        evidence.extend(self._coauthor_blockers(record))
        if nonstandard:
            return 'needs_review', evidence + ['Authorship role requires manual confirmation; metadata was retained']
        if not signatures or best < self.config.name_similarity:
            return 'needs_review', evidence + ['Insufficient compatible target byline evidence']
        if profile.orcid and observed_orcids:
            scopes = sorted({author.get('orcid_scope') or 'unspecified' for author in details})
            paper_details = [author for author in details
                             if author.get('orcid_scope') in self.config.paper_orcid_scopes]
            if self._observed_orcids(paper_details) == {canonical_orcid(profile.orcid)}:
                return 'verified', evidence + [
                    'Target authorship ORCID agrees with confirmed professor ORCID (scope: {})'.format(', '.join(scopes)),
                ]
            evidence.append('Matching author-profile or unscoped ORCID is not independent paper authorship evidence '
                            '(scope: {})'.format(', '.join(scopes)))
        institution = self._institution_agrees(details, profile)
        if details and institution:
            return 'verified', evidence + [
                'Explicit target Author ID, compatible byline and paper-level institution agree: ' + institution,
            ]
        return 'needs_review', evidence

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
            is_target = any(author_signatures_match(name, form)
                            for name in [profile.name] + profile.name_aliases
                            for form in byline_forms(author))
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
                if record.ownership_status != 'needs_review' or not self._coauthor_eligible(record, profile):
                    continue
                overlap = self._coauthors(record, profile) & known_coauthors
                if len(overlap) >= self.config.minimum_known_coauthors:
                    record.ownership_status = 'verified'
                    record.ownership_evidence.append(
                        'Compatible signature plus recurring independently verified coauthors: ' + ', '.join(sorted(overlap))
                    )
            self._aggregate(paper)
        return papers

    def _trusted_coauthors(self, papers, profile):
        counts = Counter()
        seed_titles = set()
        for paper in papers:
            statuses = {record.ownership_status for record in paper.records}
            if 'verified' not in statuses or 'rejected' in statuses:
                continue
            title = normalize(paper.title)
            if title in seed_titles:
                continue
            seed_titles.add(title)
            coauthors = set()
            for record in paper.records:
                if record.ownership_status == 'verified' and self._coauthor_eligible(record, profile):
                    coauthors.update(self._coauthors(record, profile))
            counts.update(coauthors)
        return {name for name, count in counts.items() if count >= self.config.seed_papers_per_coauthor}

    def _cross_source_evidence(self, left, right, profile):
        links = duplicate_evidence(left, right)
        if not any(message.startswith('Shared stable identifier:') for message in links):
            return None
        left_details = self._target_details(left, profile)
        right_details = self._target_details(right, profile)
        if left_details and right_details:
            large = max(len(left.authors), len(right.authors), len(left.author_details), len(right.author_details))
            independent = left.ownership_status == 'verified' or right.ownership_status == 'verified'
            if large > self.config.maximum_coauthor_authors and not independent:
                return None
            # IDs from different databases are compared to their own verified namespace.
            return 'Cross-source stable work identity and confirmed source-scoped target Author IDs with compatible bylines'
        if self._coauthor_eligible(left, profile) and self._coauthor_eligible(right, profile):
            coauthors = self._coauthors(left, profile) & self._coauthors(right, profile)
            if len(coauthors) >= self.config.minimum_shared_coauthors:
                return 'Cross-source stable work identity, compatible target signatures and shared coauthors'
        return None

    def _cross_validate(self, paper, profile):
        if any(self._nonstandard_reasons(record) for record in paper.records):
            for record in paper.records:
                if record.ownership_status != 'rejected':
                    record.ownership_status = 'needs_review'
                    record.ownership_evidence.append(
                        'Nonstandard source classification in merged publication; automatic verification blocked'
                    )
            return
        eligible = []
        for record in paper.records:
            if record.ownership_status == 'rejected' or self._nonstandard_reasons(record):
                continue
            if not self._identity_eligible(record, profile):
                continue
            if self._compatible_signature(record, profile):
                eligible.append(record)
        upgrades = {}
        for index, left in enumerate(eligible):
            for right in eligible[index + 1:]:
                if left.source == right.source:
                    continue
                left_orcids = self._observed_orcids(self._target_details(left, profile))
                right_orcids = self._observed_orcids(self._target_details(right, profile))
                if left_orcids and right_orcids and left_orcids != right_orcids:
                    for record in (left, right):
                        record.ownership_status = 'needs_review'
                        record.ownership_evidence.append('Cross-source target ORCID conflict; automatic verification blocked')
                    continue
                message = self._cross_source_evidence(left, right, profile)
                if message:
                    upgrades.setdefault(id(left), set()).add(message)
                    upgrades.setdefault(id(right), set()).add(message)
        for record in eligible:
            conflict = any('Cross-source target ORCID conflict' in message for message in record.ownership_evidence)
            if id(record) in upgrades and not conflict:
                record.ownership_status = 'verified'
                record.ownership_evidence.extend(sorted(upgrades[id(record)]))

    @staticmethod
    def _aggregate(paper):
        statuses = {record.ownership_status for record in paper.records}
        if statuses == {'rejected'}:
            paper.ownership_status = 'rejected'
        elif 'verified' in statuses and 'rejected' not in statuses:
            paper.ownership_status = 'verified'
        else:
            paper.ownership_status = 'needs_review'
        evidence = set()
        for record in paper.records:
            for message in record.ownership_evidence:
                evidence.add('{}:{}: {}'.format(record.source, record.source_paper_id, message))
        if 'verified' in statuses and 'rejected' in statuses:
            evidence.add('Conflicting record-level ownership decisions; review required')
        paper.ownership_evidence = sorted(evidence)
