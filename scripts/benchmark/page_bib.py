"""Extract BibTeX blocks embedded in a page (HTML or JS). argv: url out.bib start end"""

import html
import re
import sys
import urllib.request
from pathlib import Path

url, out, start, end = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
raw = (
    urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30)
    .read()
    .decode('utf-8', 'replace')
)
text = html.unescape(re.sub(r'<[^>]+>', '', raw)) if '<html' in raw[:2000].lower() else raw
text = text.replace('\\n', '\n').replace('\\"', '"')
pat = re.compile(
    r'@(inproceedings|article|misc|inbook|incollection|techreport|phdthesis|book|unpublished|online)\s*\{', re.I
)
entries, seen = [], set()
for m in pat.finditer(text):
    depth, j = 0, m.end() - 1
    while j < len(text):
        depth += {'{': 1, '}': -1}.get(text[j], 0)
        if depth == 0:
            break
        j += 1
    block = text[m.start() : j + 1]
    y = re.search(r'year\s*=\s*[{"]?\s*(\d{4})', block, re.I)
    key = re.sub(r'\s+', ' ', block)
    if y and start <= int(y.group(1)) <= end and key not in seen:
        seen.add(key)
        entries.append(block)
Path(out).write_text(
    f'%% BibTeX embedded in {url} (captured 2026-10-18); entries dated {start}-{end}.\n\n'
    + '\n\n'.join(entries)
    + '\n',
    encoding='utf-8',
)
print(out, len(entries))
