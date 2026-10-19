"""Prepare a bounded, independent reference review without assigning human labels."""

import argparse
import csv
import json
from collections import Counter
from copy import deepcopy
from itertools import combinations
from pathlib import Path

from scripts.ground_truth.build_candidates import export_candidates, read_json, write_json

from paper_author_searcher.matching import normalize


def select_pilot(dataset, professor_id):
    professors = [item for item in dataset['scope']['professors'] if item['professor_id'] == professor_id]
    if len(professors) != 1:
        raise ValueError('Select one existing professor_id')
    pilot = deepcopy(dataset)
    pilot['scope']['professors'] = professors
    pilot['papers'] = [deepcopy(paper) for paper in dataset['papers'] if paper['professor_id'] == professor_id]
    if any(paper['review_status'] != 'candidate' for paper in pilot['papers']):
        raise ValueError('Prepare the checklist from the original candidate snapshot')
    pilot['human_confirmed_count'] = 0
    pilot['complete_positive_list'] = False
    pilot['statistics'] = {'candidate_count': len(pilot['papers'])}
    return pilot


def paper_links(paper):
    links = set()
    for reference in paper['reference_evidence']:
        links.update(reference.get('linked_urls') or [])
        extra = reference.get('extra') or {}
        for key in ('publication_identity_url', 'official_proceedings_record_url'):
            if extra.get(key):
                links.add(extra[key])
    return sorted(links)


def relationship_suggestions(papers):
    reference_owner = {}
    for paper in papers:
        for reference in paper['reference_evidence']:
            reference_owner[reference['reference_id']] = paper['candidate_id']
    suggestions = {}

    def suggest(left, right, reason):
        if left == right:
            return
        pair = tuple(sorted((left, right)))
        suggestions.setdefault(pair, set()).add(reason)

    for paper in papers:
        for reference in paper['reference_evidence']:
            for parent in reference.get('extra', {}).get('linked_from_reference_ids', []):
                if parent in reference_owner:
                    suggest(paper['candidate_id'], reference_owner[parent], 'explicit_bibliography_link')
    for left, right in combinations(papers, 2):
        if normalize(left['title']) == normalize(right['title']):
            suggest(left['candidate_id'], right['candidate_id'], 'same_normalized_title')
        if left.get('doi') and left['doi'] == right.get('doi'):
            suggest(left['candidate_id'], right['candidate_id'], 'same_reported_doi')
    by_id = {paper['candidate_id']: paper for paper in papers}
    output = []
    for pair, reasons in sorted(suggestions.items()):
        left, right = [by_id[key] for key in pair]
        types = {left['publication_type'], right['publication_type']}
        if 'preprint' in types and len(types) > 1:
            suggested = 'possible_preprint_of'
        elif left['year'] != right['year'] or len(types) > 1:
            suggested = 'distinct_publication_versions_check'
        else:
            suggested = 'possible_same_publication'
        output.append(
            {
                'candidate_ids': list(pair),
                'titles': [left['title'], right['title']],
                'years': [left['year'], right['year']],
                'publication_types': [left['publication_type'], right['publication_type']],
                'suggested_relation': suggested,
                'evidence': sorted(reasons),
                'review_status': 'candidate',
                'human_relation': '',
                'reviewer': '',
                'reviewed_at': '',
                'evidence_urls': [],
                'notes': '',
            }
        )
    return output


