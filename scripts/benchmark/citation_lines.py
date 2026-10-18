"""Segment a page that lists one citation per line under year headings into a ``manual`` snapshot.

Only lines carrying a DOI or PMID are kept, so every entry has a stable identifier. Pages without year headings
are handled when each line carries "(YEAR)"; ACS-style "Family, I.; Family, I. Title." bylines are split too.
Usage: citation_lines.py URL START_YEAR END_YEAR OUT_JSON SCOPE_NOTE
"""
import html
import json
import re
import sys
import urllib.request
from datetime import date

DOI = re.compile(r'(?:doi(?:\.org)?[:/]\s*|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/[^\s;,]+)', re.I)
PMID = re.compile(r'PMID:\s*(\d+)')


def page_lines(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(request, timeout=30).read()
    encoding = 'utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'
    raw = data.decode(encoding, 'replace')
    text = re.sub(r'<script.*?</script>|<style.*?</style>', '', raw, flags=re.S)
    text = re.sub(r'<br\s*/?>|</p>|</li>|</h\d>|</div>', '\n', text)
    text = html.unescape(re.sub(r'<[^>]+>', '', text))
    return [line.strip() for line in text.split('\n') if line.strip()]


INLINE_YEAR = re.compile(r'\(((?:19|20)\d{2})\)')
INITIALS_THEN_TITLE = re.compile(r'^(?P<family>[^,]+),\s*(?P<given>(?:[A-Z][a-z]?\.\s?-?)+)\s+(?P<rest>.+)$')


def acs_entry(line, year):
    """"Family, I.; Family, I. J. Title. Journal, (YEAR) DOI" -> authors and title."""
    chunks = line.split('; ')
    if len(chunks) < 2:
        return None
    last = INITIALS_THEN_TITLE.match(chunks[-1])
    if not last:
        return None
    authors = []
    for chunk in chunks[:-1] + ['{}, {}'.format(last.group('family'), last.group('given'))]:
        family, _, given = chunk.partition(',')
        authors.append((given.strip() + ' ' + family.strip()).strip())
    title = re.split(r'\.\s+', last.group('rest'), maxsplit=1)[0]
    return {'title': title.strip(), 'year': year, 'type': 'journal_article', 'authors': authors, 'citation': line}


def entry(line, year):
    doi, pmid = DOI.search(line), PMID.search(line)
    if not doi and not pmid:
        return None
    result = acs_entry(line, year) if ';' in line[:120] else None
    if result:
        if doi:
            result['doi'] = doi.group(1).rstrip('.')
        return result
    parts = re.split(r'\.\s+', line, maxsplit=2)
    authors_text = re.sub(r'\(\d{4}\).*', '', parts[1] if len(parts) > 1 else '')
    authors = [name.strip(' .&') for name in re.split(r',\s*&\s*|,\s+(?=[A-Z])', authors_text) if name.strip(' .&')]
    result = {'title': parts[0].strip(), 'year': year, 'type': 'journal_article', 'authors': authors, 'citation': line}
    if doi:
        result['doi'] = doi.group(1).rstrip('.')
    if pmid:
        result['pmid'] = pmid.group(1)
    return result


def main(argv):
    url, start, end, out, note = argv[1], int(argv[2]), int(argv[3]), argv[4], argv[5]
    year, entries = None, []
    for line in page_lines(url):
        if re.fullmatch(r'(19|20)\d{2}', line):
            year = int(line)
        elif len(line) >= 40:
            inline = INLINE_YEAR.search(line)
            line_year = int(inline.group(1)) if inline else year
            if line_year is None or not start <= line_year <= end:
                continue
            item = entry(line, line_year)
            if item:
                entries.append(item)
    snapshot = {'snapshot_schema_version': 1, 'source_url': url, 'captured_at': date.today().isoformat(),
                'captured_by': 'Script segmentation of the public page (one citation per line); pending human review',
                'scope_note': note, 'entries': entries}
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    print(out, len(entries))


if __name__ == '__main__':
    main(sys.argv)
