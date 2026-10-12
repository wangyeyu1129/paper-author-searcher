"""Publication-first review views; grouping suggestions never adjudicate identity."""
import argparse
import csv
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from paper_author_searcher.matching import canonical_doi, normalize
from paper_author_searcher.evaluation_matching import _bibliographic_authors_agree
from scripts.ground_truth.evaluate_pilot import fingerprint
from scripts.ground_truth.build_candidates import FACT_COLUMNS, REVIEW_COLUMNS, read_json, write_json
from scripts.ground_truth.review_candidates import validate_reviews


def _compatible_members(left, right):
    if left['year'] != right['year'] or left['publication_type'] != right['publication_type']:
        return False
    if left.get('authors') and right.get('authors') and not _bibliographic_authors_agree(left['authors'], right['authors']):
        return False
    if left.get('doi') and right.get('doi'):
        return canonical_doi(left['doi']) == canonical_doi(right['doi'])
    return True


def _primary_groups(papers, relationships):
    primary = {paper['candidate_id']: paper for paper in papers if paper['publication_type'] != 'preprint'}
    groups = {key: [key] for key in primary}
    owner = {key: key for key in primary}
    for relation in relationships:
        ids = relation['candidate_ids']
        if relation['suggested_relation'] != 'possible_same_publication' or any(key not in primary for key in ids):
            continue
        first, second = [owner[key] for key in ids]
        if first == second:
            continue
        members = groups[first] + groups[second]
        if not all(_compatible_members(primary[a], primary[b]) for a in members for b in members):
            continue
        groups[first] = members
        del groups[second]
        for key in members:
            owner[key] = first
    return list(groups.values())


def _cached_observations(paper):
    observations = []
    for reference in paper['reference_evidence']:
        for item in reference.get('extra', {}).get('linked_metadata_observations', []):
            values = item.get('metadata') or {}
            if item.get('fetch_status') != 'citation_metadata' or values.get('citation_arxiv_id'):
                continue
            title = (values.get('citation_title') or [None])[0]
            authors = values.get('citation_author') or []
            if not title or not authors:
                continue
            dates = values.get('citation_publication_date') or values.get('citation_date') or []
            year = int(dates[0][:4]) if dates and dates[0][:4].isdigit() else None
            observations.append({'candidate_ids': [paper['candidate_id']], 'url': item['url'],
                                 'source_role': 'formal_publication', 'access_status': 'metadata_observed',
                                 'collection_method': 'independent_official_reference_snapshot',
                                 'response_sha256': item.get('response_sha256'),
                                 'metadata': {'title': title.strip(), 'authors': authors, 'year': year,
                                              'publication_type': paper['publication_type'], 'doi': (values.get('citation_doi') or [None])[0],
                                              'venue': paper.get('venue')}, 'notes': []})
    return observations


def _linked_references(paper):
    links = set()
    for reference in paper['reference_evidence']:
        links.update(reference.get('linked_urls') or [])
        url = reference.get('extra', {}).get('publication_identity_url')
        if url:
            links.add(url)
    return [{'candidate_ids': [paper['candidate_id']], 'url': url, 'source_role': 'linked_reference',
             'access_status': 'link_only_not_publication_confirmation', 'metadata': {}, 'notes': []}
            for url in sorted(links) if 'arxiv.org/' not in url]


def _blank_review():
    fields = {name: '' for name in REVIEW_COLUMNS}
    fields.update(review_status='candidate', ownership='uncertain', inclusion_decision='pending',
                  use_suggested_metadata=False)
    return fields


