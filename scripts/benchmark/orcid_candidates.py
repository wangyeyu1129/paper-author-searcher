"""Find roster candidates on ORCID: current role titles from employments and work counts in a window.

Usage: orcid_candidates.py 'ORCID solr query' [rows] [start_year] [end_year]
Example: orcid_candidates.py 'family-name:Wang AND affiliation-org-name:"Johns Hopkins University"' 20
"""

import json
import sys
import urllib.parse
import urllib.request

API = 'https://pub.orcid.org/v3.0'
HEADERS = {'Accept': 'application/json', 'User-Agent': 'paper-author-searcher-benchmark'}


def get(path):
    request = urllib.request.Request(API + path, headers=HEADERS)
    return json.load(urllib.request.urlopen(request, timeout=30))


def current_roles(orcid):
    roles = []
    for group in get(f'/{orcid}/employments').get('affiliation-group') or []:
        for summary in group.get('summaries') or []:
            item = summary.get('employment-summary') or {}
            if item.get('end-date') is None:
                roles.append('{} @ {}'.format(item.get('role-title'), (item.get('organization') or {}).get('name')))
    return roles


def window_works(orcid, start, end):
    count = 0
    for group in get(f'/{orcid}/works').get('group') or []:
        year = ((group['work-summary'][0].get('publication-date') or {}).get('year') or {}).get('value')
        if year and start <= int(year) <= end:
            count += 1
    return count


def main(argv):
    query = argv[1]
    rows = int(argv[2]) if len(argv) > 2 else 15
    start, end = (int(argv[3]), int(argv[4])) if len(argv) > 4 else (2020, 2025)
    results = get(f'/expanded-search/?rows={rows}&q={urllib.parse.quote(query)}')
    for result in results.get('expanded-result') or []:
        orcid = result['orcid-id']
        try:
            roles, works = current_roles(orcid), window_works(orcid, start, end)
        except Exception as exc:  # report and keep scanning
            roles, works = [f'error: {exc}'], -1
        if works:
            print(
                '{} {} {} | works {}-{}: {} | {}'.format(
                    orcid,
                    result.get('given-names'),
                    result.get('family-names'),
                    start,
                    end,
                    works,
                    '; '.join(roles)[:200],
                )
            )


if __name__ == '__main__':
    main(sys.argv)
