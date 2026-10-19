import json, sys, time, urllib.request, urllib.parse
path = sys.argv[1]; c = json.load(open(path))
for doi, v in list(c.items()):
    if 'error' not in v: continue
    url = 'https://api.datacite.org/dois/' + urllib.parse.quote(doi, safe='')
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'benchmark-review'}), timeout=30) as r:
            a = json.load(r)['data']['attributes']
        c[doi] = {'source': 'datacite', 'type': (a.get('types') or {}).get('resourceTypeGeneral'), 'title': (a.get('titles') or [{}])[0].get('title', ''),
                  'year': a.get('publicationYear'), 'container': a.get('publisher'),
                  'authors': [{'given': x.get('givenName', ''), 'family': x.get('familyName', ''), 'name': x.get('name', ''),
                               'orcid': ' '.join(n.get('nameIdentifier', '') for n in x.get('nameIdentifiers', [])),
                               'aff': [y if isinstance(y, str) else y.get('name', '') for y in x.get('affiliation', [])]} for x in a.get('creators', [])],
                  'relation': [r.get('relationType') + ':' + r.get('relatedIdentifier', '') for r in a.get('relatedIdentifiers', [])]}
    except Exception as e:
        c[doi] = {'error': str(e)[:60]}
    time.sleep(0.2)
json.dump(c, open(path, 'w'))
print('errors', [(k, v['error']) for k, v in c.items() if 'error' in v])
