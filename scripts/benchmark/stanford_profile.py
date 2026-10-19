"""Parse the "All Publications" tab of a Stanford Profiles page into a ``manual`` snapshot.

Stanford maintains these lists (from PubMed/Web of Science claims); each entry has a year line ("2025; 15 (1): 21-33")
and usually a DOI/PubMed ID. Usage: stanford_profile.py PROFILE_URL START_YEAR END_YEAR OUT_JSON
"""

import html
import json
import re
import sys
import urllib.request
from datetime import date

YEAR_LINE = re.compile(r'^((?:19|20)\d{2})(?:;|$)')
DETAIL = re.compile(r'^View details for (DOI|PubMedID) (\S+)')


def page_lines(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(request, timeout=30).read().decode('utf-8', 'replace')
    text = re.sub(r'<script.*?</script>|<style.*?</style>', '', raw, flags=re.S)
    text = re.sub(r'<br\s*/?>|</p>|</li>|</h\d>|</div>', '\n', text)
    text = html.unescape(re.sub(r'<[^>]+>', '', text))
    lines = [' '.join(line.split()) for line in text.split('\n')]
    lines = [line for line in lines if line]
    start = next((index for index, line in enumerate(lines) if line == 'All Publications'), None)
    if start is None:
        raise SystemExit('No "All Publications" section found')
    return lines[start + 1 :]


def authors(line):
    # "Krishnan, S. R., Bochenek, M. A., Langer, R." -> ["S. R. Krishnan", ...]
    parts = [part.strip() for part in line.split(',')]
    names = []
    for family, given in zip(parts[0::2], parts[1::2], strict=False):
        names.append((given + ' ' + family).strip())
    return names


def main(argv):
    url, start, end, out = argv[1], int(argv[2]), int(argv[3]), argv[4]
    lines = page_lines(url)
    boundaries = {-1}
    entries = []
    for index, line in enumerate(lines):
        # An entry is preceded by the previous entry's detail links or, failing those, its abstract text.
        if line.startswith('View details for ') or (index and lines[index - 1] == 'Abstract'):
            boundaries.add(index)
        match = YEAR_LINE.match(line)
        if not match or index < 2 or ',' not in lines[index - 1]:
            continue
        has_venue = (index - 4) in boundaries or index - 3 <= 0
        title_index = index - 3 if has_venue else index - 2
        year = int(match.group(1))
        item = {
            'title': lines[title_index].rstrip('.'),
            'year': year,
            'type': None,
            'venue': lines[index - 2] if has_venue else None,
            'authors': authors(lines[index - 1]),
        }
        for follow in lines[index + 1 : index + 40]:
            if YEAR_LINE.match(follow):
                break
            detail = DETAIL.match(follow)
            if detail and detail.group(1) == 'DOI':
                item.setdefault('doi', detail.group(2))
            elif detail:
                item.setdefault('pmid', detail.group(2))
        if start <= year <= end:
            entries.append(item)
    snapshot = {
        'snapshot_schema_version': 1,
        'source_url': url,
        'captured_at': date.today().isoformat(),
        'captured_by': 'Script parsing of the Stanford Profiles publication tab; pending human review',
        'scope_note': f'Stanford Profiles "All Publications", {start}-{end}',
        'entries': entries,
    }
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    print(out, len(entries))


if __name__ == '__main__':
    main(sys.argv)
