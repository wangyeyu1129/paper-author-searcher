"""Assistant decisions for the leads added to the two closed-world truths (Wei Wang, Raj Chetty), 2026-10-19.

Truth rows keep the decisions already recorded (prefilled); only leads are decided, with the 2026-10-19 rules.
"""
import csv, glob, json, re, sys, difflib, collections
S, FOLDER = sys.argv[1], sys.argv[2]
A = {(x['pid'], x['row']): x for x in json.load(open(S + '/analysis_cw.json'))}
B, U, N = 'belongs', 'uncertain', 'not_belongs'
# Names with no known namesake publishing in the same field; a Crossref byline plus a consistent topic is enough.
DISTINCTIVE = {'cs_graham_neubig', 'cs_christopher_manning', 'eng_daniela_rus', 'cs_kevin_ellis', 'bio_brian_hie',
               'cs_julie_kallini', 'soc_nina_roussille', 'bio_anshul_kundaje', 'bio_jennifer_doudna', 'bio_marena_trinidad',
               'soc_raj_chetty'}
# Common names: an institution alone is not enough, the affiliation must name the researcher's own unit.
STRICT_AFF = {'cs_wei_wang_ucla': r'computer science', 'cs_yang_liu_tsinghua': r'computer science|artificial intelligence', 'bio_feng_zhang': r'mcgovern|brain and cognitive',
              'cs_huan_zhang': r'electrical and computer|computer science', 'soc_xiang_zhou': r'sociology',
              'soc_yu_xie': r'sociology|social research', 'eng_wei_lu': r'electrical engineering', 'eng_lu_lu': r'yale|pennsylvania',
              'eng_min_zhu': r'yale'}
NAMESAKE = 'Different field and unit, no shared coauthors: a namesake'
OUT_OF_SCOPE_TYPES = {'dataset', 'Dataset', 'Model', 'Software', 'monograph', 'book'}
def nt(s): return ' '.join(re.sub(r'[^a-z0-9 ]', ' ', s.lower()).split())
TRUTH = {  # truth-row overrides: row -> (decision, note) ; ('same_as', row)
 'bio_anshul_kundaje': {5: (U, 'Author correction notice, not a publication'), 37: (U, 'Author correction notice, not a publication'),
                        55: (U, 'Publisher correction notice, not a publication'), 60: ('same_as', 45)},
 'bio_brian_hie': {9: ('same_as', 12), 19: ('same_as', 24)},
 'bio_jennifer_doudna': {5: (U, 'Author correction notice, not a publication'), 6: (U, 'Author correction notice, not a publication'),
                         7: ('same_as', 8), 26: ('same_as', 44), 28: ('same_as', 29)},
 'cs_kevin_ellis': {5: (U, 'Monograph (book), excluded type')},
 'eng_yi_cui': {20: (U, 'Parse artefact: title "E.C", DOI is a 2012 Nature Materials paper outside the window')},
}
LEADS = {
 'cs_wei_wang_ucla': {r: (N, NAMESAKE) for r in (44, 45, 46, 47, 120)},
 'soc_raj_chetty': {18: (U, 'Edited NBER volume (book), excluded type'),
                    24: (U, 'Edited NBER volume (book), excluded type'),
                    32: (U, 'Introduction to an edited volume, not a research publication')},
}
# Wei Wang has been at UCLA throughout the 2024-2025 window: a paper-level affiliation elsewhere is a namesake.
SINGLE_INSTITUTION = {'cs_wei_wang_ucla': r'ucla|los angeles'}
summary = collections.Counter()
for path in sorted(glob.glob(FOLDER + '/*.csv')):
    pid = path.split('/')[-1][:-4]
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig', newline='')))
    kept_leads = []  # (normalized title, row index) of leads accepted so far, to catch versions among leads
    for i, r in enumerate(rows):
        x = A[(pid, i)]
        if r['row_kind'] == 'truth' and r['decision']:
            summary[(r['row_kind'], r['decision'].split(':')[0])] += 1
            continue
        if r['row_kind'] == 'truth':
            choice = TRUTH.get(pid, {}).get(i)
            if choice is None:
                note = ''
                if 'target_name_not_found' in r['review_flags'] or 'byline_unavailable' in r['review_flags']:
                    note = 'Crossref author list names the researcher' if x['cr_name_hit'] else 'Listed on the researcher-maintained bibliography'
                choice = (B, note)
        else:
            choice = LEADS.get(pid, {}).get(i)
            title = r['title']
            if choice is None and (set(x['cr_types']) & OUT_OF_SCOPE_TYPES or 'dataset' in r['work_types']):
                choice = (U, 'Dataset/software record, excluded type')
            if choice is None and re.search(r'(?i)^(abstract \d+|bps20\d\d|\d+ |(author|publisher) correction)', title):
                choice = (U, 'Meeting abstract or correction notice, not a publication')
            if choice is None and x.get('version_of'):
                sim, eid, _ = x['version_of']
                choice = ('same_as:' + eid, 'Another version of a truth entry (title similarity %.2f)' % sim)
            if choice is None:
                twin = next((j for t, j in kept_leads if difflib.SequenceMatcher(None, t, nt(title)).ratio() >= 0.9), None)
                if twin is not None:
                    choice = (U, 'Another version of lead row %d' % twin)
            if choice is None:
                ev = []
                if x['cr_orcid']: ev.append("Crossref byline carries the researcher's ORCID")
                strict = STRICT_AFF.get(pid)
                if x['aff_match'] and (not strict or any(re.search(strict, a.lower()) for a in x['affs'])): ev.append('Crossref affiliation: ' + x['affs'][0][:80])
                co, n = x['shared_coauthors'], x['n_authors']
                if co >= 2 or (co >= 1 and n <= 15): ev.append('%d coauthors shared with the researcher-maintained bibliography' % co)
                if not ev and x['cr_name_hit'] and pid in DISTINCTIVE:
                    ev.append('Crossref/DataCite byline names the researcher; no namesake in the field; topic matches')
                if not ev and not x['cr_name_hit'] and co >= 2:
                    ev.append('%d coauthors shared with the researcher-maintained bibliography' % co)
                elsewhere = SINGLE_INSTITUTION.get(pid)
                if not ev and elsewhere and x['affs'] and not any(re.search(elsewhere, a.lower()) for a in x['affs']):
                    choice = (N, 'Crossref affiliation ' + x['affs'][0][:70] + ': a namesake')
                else:
                    choice = (B, '; '.join(ev)) if ev else (U, 'No independent evidence beyond the system byline')
            if choice[0] == B:
                kept_leads.append((nt(title), i))
        decision, note = choice
        if decision == 'same_as' and r['row_kind'] == 'truth':
            decision, note = U, 'Another version of row %d' % note
        elif decision == 'same_as':
            decision, note = 'same_as:' + rows[note]['evaluation_id'], 'Another version of row %d' % note
        r['decision'], r['reviewer_note'] = decision, note
        summary[(r['row_kind'], decision.split(':')[0])] += 1
    with open(path, 'w', encoding='utf-8-sig', newline='') as h:
        w = csv.DictWriter(h, rows[0].keys()); w.writeheader(); w.writerows(rows)
for k, v in sorted(summary.items()): print(k, v)