def build_grouped_review(dataset, relationships, source_pack):
    if len(dataset['scope']['professors']) != 1:
        raise ValueError('Publication review requires a single professor scope')
    if any(paper['review_status'] != 'candidate' for paper in dataset['papers']):
        raise ValueError('Build automatic suggestions from the untouched candidate snapshot')
    papers = {paper['candidate_id']: paper for paper in dataset['papers']}
    if any(key not in papers for relation in relationships for key in relation['candidate_ids']):
        raise ValueError('Relationships must reference existing candidate IDs')
    observations = list(source_pack.get('observations', []))
    for paper in papers.values():
        observations.extend(_cached_observations(paper))
        observations.extend(_linked_references(paper))
    professor = dataset['scope']['professors'][0]
    groups = []
    attached_preprints = set()
    for ids in _primary_groups(list(papers.values()), relationships):
        members = [papers[key] for key in ids]
        evidence = [item for item in observations if set(item.get('candidate_ids', [])) & set(ids)]
        official = [item for item in evidence if item.get('source_role') == 'formal_publication'
                    and item.get('access_status') == 'metadata_observed' and item.get('metadata', {}).get('authors')]
        detailed = [item for item in evidence if item.get('metadata', {}).get('authors')
                    and item.get('metadata', {}).get('title')]
        preferred = official[0] if official else (detailed[0] if detailed else None)
        primary = preferred['metadata'] if preferred else members[0]
        metadata = {field: deepcopy(primary.get(field)) for field in ('title', 'authors', 'year', 'doi', 'venue', 'publication_type')}
        preprints = set()
        relations = []
        for relation in relationships:
            if not set(relation['candidate_ids']) & set(ids):
                continue
            relations.append(deepcopy(relation))
            for other in relation['candidate_ids']:
                if papers[other]['publication_type'] == 'preprint':
                    preprints.add(other)
        attached_preprints.update(preprints)
        questions = []
        if not official:
            questions.append('尚未取得正式出版物的完整论文级元数据：核对归档全文、完整署名、类型与年份。')
        if any(normalize(member['title']) != normalize(metadata['title']) for member in members):
            questions.append('原书目题名与官方题名不同：核对后更正，旧题名别名需单独人工批准。')
        if any(member['publication_type'] != metadata['publication_type'] for member in members):
            questions.append('出版类型有差异：以正式出版记录裁决，不将会议展示和期刊文章混为同一类型。')
        if any(not member.get('authors') for member in members):
            questions.append('原记录缺少作者，不能把出版社、报告组织或贡献者当成论文作者。')
        if any(item['suggested_relation'] == 'distinct_publication_versions_check' for item in relations):
            questions.append('存在会议与期刊等不同正式版本：保留独立出版身份，关联不是合并许可。')
        if len(ids) > 1:
            questions.append('本组是重复书目审核建议；人工核实所有成员为同一出版物后才能使用同一 canonical group。')
        background = []
        for member in members:
            if member.get('revision_provenance'):
                questions.extend(member.get('review_warnings') or [])
            for reference in member['reference_evidence']:
                claim = reference.get('extra', {}).get('secondary_journal_claim')
                if claim:
                    background.append({'earlier_publication': deepcopy(reference['metadata']),
                                       'secondary_journal_claim': deepcopy(claim),
                                       'source_url': reference['source_url'], 'review_status': 'candidate'})
        for item in evidence:
            questions.extend(item.get('notes') or [])
        key = hashlib.sha256('\n'.join(sorted(ids)).encode('utf-8')).hexdigest()[:16]
        core = metadata['publication_type'] in dataset['scope']['core_publication_types']
        recommendation = '建议审核后纳入核心正式论文集合' if core and official else '先补充正式出版证据再决定'
        if not core:
            recommendation = '建议保留为关联/其他出版物，暂不进入核心正例分母'
        groups.append({'group_id': 'publication_' + key, 'candidate_ids': sorted(ids),
                       'suggested_metadata': metadata, 'metadata_source_url': preferred['url'] if preferred else None,
                       'official_evidence': evidence, 'professor_bibliography_url': professor.get('bibliography_url'),
                       'preprint_candidate_ids': sorted(preprints), 'relationship_suggestions': relations,
                       'suggested_judgment': recommendation,
                       'judgment_basis': '正式来源元数据和独立教授书目支持；仍需人工身份与版本审核。' if official else '当前只有书目、日程、仓储或其他有限证据。',
                       'specific_questions': list(dict.fromkeys(questions)), 'review_status': 'candidate'})
        groups[-1]['earlier_publication_context'] = background
    # A shared preprint is attached to both formal versions for navigation only; it never unions them.
    standalone = [key for key, paper in papers.items() if paper['publication_type'] == 'preprint' and key not in attached_preprints]
    order = {kind: index for index, kind in enumerate(dataset['scope']['core_publication_types'] + ['workshop_paper', 'unknown', 'other'])}
    groups.sort(key=lambda item: (order.get(item['suggested_metadata']['publication_type'], len(order)),
                                 item['suggested_metadata']['year'] or 0, item['suggested_metadata']['title']))
    for group in groups:
        linked_ids = {key for relation in group['relationship_suggestions'] for key in relation['candidate_ids']}
        group['related_formal_group_ids'] = [other['group_id'] for other in groups if other is not group and
                                            (set(other['candidate_ids']) & linked_ids or
                                             set(other['preprint_candidate_ids']) & set(group['preprint_candidate_ids']))]
    return {'dataset_id': dataset['dataset_id'], 'source_scope': 'independent_official_references',
            'professor': professor, 'candidate_count': len(papers), 'groups': groups,
            'standalone_preprint_ids': standalone, 'preprint_records': [deepcopy(paper) for paper in papers.values()
                                                                       if paper['publication_type'] == 'preprint'],
            'papers': deepcopy(dataset['papers']), 'human_confirmed_count': 0, 'complete_positive_list': False,
            'note': 'Display grouping only. No candidate records, publication identities or human decisions are merged.'}


