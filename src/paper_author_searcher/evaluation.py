"""Offline evaluation of identity, retrieval, ownership and deduplication."""
from dataclasses import asdict

from .evaluation_matching import GroundTruth, GroundTruthMatcher, stable_identifiers, metadata_key

STATUSES = ('verified', 'needs_review', 'rejected')


def _ratio(numerator, denominator):
    if denominator == 0:
        return None
    return numerator / denominator


def _metrics(positive, negative, total_positive):
    true_positives = len(positive)
    false_positives = len(negative)
    false_negatives = total_positive - true_positives
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, total_positive)
    f1 = _ratio(2 * true_positives, 2 * true_positives + false_positives + false_negatives)
    return {
        'true_positives': true_positives, 'false_positives': false_positives,
        'false_negatives': false_negatives, 'precision': precision, 'recall': recall, 'f1': f1,
    }


def _choose_two(count):
    return count * (count - 1) // 2


def _paper_summary(paper, index):
    fields = ('title', 'year', 'authors', 'doi', 'source', 'source_paper_id', 'source_author_id',
              'ownership_status', 'ownership_evidence', 'deduplication_evidence', 'related_versions')
    summary = {key: paper.get(key) for key in fields}
    summary['entry_index'] = index
    summary['source_records'] = []
    for record in paper.get('records') or [paper]:
        summary['source_records'].append({
            'source': record.get('source'), 'paper_id': record.get('source_paper_id'),
            'author_id': record.get('source_author_id'), 'doi': record.get('doi'),
            'ownership_status': record.get('ownership_status'),
            'ownership_evidence': record.get('ownership_evidence') or [],
        })
    return summary


