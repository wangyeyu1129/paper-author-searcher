"""Parse ACL-style citation lists ("Authors. 2025. Title. In Proceedings of VENUE.") into a ``manual`` snapshot.

Usage: acl_citations.py URL START_YEAR END_YEAR OUT_JSON SCOPE_NOTE
"""

import html
import json
import re
import sys
import urllib.request
from datetime import date

CITATION = re.compile(r'^(?P<authors>.+?)\.\s+(?P<year>(?:19|20)\d{2})\.\s+(?P<title>.+?[.?!])\s+(?P<venue>.*)$')
ARXIV = re.compile(r'arXiv[:\s]*(\d{4}\.\d{4,5})', re.I)


def split_authors(text):
    text = re.sub(r',?\s+and\s+', ', ', text)
    return [name.strip() for name in text.split(',') if name.strip()]


def kind(venue):
    if re.match(r'arXiv', venue, re.I):
        return 'preprint'
    if re.search(r'Proceedings|Findings|Workshop|Conference', venue, re.I):
        return 'conference_paper'
    return 'journal_article'


def main(argv):
    url, start, end, out, note = argv[1], int(argv[2]), int(argv[3]), argv[4], argv[5]
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(request, timeout=30).read().decode('utf-8', 'replace')
    text = re.sub(r'<script.*?</script>|<style.*?</style>', '', raw, flags=re.S)
    text = re.sub(r'<br\s*/?>|</p>|</li>|</h\d>|</div>|</tr>', '\n', text)
    text = html.unescape(re.sub(r'<[^>]+>', '', text))
    entries = []
    for line in (' '.join(line.split()) for line in text.split('\n')):
        match = CITATION.match(line)
        if not match or not start <= int(match.group('year')) <= end:
            continue
        venue = re.sub(r'\[[^\]]*\]', '', match.group('venue')).strip(' .')
        work_type = kind(venue)
        venue = re.sub(r'^In (Proceedings of )?', '', venue)
        item = {
            'title': match.group('title').rstrip('.'),
            'year': int(match.group('year')),
            'type': work_type,
            'venue': venue,
            'authors': split_authors(match.group('authors')),
        }
        arxiv = ARXIV.search(match.group('venue'))
        if arxiv:
            item['arxiv'] = arxiv.group(1)
        entries.append(item)
    snapshot = {
        'snapshot_schema_version': 1,
        'source_url': url,
        'captured_at': date.today().isoformat(),
        'captured_by': 'Script parsing of ACL-style citations on the public page; pending human review',
        'scope_note': note,
        'entries': entries,
    }
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    print(out, len(entries))


if __name__ == '__main__':
    main(sys.argv)