def review_template(grouped):
    return {'dataset_id': grouped['dataset_id'],
            'grouped_review_sha256': fingerprint(grouped),
            'groups': [{'group_id': group['group_id'], 'candidate_ids': group['candidate_ids'],
                        'title': group['suggested_metadata']['title'],
                        'suggested_metadata': deepcopy(group['suggested_metadata']),
                        'specific_questions': deepcopy(group['specific_questions']),
                        'human_review': _blank_review()} for group in grouped['groups']],
            'preprint_reviews': [{'candidate_id': paper['candidate_id'], 'title': paper['title'],
                                 'year': paper['year'], 'authors': deepcopy(paper['authors']), 'human_review': _blank_review()}
                                for paper in grouped['preprint_records']]}


def annotation_rows(dataset, grouped, annotations):
    if annotations.get('dataset_id') != dataset['dataset_id'] or grouped['dataset_id'] != dataset['dataset_id']:
        raise ValueError('Group annotations belong to a different dataset')
    if grouped['papers'] != dataset['papers'] or annotations.get('grouped_review_sha256') != fingerprint(grouped):
        raise ValueError('Group annotations are stale or candidate/evidence facts changed')
    expected = {group['group_id']: group for group in grouped['groups']}
    reviews = {}
    seen = set()
    for entry in annotations.get('groups', []):
        key = entry['group_id']
        if key not in expected or key in seen or entry['candidate_ids'] != expected[key]['candidate_ids']:
            raise ValueError('Unknown, duplicated or changed publication review group')
        seen.add(key)
        review = deepcopy(entry['human_review'])
        if review.get('use_suggested_metadata') is True:
            if review.get('review_status') != 'confirmed':
                raise ValueError('Suggested metadata cannot be approved without an explicit human confirmed decision')
            metadata = expected[key]['suggested_metadata']
            for field in ('title', 'year', 'doi', 'publication_type', 'venue'):
                review['reviewed_' + field] = 'null' if field == 'doi' and metadata[field] is None else str(metadata[field] or '')
            review['reviewed_authors_json'] = json.dumps(metadata['authors'], ensure_ascii=False)
        for candidate_id in expected[key]['candidate_ids']:
            reviews[candidate_id] = review
    expected_preprints = {paper['candidate_id'] for paper in grouped['preprint_records']}
    seen_preprints = set()
    for entry in annotations.get('preprint_reviews', []):
        key = entry['candidate_id']
        if key not in expected_preprints or key in seen_preprints:
            raise ValueError('Unknown or duplicated preprint review')
        seen_preprints.add(key)
        review = deepcopy(entry['human_review'])
        if review.get('use_suggested_metadata') is True:
            raise ValueError('Preprint metadata must be reviewed separately from formal publication suggestions')
        reviews[key] = review
    rows = []
    for paper in dataset['papers']:
        row = {field: '' if paper.get(field) is None else str(paper.get(field, '')) for field in FACT_COLUMNS}
        for field, source in (('authors_json', 'authors'), ('source_urls_json', 'source_urls'), ('review_warnings_json', 'review_warnings')):
            row[field] = json.dumps(paper[source], ensure_ascii=False)
        row.update(_blank_review())
        row.update(reviews.get(paper['candidate_id'], {}))
        row.pop('use_suggested_metadata', None)
        for key, value in row.items():
            if isinstance(value, bool):
                row[key] = 'true' if value else 'false'
        rows.append(row)
    validate_reviews(dataset, rows)
    return rows


