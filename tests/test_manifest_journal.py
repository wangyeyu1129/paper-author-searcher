import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from paper_author_searcher.http import DownloadReceipt
from paper_author_searcher.manifest_journal import ManifestJournal, journal_path, replay
from paper_author_searcher.pdf_downloader import PDFDownloader
from test_pdf_downloader import fake_validator, target


def fake_client():
    """Distinct bytes per URL, so no download is a duplicate of another."""
    client = Mock()

    def transfer(url, path, config):
        body = b'%PDF-synthetic ' + url.encode()
        path.write_bytes(body)
        return DownloadReceipt(url, 200, 'application/pdf', len(body), hashlib.sha256(body).hexdigest())

    client.stream_pdf.side_effect = transfer
    return client


def paper(number):
    value = target()
    value['doi'] = f'10.1234/example-{number}'
    value['title'] = f'Reusable methods for synthetic research {number}'
    for candidate in value['pdf_candidates']:
        candidate['url'] = f'https://repo.example.edu/document-{number}'
    value['best_pdf_url'] = value['pdf_candidates'][0]['url']
    return value


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'manifest.json.journal.jsonl'

    def test_entries_round_trip_and_torn_tail_is_ignored(self):
        journal = ManifestJournal(self.path)
        journal.append({'index': 0, 'record': {'paper_key': 'a', 'download_status': 'downloading'}})
        journal.append({'index': 0, 'record': {'paper_key': 'a', 'download_status': 'downloaded'}})
        journal.close()
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write('{"index": 1, "record": {"paper_k')  # crash mid-write
        statuses = [entry['record']['download_status'] for entry in ManifestJournal(self.path).entries()]
        self.assertEqual(statuses, ['downloading', 'downloaded'])

    def test_replay_updates_by_index_then_by_key(self):
        snapshot = {
            'statistics': {'total_input_papers': 2},
            'papers': [{'paper_key': 'a', 'v': 0}, {'paper_key': 'b', 'v': 0}, {'paper_key': 'old', 'v': 0}],
        }
        entries = [
            {'index': 1, 'record': {'paper_key': 'b', 'v': 1}},
            {'index': 7, 'record': {'paper_key': 'old', 'v': 2}},
            {'index': 9, 'record': {'paper_key': 'new', 'v': 3}},
            {'record': {'v': 4}},
        ]
        result = replay(snapshot, iter(entries))
        self.assertEqual(
            [(p['paper_key'], p['v']) for p in result['papers']], [('a', 0), ('b', 1), ('old', 2), ('new', 3)]
        )
        self.assertEqual(snapshot['papers'][1]['v'], 0)

    def test_discard_removes_the_file(self):
        journal = ManifestJournal(self.path)
        journal.append({'index': 0, 'record': {'paper_key': 'a'}})
        journal.discard()
        self.assertFalse(self.path.exists())


class DownloaderJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_full_manifest_is_written_twice_per_run_whatever_the_paper_count(self):
        downloader = PDFDownloader(fake_client(), fake_validator())
        papers = [paper(number) for number in range(6)]
        with patch.object(PDFDownloader, '_save_manifest', wraps=downloader._save_manifest) as save:
            manifest = downloader.download({'papers': papers}, self.root)
        self.assertEqual(save.call_count, 2)
        self.assertEqual([entry['download_status'] for entry in manifest['papers']], ['downloaded'] * 6)
        self.assertFalse(journal_path(self.root / 'manifest.json').exists())

    def test_progress_survives_a_crash_between_snapshots(self):
        papers = [paper(number) for number in range(3)]
        downloader = PDFDownloader(fake_client(), fake_validator())
        original = downloader._save_manifest
        calls = []

        def crash_on_final_snapshot(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('disk vanished')  # the process dies before the closing snapshot
            return original(*args, **kwargs)

        with patch.object(downloader, '_save_manifest', side_effect=crash_on_final_snapshot):
            with self.assertRaises(OSError):
                downloader.download({'papers': papers}, self.root)
        snapshot = json.loads((self.root / 'manifest.json').read_text())
        self.assertEqual({entry['download_status'] for entry in snapshot['papers']}, {'not_processed'})
        recovered = PDFDownloader(fake_client(), fake_validator())._load_manifest(self.root)
        self.assertEqual([entry['download_status'] for entry in recovered['papers']], ['downloaded'] * 3)

        client = fake_client()
        manifest = PDFDownloader(client, fake_validator()).download({'papers': papers}, self.root)
        client.stream_pdf.assert_not_called()  # completed files are reused, not fetched again
        self.assertEqual([entry['download_status'] for entry in manifest['papers']], ['skipped_existing'] * 3)


if __name__ == '__main__':
    unittest.main()
