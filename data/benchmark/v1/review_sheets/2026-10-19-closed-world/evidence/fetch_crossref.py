import csv, glob, json, os, sys, time, urllib.request, urllib.parse, concurrent.futures as cf
out = sys.argv[1]
cache = json.load(open(out)) if os.path.exists(out) else {}
dois = set()
for f in glob.glob('data/benchmark/v1/review_sheets/2026-10-19-closed-world/*.csv'):
    for r in csv.DictReader(open(f, encoding='utf-8-sig')):
        flags = r['review_flags']
        if r['row_kind'] == 'system_suggestion' or 'identifier_from_title_lookup' in flags or 'target_name' in flags or 'byline' in flags:
            for d in r['dois'].split('; '):
                if d: dois.add(d.lower())
todo = sorted(d for d in dois if d not in cache)
print(len(dois), 'dois,', len(todo), 'to fetch', flush=True)
def get(doi):
    url = 'https://api.crossref.org/works/' + urllib.parse.quote(doi, safe='')
    req = urllib.request.Request(url, headers={'User-Agent': 'paper-author-searcher-benchmark-review/1.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                m = json.load(resp)['message']
            return doi, {'type': m.get('type'), 'title': (m.get('title') or [''])[0], 'year': (m.get('issued', {}).get('date-parts') or [[None]])[0][0],
                         'container': (m.get('container-title') or [''])[0], 'publisher': m.get('publisher'),
                         'authors': [{'given': a.get('given', ''), 'family': a.get('family', ''), 'name': a.get('name', ''), 'orcid': a.get('ORCID', ''),
                                      'aff': [x.get('name', '') for x in a.get('affiliation', [])]} for a in m.get('author', [])],
                         'relation': list((m.get('relation') or {}).keys())}
        except urllib.error.HTTPError as e:
            if e.code == 404: return doi, {'error': 404}
            time.sleep(2)
        except Exception as e:
            time.sleep(2)
    return doi, {'error': 'failed'}
with cf.ThreadPoolExecutor(4) as pool:
    for i, (doi, rec) in enumerate(pool.map(get, todo)):
        cache[doi] = rec
        if i % 50 == 0: print(i, flush=True); json.dump(cache, open(out, 'w'))
json.dump(cache, open(out, 'w'))
print('errors', sum(1 for v in cache.values() if 'error' in v))