def render_markdown(grouped):
    papers = {paper['candidate_id']: paper for paper in grouped['papers']}
    lines = ['# {} 按正式出版物分组审核'.format(grouped['professor']['name']), '',
             '{} 条候选，{} 个非预印本审核组；全部 candidate。分组不构成确认或去重结果。'.format(
                 grouped['candidate_count'], len(grouped['groups'])), '',
             '正式会议/期刊在前，workshop 和其他出版物在后。共享预印本只作关联，各正式版本独立展示。', '',
             '教授书目：[统一核查入口]({})。本页集中呈现论文级来源事实，先阅读证据和问题，仅在有疑问时打开对应来源。'.format(
                 grouped['professor']['bibliography_url']), '', '## 分组索引', '']
    if grouped['professor'].get('identity_risks'):
        lines.insert(-2, '身份注意：' + '; '.join(grouped['professor']['identity_risks']) + ' 此为书目范围警示，不表示每篇论文都冲突。')
    for index, group in enumerate(grouped['groups'], 1):
        m = group['suggested_metadata']
        lines.append('{}. [{} — {} {}](#{})'.format(index, m['title'], m['year'], m['publication_type'], group['group_id']))
    for index, group in enumerate(grouped['groups'], 1):
        m = group['suggested_metadata']
        lines.extend(['', '<a id="{}"></a>'.format(group['group_id']), '', '## {}. {}'.format(index, m['title']), '',
                      '**建议出版类型：{}；出版年份：{}；来源：{}。**'.format(m['publication_type'], m['year'], m['venue'] or '未报告'), '',
                      '完整署名（来源原样）：' + ('; '.join(m['authors'] or []) or '缺失，需核查；不推断姓名'), '',
                      '署名/题名来源：' + ('[论文级来源]({})'.format(group['metadata_source_url']) if group['metadata_source_url'] else '现有书目声明，正式出版元数据尚不足'), '',
                      'DOI：' + (m['doi'] or '未报告，不代表不存在'), '',
                      '**自动建议：{}。** {}'.format(group['suggested_judgment'], group['judgment_basis']), '',
                      '教授书目：[出版列表]({})'.format(group['professor_bibliography_url']), '', '### 官方及独立证据', ''])
        displayed = set()
        for evidence in group['official_evidence']:
            signature = (evidence['url'], evidence['source_role'], evidence['access_status'])
            if signature in displayed:
                continue
            displayed.add(signature)
            observed = evidence.get('metadata') or {}
            lines.append('- [{}]({})：{}；{}；{}；作者 {}。'.format(evidence['source_role'], evidence['url'],
                         observed.get('title', '元数据未取得'), observed.get('year', '年份未报告'),
                         evidence['access_status'], '; '.join(observed.get('authors') or [])))
        if not group['official_evidence']:
            lines.append('- 正式出版链接/元数据未取得；按教授书目中的发表来源核查，不能据此确认完整论文。')
        lines.extend(['', '### 本组原始记录', '', '| 候选 ID | 原题名 | 原年份/类型 |', '| --- | --- | --- |'])
        for key in group['candidate_ids']:
            paper = papers[key]
            lines.append('| `{}` | {} | {} / {} |'.format(key, paper['title'].replace('|', '\\|'), paper['year'], paper['publication_type']))
        lines.extend(['', '### 预印本关系与其他正式版本', ''])
        for key in group['preprint_candidate_ids']:
            paper = papers[key]
            lines.append('- `{}`：{}；{}；署名 {}；来源 {}。关联仅为 candidate，预印本单独审核。'.format(
                key, paper['title'], paper['year'], '; '.join(paper['authors']), ' '.join('<{}>'.format(url) for url in set(paper['source_urls']))))
        if not group['preprint_candidate_ids']:
            lines.append('- 已有候选中没有取得相关预印本；不补造记录或链接。')
        for other_id in group['related_formal_group_ids']:
            other = next(item for item in grouped['groups'] if item['group_id'] == other_id)
            other_meta = other['suggested_metadata']
            lines.append('- 关联独立出版/展示组：[{}，{}，{}](#{})。分别审核，不沿共享预印本合并。'.format(
                other_meta['title'], other_meta['year'], other_meta['publication_type'], other_id))
        for background in group['earlier_publication_context']:
            old = background['earlier_publication']
            lines.append('- [教授书目旧条目]({})：{}；{}；{}；作者 {}。条目下的期刊补充声明：{}。这是版本背景，不是本组期刊署名。'.format(
                background['source_url'], old['title'], old['year'], old['publication_type'],
                '; '.join(old['authors']), json.dumps(background['secondary_journal_claim'], ensure_ascii=False)))
        lines.extend(['', '### 具体待核查事项', ''])
        for question in group['specific_questions']:
            lines.append('- ' + question)
        if not group['specific_questions']:
            lines.append('- 核对目标教授身份、正式出版类型及作者署名；自动证据不代替人工确认。')
        lines.extend(['', '`{}`：在 group_annotations.template.json 的副本中填写人工决定。人工字段目前为空。'.format(group['group_id'])])
    if grouped['standalone_preprint_ids']:
        lines.extend(['', '## 未关联到正式出版组的预印本', ''])
        for key in grouped['standalone_preprint_ids']:
            paper = papers[key]
            lines.append('- `{}`：{}，{}；作者 {}；{}。仅候选，不计正式正例。'.format(
                key, paper['title'], paper['year'], '; '.join(paper['authors']), ' '.join(paper['source_urls'])))
    return '\n'.join(lines) + '\n'


