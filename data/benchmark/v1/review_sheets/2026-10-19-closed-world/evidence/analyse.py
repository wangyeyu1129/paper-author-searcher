import csv, glob, json, re, sys, difflib, unicodedata, collections
S = sys.argv[1]
cr = json.load(open(S + '/crossref.json'))
roster = {p['person_id']: p for p in json.load(open('data/benchmark/v1/roster.json'))['people']}
AFF = {
 'cs_wei_wang_ucla': ['ucla', 'los angeles'], 'soc_raj_chetty': ['harvard', 'opportunity insights', 'nber', 'national bureau', 'stanford'],
 'bio_anshul_kundaje': ['stanford'], 'bio_brian_hie': ['stanford', 'arc institute', 'massachusetts institute', 'mit', 'berkeley', 'evo'],
 'bio_jennifer_doudna': ['berkeley', 'gladstone', 'innovative genomics', 'howard hughes', 'lawrence berkeley'],
 'bio_marena_trinidad': ['berkeley', 'gladstone', 'innovative genomics'], 'cs_christopher_manning': ['stanford'],
 'cs_graham_neubig': ['carnegie mellon', 'all hands', 'cmu'], 'cs_julie_kallini': ['stanford'], 'cs_kevin_ellis': ['cornell'],
 'eng_daniela_rus': ['massachusetts institute', 'mit', 'csail'], 'eng_lu_lu': ['yale', 'pennsylvania', 'massachusetts institute', 'brown'],
 'eng_wei_lu': ['michigan'], 'soc_christian_wolf': ['massachusetts institute', 'mit', 'federal reserve', 'chicago', 'nber', 'national bureau'],
 'soc_nina_roussille': ['massachusetts institute', 'mit', 'london school', 'berkeley', 'nber'],
 'bio_feng_zhang': ['broad institute', 'mcgovern', 'massachusetts institute', 'mit', 'howard hughes'],
 'cs_huan_zhang': ['illinois', 'uiuc', 'carnegie mellon', 'ucla', 'los angeles'], 'cs_yang_liu_tsinghua': ['tsinghua'],
 'eng_min_zhu': ['yale'], 'soc_xiang_zhou': ['harvard'], 'soc_yu_xie': ['princeton', 'peking', 'michigan'],
}
def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9 ]+', ' ', s).split()
def ntitle(s): return ' '.join(norm(s))
def name_key(full):
    # Full first + last name: initials collide too often for common names (Zhang Y., Liu Y.).
    full = ' '.join(reversed(full.split(','))) if ',' in full else full
    t = [w for w in norm(full) if len(w) > 1]
    return (t[-1], t[0]) if len(t) >= 2 else None
def target_keys(pid):
    n = roster[pid]['search_input']['name']; t = norm(n)
    keys = {(t[-1], t[0][0]), (t[-1], t[0])}
    if pid == 'bio_zhiwei_xue': keys.add(('xue', 'w'))
    return keys
def cr_author_match(rec, pid):
    tk = target_keys(pid); hits = []
    for a in rec.get('authors', []):
        fam = norm(a['family']) or norm(a['name'])[-1:]
        giv = norm(a['given']) or norm(a['name'])[:1]
        if fam and giv and (fam[-1], giv[0][0]) in tk: hits.append(a)
        elif fam and not giv and a.get('name') and name_key(a['name']) in tk: hits.append(a)
    return hits
rows_out = []
for f in sorted(glob.glob(sys.argv[2] + '/*.csv')):
    pid = f.split('/')[-1][:-4]
    rows = list(csv.DictReader(open(f, encoding='utf-8-sig')))
    truth = [r for r in rows if r['row_kind'] == 'truth']
    tk = target_keys(pid)
    co = collections.Counter()
    for r in truth:
        for a in r['authors'].split('; '):
            k = name_key(a)
            if k and k not in tk: co[k] += 1
    tt = [(ntitle(r['title']), r) for r in truth]
    for i, r in enumerate(rows):
        info = {'pid': pid, 'row': i, 'kind': r['row_kind'], 'flags': r['review_flags'], 'year': r['year'], 'title': r['title'][:110], 'types': r['work_types'], 'venue': r['venue'][:60], 'dois': r['dois']}
        recs = [cr.get(d.lower()) for d in r['dois'].split('; ') if d]
        recs = [x for x in recs if x and 'error' not in x]
        orc = roster[pid].get('orcid') or ''
        info['cr_types'] = sorted({x.get('type') for x in recs if x.get('type')})
        hits = [h for x in recs for h in cr_author_match(x, pid)]
        info['cr_has_authors'] = any(x.get('authors') for x in recs)
        info['cr_name_hit'] = bool(hits)
        info['cr_orcid'] = any(orc and orc in (h.get('orcid') or '') for h in hits)
        info['cr_other_orcid'] = [h['orcid'] for h in hits if h.get('orcid') and orc not in h['orcid']]
        affs = [a for h in hits for a in h['aff']]
        info['affs'] = affs[:3]
        info['aff_match'] = any(any(re.search(r'\b' + re.escape(k) + r'\b', a.lower()) for k in AFF.get(pid, [])) for a in affs)
        info['cr_title_sim'] = max([difflib.SequenceMatcher(None, ntitle(x['title']), ntitle(r['title'])).ratio() for x in recs] or [None]) if recs else None
        info['cr_years'] = sorted({x.get('year') for x in recs})
        if r['row_kind'] == 'system_suggestion':
            mine = ntitle(r['title'])
            best = max(((difflib.SequenceMatcher(None, mine, t).ratio(), tr['evaluation_id'], tr['title'][:60]) for t, tr in tt), default=(0, '', ''))
            info['version_of'] = best if best[0] >= 0.85 else None
            auth = r['authors'].split('; ')
            ks = {k for k in (name_key(a) for a in auth) if k and (k[0], k[1][0]) not in tk and k not in tk}
            info['shared_coauthors'] = sum(1 for k in ks if co[k])
            info['n_authors'] = len(auth)
        rows_out.append(info)
json.dump(rows_out, open(S + '/analysis_cw.json', 'w'), indent=0)
print(len(rows_out))
