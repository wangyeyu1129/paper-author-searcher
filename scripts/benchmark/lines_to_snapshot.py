"""Convert a hand transcription (one publication per line) into a ``manual`` truth snapshot.

Line format: ``YEAR | TYPE | VENUE | TITLE | AUTHOR, AUTHOR, ... [| doi=...;arxiv=...;pmid=...]``.
Lines starting with ``#`` are notes. Usage: lines_to_snapshot.py LINES OUT_JSON SOURCE_URL SCOPE_NOTE
"""

import json
import sys
from datetime import date


def parse_line(raw):
    parts = [part.strip() for part in (raw.rstrip('|') + ' ').split(' | ')]
    parts += [''] * (5 - len(parts))
    year, kind, venue, title, authors = parts[:5]
    entry = {
        'title': title,
        'year': int(year),
        'type': kind or None,
        'venue': venue or None,
        'authors': [name.strip().rstrip('*').strip() for name in authors.split(', ') if name.strip()],
    }
    for item in (parts[5] if len(parts) > 5 else '').split(';'):
        key, _, value = item.partition('=')
        if value.strip():
            entry[key.strip()] = value.strip()
    return entry


def main(argv):
    lines_path, out, url, note = argv[1:5]
    with open(lines_path, encoding='utf-8') as handle:
        rows = [line.strip() for line in handle]
    entries = [parse_line(row) for row in rows if row and not row.startswith('#')]
    notes = [row[1:].strip() for row in rows if row.startswith('#')]
    snapshot = {
        'snapshot_schema_version': 1,
        'source_url': url,
        'captured_at': date.today().isoformat(),
        'captured_by': 'Assistant transcription of the public page; pending human review',
        'scope_note': note,
        'transcription_notes': notes,
        'entries': entries,
    }
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    print(out, len(entries))


if __name__ == '__main__':
    main(sys.argv)