def write_review(grouped, output_dir):
    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise ValueError('Use an empty output directory; review files must not be overwritten')
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / 'grouped_review.json', grouped)
    write_json(root / 'group_annotations.template.json', review_template(grouped))
    (root / 'grouped_review.md').write_text(render_markdown(grouped), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--relationships', type=Path)
    parser.add_argument('--sources', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--grouped-review', type=Path)
    parser.add_argument('--group-annotations', type=Path)
    parser.add_argument('--annotations-output', type=Path)
    args = parser.parse_args(argv)
    try:
        dataset = read_json(args.candidates)
        if args.group_annotations:
            if not args.grouped_review or not args.annotations_output:
                raise ValueError('Annotation expansion needs --grouped-review and --annotations-output')
            rows = annotation_rows(dataset, read_json(args.grouped_review), read_json(args.group_annotations))
            with args.annotations_output.open('x', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=FACT_COLUMNS + REVIEW_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            print('Expanded explicit human annotations; no decisions inferred')
        else:
            if not args.output_dir or not args.relationships or not args.sources:
                raise ValueError('Review preparation needs --output-dir, --relationships and --sources')
            grouped = build_grouped_review(dataset, read_json(args.relationships)['suggestions'], read_json(args.sources))
            write_review(grouped, args.output_dir)
            print('Prepared {} candidate publication review groups; confirmed=0'.format(len(grouped['groups'])))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