class Evaluator:
    def evaluate(self, saved_result, ground_truth):
        if not isinstance(saved_result, dict):
            raise ValueError('Saved result must be an object')
        if isinstance(ground_truth, dict):
            ground_truth = GroundTruth.from_dict(ground_truth)
        matcher = GroundTruthMatcher(ground_truth)
        collection = saved_result.get('collection', saved_result)
        if not isinstance(collection, dict):
            raise ValueError('Collection must be an object')
        papers = collection.get('all_papers', collection.get('papers'))
        if papers is None and ('profile' in saved_result or 'status' in saved_result):
            return self._resolver_only(saved_result, ground_truth)
        if not isinstance(papers, list):
            raise ValueError('Collection must contain papers or all_papers')
        sources = collection.get('sources', [])
        if not isinstance(sources, list) or not all(isinstance(source, dict) for source in sources):
            raise ValueError('Collection sources must be a list of objects')
        matches, review = self._match_entries(papers, matcher)
        positives = {paper['evaluation_id'] for paper in ground_truth.papers if paper['ownership'] == 'belongs'}
        negatives = {paper['evaluation_id'] for paper in ground_truth.papers if paper['ownership'] == 'not_belongs'}
        explicit_negative_count = len(negatives)
        inferred_negatives = {entry['inferred_negative'] for entry in matches if entry['inferred_negative']}
        negatives.update(inferred_negatives)
        sets = {status: set() for status in STATUSES}
        for entry in matches:
            sets[entry['status']].update(entry['judged_ids'])
        all_ids = set().union(*sets.values())
        status_metrics = {}
        for status in STATUSES:
            found = sets[status]
            status_metrics[status] = _metrics(found & positives, found & negatives, len(positives))
            status_metrics[status]['entry_count'] = sum(entry['status'] == status for entry in matches)
            status_metrics[status]['unjudged_entries'] = sum(
                entry['status'] == status and not entry['judged_ids'] for entry in matches
            )
        false_positives = self._false_positives(matches, negatives)
        missing_candidates = positives - all_ids
        missing_verified = positives - sets['verified']
        conflicts = []
        for evaluation_id in all_ids:
            statuses = [status for status in STATUSES if evaluation_id in sets[status]]
            if len(statuses) > 1:
                conflicts.append({'evaluation_id': evaluation_id, 'predicted_statuses': statuses})
        report = {
            'schema_version': 1,
            'metric_scope': 'unique_adjudicated_ground_truth_publications',
            'complete_positive_list': ground_truth.complete_positive_list,
            'ground_truth': {
                'positive_publications': len(positives), 'negative_publications': explicit_negative_count,
                'closed_world_inferred_negatives': len(inferred_negatives),
                'uncertain_publications': len(ground_truth.papers) - len(positives) - explicit_negative_count,
                'metadata': ground_truth.metadata,
            },
            'ownership': status_metrics['verified'],
            'all_candidates': _metrics(all_ids & positives, all_ids & negatives, len(positives)),
            'by_status': status_metrics,
            'rejection_precision': _ratio(len(sets['rejected'] & negatives),
                                         len(sets['rejected'] & (positives | negatives))),
            'deduplication': self._deduplication(matches),
            'false_positives': false_positives,
            'false_negatives': {
                'all_candidates': self._false_negatives(missing_candidates, matches, matcher, 'all_candidates'),
                'verified': self._false_negatives(missing_verified, matches, matcher, 'verified'),
            },
            'manual_confirmation': review,
            'judgment_coverage': {
                'total_entries': len(matches),
                'entries_with_scored_ownership': sum(bool(entry['judged_ids']) for entry in matches),
                'unjudged_entries': sum(not entry['judged_ids'] for entry in matches),
                'matched_positive_truth_fraction': _ratio(len(all_ids & positives), len(positives)),
            },
            'status_conflicts': sorted(conflicts, key=lambda entry: entry['evaluation_id']),
            'professor_resolver': self._evaluate_professor(saved_result, ground_truth.professor),
            'retrieval': {
                'available': True,
                'complete': collection.get('complete'), 'sources': sources,
                'unparsed_records': sum(len(source.get('rejected_records') or [])
                                       for source in sources),
            },
            'notes': [
                'No ownership or deduplication rules were rerun or tuned.',
                'Uncertain, conflicting or insufficient metadata is excluded from scored decisions.',
                'Recall is relative to the imported positive list; completeness requires human declaration.',
                'Per-status precision is positive ownership yield; rejection_precision measures correct rejection.',
                'A work with conflicting statuses counts as verified if any entry is verified; conflicts are reported.',
                'Candidate metrics cover retained parsed records; unparsed source records are reported separately.',
            ],
        }
        return report

    def _resolver_only(self, saved_result, ground_truth):
        return {
            'schema_version': 1,
            'professor_resolver': self._evaluate_professor(saved_result, ground_truth.professor),
            'ownership': None, 'all_candidates': None, 'by_status': {},
            'deduplication': None, 'judgment_coverage': None,
            'false_positives': [], 'false_negatives': {}, 'manual_confirmation': [],
            'retrieval': {'available': False},
            'notes': ['No collection supplied; publication metrics were not evaluated.'],
        }

    def _match_entries(self, papers, matcher):
        entries = []
        review = []
        for index, paper in enumerate(papers):
            if not isinstance(paper, dict):
                raise ValueError('Collection papers must be objects')
            records = paper.get('records') or [paper]
            if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
                raise ValueError('Paper records must be a list of objects')
            status = paper.get('ownership_status', 'needs_review')
            if status not in STATUSES:
                raise ValueError('Unknown ownership_status: ' + str(status))
            summary = _paper_summary(paper, index)
            record_matches = []
            judged_ids = set()
            concerns = []
            for record_index, record in enumerate(records):
                match = matcher.match(record)
                annotation = matcher.by_id.get(match.evaluation_id)
                if annotation and annotation['ownership'] != 'uncertain':
                    judged_ids.add(match.evaluation_id)
                else:
                    concerns.append(match.decision)
                record_matches.append({
                    'record_index': record_index, 'source': record.get('source'),
                    'paper_id': record.get('source_paper_id'), 'author_id': record.get('source_author_id'),
                    'match': asdict(match),
                })
            # Closed-world evaluation may count well-identified absent publications as false positives.
            inferred_negative = None
            if not judged_ids and matcher.truth.complete_positive_list:
                if all(record['match']['decision'] == 'unmatched' for record in record_matches):
                    keys = stable_identifiers(paper)
                    description = metadata_key(paper)
                    if keys or description is not None:
                        inferred_negative = 'absent:' + repr(sorted(keys) or description)
            if inferred_negative:
                judged_ids.add(inferred_negative)
            entry = {
                'summary': summary, 'status': status, 'records': record_matches,
                'judged_ids': judged_ids, 'inferred_negative': inferred_negative,
            }
            entries.append(entry)
            if status == 'needs_review' or concerns:
                review.append({
                    **summary, 'reasons': sorted(set(concerns + (['Ownership validator abstained'] if status == 'needs_review' else []))),
                    'ground_truth_matches': record_matches,
                })
        return entries, review

    @staticmethod
    def _false_positives(entries, negatives):
        output = []
        for entry in entries:
            if entry['status'] != 'verified':
                continue
            wrong = entry['judged_ids'] & negatives
            if entry['inferred_negative']:
                wrong.add(entry['inferred_negative'])
            if wrong:
                output.append({**entry['summary'], 'negative_evaluation_ids': sorted(wrong),
                               'ground_truth_matches': entry['records']})
        return output

    @staticmethod
    def _false_negatives(missing, entries, matcher, scope):
        output = []
        for evaluation_id in sorted(missing):
            retrieved = []
            unresolved = []
            for entry in entries:
                if evaluation_id in entry['judged_ids']:
                    retrieved.append(entry['summary'])
                for record in entry['records']:
                    if evaluation_id in record['match']['candidates'] and record['match']['evaluation_id'] is None:
                        unresolved.append({'entry_index': entry['summary']['entry_index'], **record})
            reason = 'No confirmed retrieval match'
            if retrieved and scope == 'verified':
                reason = 'Retrieved but not classified verified'
            output.append({
                'evaluation_id': evaluation_id,
                'ground_truth': matcher.by_id[evaluation_id],
                'reason': reason, 'retrieved_entries': retrieved, 'unresolved_matches': unresolved,
            })
        return output

    @staticmethod
    def _deduplication(entries):
        locations = {}
        true_pairs = 0
        false_pairs = 0
        wrong_merges = []
        assessed = 0
        total_records = 0
        for entry_index, entry in enumerate(entries):
            counts = {}
            for record in entry['records']:
                total_records += 1
                evaluation_id = record['match']['evaluation_id']
                if evaluation_id is None:
                    continue
                assessed += 1
                counts[evaluation_id] = counts.get(evaluation_id, 0) + 1
                locations.setdefault(evaluation_id, {}).setdefault(entry_index, []).append(record)
            true_pairs += sum(_choose_two(count) for count in counts.values())
            false_pairs += _choose_two(sum(counts.values())) - sum(_choose_two(count) for count in counts.values())
            if len(counts) > 1:
                wrong_merges.append({**entry['summary'], 'ground_truth_ids': sorted(counts),
                                     'ground_truth_matches': entry['records']})
        missing_pairs = 0
        missed_duplicates = []
        for evaluation_id, groups in sorted(locations.items()):
            count = sum(len(records) for records in groups.values())
            missing_pairs += _choose_two(count) - sum(_choose_two(len(records)) for records in groups.values())
            if len(groups) > 1:
                missed_duplicates.append({'evaluation_id': evaluation_id, 'entry_indices': sorted(groups),
                                          'source_records': [record for records in groups.values() for record in records]})
        denominator = 2 * true_pairs + false_pairs + missing_pairs
        return {
            'true_duplicate_pairs_merged': true_pairs,
            'different_publication_pairs_merged': false_pairs,
            'true_duplicate_pairs_missed': missing_pairs,
            'pairwise_precision': _ratio(true_pairs, true_pairs + false_pairs),
            'pairwise_recall': _ratio(true_pairs, true_pairs + missing_pairs),
            'pairwise_f1': _ratio(2 * true_pairs, denominator),
            'assessed_records': assessed, 'total_retained_records': total_records,
            'annotation_coverage': _ratio(assessed, total_records),
            'incorrect_merges': wrong_merges, 'missed_duplicates': missed_duplicates,
        }

    @staticmethod
    def _evaluate_professor(saved, annotation):
        if not annotation or not (annotation.get('author_ids') or annotation.get('orcid')):
            return {'available': False, 'reason': 'No human-verified professor Author IDs supplied'}
        profile = saved.get('profile') or saved
        aliases = profile.get('author_id_aliases') or {}
        observed = {source: set(ids) for source, ids in aliases.items()}
        for source, author_id in (profile.get('author_ids') or {}).items():
            observed.setdefault(source, set()).add(author_id)
        expected = {source: set(ids) for source, ids in (annotation.get('author_ids') or {}).items()}
        correct = sum(len(observed.get(source, set()) & ids) for source, ids in expected.items())
        total_expected = sum(len(ids) for ids in expected.values())
        total_observed = sum(len(ids) for ids in observed.values())
        exhaustive = annotation.get('author_ids_complete', False)
        extra = total_observed - correct
        from .matching import canonical_orcid

        orcid_agreement = None
        if annotation.get('orcid') and profile.get('orcid'):
            orcid_agreement = canonical_orcid(annotation['orcid']) == canonical_orcid(profile['orcid'])
        confirmed = None
        if exhaustive and total_observed:
            confirmed = correct > 0 and extra == 0
        if orcid_agreement is False:
            confirmed = False
        elif orcid_agreement is True and not total_observed:
            confirmed = True
        candidates = saved.get('candidates') or [profile]
        candidate_ids = {}
        for candidate in candidates:
            for source, ids in (candidate.get('author_id_aliases') or {}).items():
                candidate_ids.setdefault(source, set()).update(ids)
            for source, author_id in (candidate.get('author_ids') or {}).items():
                candidate_ids.setdefault(source, set()).add(author_id)
        candidate_correct = sum(len(candidate_ids.get(source, set()) & ids) for source, ids in expected.items())
        return {
            'available': True, 'status': profile.get('status'),
            'expected_author_ids': {source: sorted(ids) for source, ids in expected.items()},
            'observed_author_ids': {source: sorted(ids) for source, ids in observed.items()},
            'matched_ids': correct, 'unmatched_ids': extra,
            'author_id_recall': _ratio(correct, total_expected),
            'author_id_precision': _ratio(correct, total_observed) if exhaustive else None,
            'top_identity_confirmed': confirmed,
            'orcid_agreement': orcid_agreement,
            'candidate_author_id_recall': _ratio(candidate_correct, total_expected),
            'auto_accepted_correct': confirmed if profile.get('status') == 'resolved' else None,
            'evidence': profile.get('evidence') or [],
        }
