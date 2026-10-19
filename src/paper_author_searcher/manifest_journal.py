"""Append-only JSONL journal of manifest changes between full snapshots.

Rewriting the whole manifest after every paper costs O(papers) per update and O(papers²) per run. Instead each
change appends one fsync'd JSON line; the full manifest is written only at the start and the end of a run. If a
run dies in between, the next run replays the journal over the last snapshot. A torn final line (a crash
mid-write) is ignored, so every complete line is durable and nothing half-written is ever read back.
"""

import json
import os
from pathlib import Path
from typing import IO, Any, Dict, Iterator, List, Optional

JOURNAL_SUFFIX = '.journal.jsonl'


def journal_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(manifest_path.name + JOURNAL_SUFFIX)


class ManifestJournal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: Optional[IO[str]] = None

    def append(self, entry: Dict[str, Any]) -> None:
        if self.path.is_symlink():
            raise ValueError('Manifest journal must not be a symlink')
        if self._handle is None:
            self._handle = self.path.open('a', encoding='utf-8')
        self._handle.write(json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + '\n')
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def entries(self) -> Iterator[Dict[str, Any]]:
        if not self.path.exists():
            return
        if self.path.is_symlink():
            raise ValueError('Manifest journal must not be a symlink')
        with self.path.open(encoding='utf-8') as handle:
            for line in handle:
                if not line.endswith('\n'):
                    return  # torn final write
                try:
                    entry = json.loads(line)
                except ValueError:
                    return
                if isinstance(entry, dict):
                    yield entry

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def discard(self) -> None:
        """Remove the journal once a complete snapshot has made it redundant."""
        self.close()
        if self.path.exists() and not self.path.is_symlink():
            self.path.unlink()


def replay(manifest: Dict[str, Any], entries: Iterator[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply journal records to a snapshot: each entry carries the full record of one input paper."""
    papers: List[Dict[str, Any]] = list(manifest.get('papers') or [])
    run_size = (manifest.get('statistics') or {}).get('total_input_papers', 0)
    for entry in entries:
        record = entry.get('record')
        index = entry.get('index')
        if not isinstance(record, dict) or not record.get('paper_key'):
            continue
        if (
            isinstance(index, int)
            and 0 <= index < min(run_size, len(papers))
            and (papers[index].get('paper_key') == record['paper_key'])
        ):
            papers[index] = record
            continue
        position = next((i for i, paper in enumerate(papers) if paper.get('paper_key') == record['paper_key']), None)
        if position is None:
            papers.append(record)
        else:
            papers[position] = record
    return dict(manifest, papers=papers)