def make_checklist(pilot, supplemental):
    professor = pilot['scope']['professors'][0]
    relations = relationship_suggestions(pilot['papers'])
    by_id = {paper['candidate_id']: paper for paper in pilot['papers']}
    links_by_id = {key: paper_links(paper) for key, paper in by_id.items()}
    output = []
    for paper in pilot['papers']:
        candidate_id = paper['candidate_id']
        related = [item for item in relations if candidate_id in item['candidate_ids']]
        verification = set(links_by_id[candidate_id])
        for item in related:
            for other in item['candidate_ids']:
                verification.update(links_by_id[other])
        supplement = supplemental.get('candidate_notes', {}).get(candidate_id, {})
        verification.update(supplement.get('verification_urls', []))
        flags = list(paper.get('review_warnings') or [])
        if professor.get('identity_risks'):
            flags.append('bibliography_identity_warning_not_a_per_paper_conflict')
        if not links_by_id[candidate_id]:
            flags.append('missing_direct_paper_level_link_in_snapshot')
        if paper['publication_type'] == 'unknown':
            flags.append('publication_type_unresolved')
        if any(item['suggested_relation'] == 'distinct_publication_versions_check' for item in related):
            flags.append('formal_versions_must_not_be_merged_without_review')
        flags.extend(supplement.get('flags', []))
        suggested = 'check_core_inclusion'
        if paper['publication_type'] in pilot['scope']['extended_publication_types']:
            suggested = 'related_or_extended_only'
        elif paper['publication_type'] not in pilot['scope']['core_publication_types']:
            suggested = 'check_type_before_inclusion'
        output.append(
            {
                'candidate_id': candidate_id,
                'title': paper['title'],
                'authors': paper['authors'],
                'year': paper['year'],
                'doi': paper['doi'],
                'venue': paper['venue'],
                'publication_type': paper['publication_type'],
                'source_urls': paper['source_urls'],
                'original_paper_links': links_by_id[candidate_id],
                'verification_urls': sorted(verification),
                'suggested_label': suggested,
                'review_flags': sorted(set(flags)),
                'related_records': related,
                'additional_source_observations': supplement,
                'review_status': 'candidate',
                'ownership': 'uncertain',
            }
        )
    return output, relations


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open('x', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            serialized = {}
            for key, value in row.items():
                serialized[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, list | dict) else value
            writer.writerow(serialized)


def prepare_review(dataset, professor_id, inventory, supplemental, output_dir):
    pilot = select_pilot(dataset, professor_id)
    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise ValueError('Use an empty output directory; never overwrite annotations')
    professor = pilot['scope']['professors'][0]
    source_ids = {reference['source_id'] for paper in pilot['papers'] for reference in paper['reference_evidence']}
    sources = [
        source
        for source in inventory['bibliography_sources']
        if source['source_id'] in source_ids
        or source['source_id'].startswith(('pmlr_', 'neurips_'))
        or source['url'] in (professor['bibliography_url'], professor['dblp_reference_url'] + '.xml')
    ]
    pilot['review_requirements'] = {
        'source_ids': [source['source_id'] for source in sources],
        'omission_ids': [item['omission_id'] for item in supplemental.get('possible_omissions', [])],
    }
    export_candidates(pilot, root)
    checklist, relations = make_checklist(pilot, supplemental)
    write_json(root / 'checklist.json', {'papers': checklist, 'human_confirmed_count': 0})
    write_csv(root / 'checklist.csv', checklist)
    write_json(root / 'relationships.json', {'suggestions': relations, 'automatically_merged': 0})
    write_csv(root / 'relationships.csv', relations)
    write_json(root / 'completeness_findings.json', supplemental)
    write_json(
        root / 'coverage_review.template.json',
        {
            'approved': False,
            'reviewer': '',
            'reviewed_at': '',
            'evidence_urls': [],
            'sources': [
                {
                    'source_id': source['source_id'],
                    'url': source['url'],
                    'observed_fetch_status': source['fetch_status'],
                    'human_checked': False,
                    'notes': '',
                }
                for source in sources
            ],
            'additional_venue_audit_complete': False,
            'identity_conflicts_resolved': False,
            'omissions': [
                dict(item, decision='pending', covered_candidate_id='', human_notes='')
                for item in supplemental.get('possible_omissions', [])
            ],
            'note': 'Approval requires independent human source/venue/version completeness review. '
            'Real missing publications require a later dataset revision, not automatic insertion.',
        },
    )
    write_json(root / 'matching_annotations.template.json', {'metadata_aliases': [], 'preprint_relations': []})
    lines = [
        '# {} {}–{} 审核清单'.format(professor['name'], pilot['scope']['year_start'], pilot['scope']['year_end']),
        '',
        f'{len(checklist)} 条原始候选；全部 candidate。关系提示不构成合并或人工确认。',
        '',
        '填写 candidates.csv 的人工字段。checklist.csv 提供辅助证据，原始事实保持不变。',
        '核查顺序：身份、论文级出版记录、类型、年份、版本关系、纳入范围。',
        '',
    ]
    for number, item in enumerate(checklist, 1):
        lines.extend(
            [
                '## {}. {}'.format(number, item['title']),
                '',
                '`{}` · {} · {} · {}'.format(
                    item['candidate_id'], item['year'], item['publication_type'], item['venue']
                ),
                '',
                '作者：' + '; '.join(item['authors']),
                '',
                'DOI：' + (item['doi'] or '未报告'),
                '',
                '原始来源：' + ' '.join(f'<{url}>' for url in dict.fromkeys(item['source_urls'])),
                '',
                '论文级核查及关联来源：'
                + (' '.join(f'<{url}>' for url in item['verification_urls']) or '缺失，须独立查证'),
                '',
                '审核提示：' + '; '.join(item['review_flags']),
                '',
                '关联候选：'
                + ', '.join(
                    sorted(
                        {
                            other
                            for relation in item['related_records']
                            for other in relation['candidate_ids']
                            if other != item['candidate_id']
                        }
                    )
                ),
                '',
                '- [ ] 身份与署名核查',
                '- [ ] 论文级元数据核查',
                '- [ ] 类型及版本核查',
                '- [ ] 人工决定及证据已填写',
                '',
            ]
        )
    (root / 'checklist.md').write_text('\n'.join(lines), encoding='utf-8')
    return {
        'candidates': len(checklist),
        'relationship_pairs': len(relations),
        'types': dict(Counter(item['publication_type'] for item in checklist)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--professor-id', required=True)
    parser.add_argument('--source-inventory', type=Path, required=True)
    parser.add_argument('--findings', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = prepare_review(
            read_json(args.dataset),
            args.professor_id,
            read_json(args.source_inventory),
            read_json(args.findings),
            args.output_dir,
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        'Prepared {} candidates and {} relationship pairs; no human labels or metrics'.format(
            summary['candidates'], summary['relationship_pairs']
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
