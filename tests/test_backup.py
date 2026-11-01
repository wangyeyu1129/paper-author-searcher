import hashlib
import io
import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path

from paper_author_searcher.service.backup import MANIFEST, Backups, last_backup_time


def database(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE IF NOT EXISTS item (value TEXT)')
    connection.execute('DELETE FROM item')
    connection.execute('INSERT INTO item VALUES (?)', (value,))
    connection.commit()
    connection.close()


def stored(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute('SELECT value FROM item').fetchone()[0]
    finally:
        connection.close()


class BackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / 'data'
        (self.data / 'files' / 'job1').mkdir(parents=True)
        (self.data / 'files' / 'job1' / 'paper.pdf').write_bytes(b'%PDF body')
        (self.data / 'api_keys.json').write_text('{"keys": []}')
        (self.data / 'indexes').mkdir()
        (self.data / 'indexes' / 'dblp.sqlite').write_bytes(b'large and rebuildable')
        database(self.data / 'jobs.sqlite', 'original')
        database(self.data / 'limits.sqlite', 'spend')
        self.backups = Backups(self.data, self.data / 'api_keys.json')
        self.output = self.root / 'backups'

    def test_a_restore_after_losing_the_data_brings_everything_back(self):
        archive = self.backups.create(self.output)
        restored = self.root / 'new'
        count = Backups(restored, restored / 'api_keys.json').restore(archive)
        self.assertEqual(count, 4)
        self.assertEqual(stored(restored / 'jobs.sqlite'), 'original')
        self.assertEqual((restored / 'files' / 'job1' / 'paper.pdf').read_bytes(), b'%PDF body')
        self.assertEqual((restored / 'api_keys.json').read_text(), '{"keys": []}')
        self.assertFalse((restored / 'indexes').exists())

    def test_a_backup_reads_what_a_running_writer_has_only_in_its_log(self):
        writer = sqlite3.connect(self.data / 'jobs.sqlite')
        self.addCleanup(writer.close)
        writer.execute('UPDATE item SET value = ?', ('in the log',))
        writer.commit()
        restored = self.root / 'new'
        Backups(restored, restored / 'api_keys.json').restore(self.backups.create(self.output))
        self.assertEqual(stored(restored / 'jobs.sqlite'), 'in the log')

    def test_a_restore_replaces_only_with_force_and_clears_the_old_log(self):
        archive = self.backups.create(self.output)
        database(self.data / 'jobs.sqlite', 'changed since')
        (self.data / 'files' / 'job2').mkdir()
        (self.data / 'files' / 'job2' / 'newer.pdf').write_bytes(b'newer')
        (self.data / 'jobs.sqlite-wal').write_bytes(b'stale log')
        with self.assertRaisesRegex(ValueError, 'force'):
            self.backups.restore(archive)
        self.assertEqual((self.data / 'jobs.sqlite-wal').read_bytes(), b'stale log')
        self.backups.restore(archive, force=True)
        self.assertEqual(stored(self.data / 'jobs.sqlite'), 'original')
        self.assertFalse((self.data / 'files' / 'job2').exists())
        self.assertFalse((self.data / 'jobs.sqlite-wal').exists())

    def _rewritten(self, archive: Path, change) -> Path:
        with tarfile.open(archive, 'r:gz') as source:
            members = {member.name: source.extractfile(member).read() for member in source.getmembers()}
        change(members)
        damaged = self.root / 'damaged.tar.gz'
        with tarfile.open(damaged, 'w:gz') as target:
            for name, content in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                target.addfile(info, io.BytesIO(content))
        return damaged

    def test_a_damaged_archive_leaves_the_data_as_it_was(self):
        archive = self.backups.create(self.output)
        damaged = self._rewritten(archive, lambda members: members.update({'files/job1/paper.pdf': b'tampered'}))
        database(self.data / 'jobs.sqlite', 'current')
        with self.assertRaisesRegex(ValueError, 'damaged'):
            self.backups.restore(damaged, force=True)
        self.assertEqual(stored(self.data / 'jobs.sqlite'), 'current')

    def test_a_member_outside_the_manifest_or_the_directory_is_refused(self):
        archive = self.backups.create(self.output)
        extra = self._rewritten(archive, lambda members: members.update({'files/extra.pdf': b'extra'}))
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.backups.restore(extra, force=True)
        escaping = self._rewritten(archive, lambda members: members.update({'../../outside': b'x'}))
        with self.assertRaisesRegex(ValueError, 'Refusing'):
            self.backups.restore(escaping, force=True)
        self.assertFalse((self.root / 'outside').exists())

    def test_an_archive_of_another_format_is_refused(self):
        def change(members):
            members[MANIFEST] = json.dumps({**json.loads(members[MANIFEST]), 'format': 99}).encode()

        with self.assertRaisesRegex(ValueError, 'format'):
            self.backups.restore(self._rewritten(self.backups.create(self.output), change), force=True)

    def test_a_corrupt_database_is_refused(self):
        (self.data / 'limits.sqlite-wal').unlink(missing_ok=True)
        (self.data / 'limits.sqlite-shm').unlink(missing_ok=True)
        archive = self.backups.create(self.output)

        def change(members):
            members['jobs.sqlite'] = b'not a database, just some bytes'
            document = json.loads(members[MANIFEST])
            document['members']['jobs.sqlite']['sha256'] = hashlib.sha256(members['jobs.sqlite']).hexdigest()
            members[MANIFEST] = json.dumps(document).encode()

        with self.assertRaisesRegex(ValueError, 'jobs.sqlite'):
            self.backups.restore(self._rewritten(archive, change), force=True)

    def test_keep_deletes_all_but_the_newest(self):
        archives = [self.backups.create(self.output, keep=2) for _ in range(3)]
        left = sorted(self.output.iterdir())
        self.assertEqual(left, sorted(archives[1:]))

    def test_files_stay_out_when_they_live_in_a_bucket(self):
        archive = Backups(self.data, self.data / 'api_keys.json', include_files=False).create(self.output)
        with tarfile.open(archive, 'r:gz') as source:
            self.assertFalse([name for name in source.getnames() if name.startswith('files/')])

    def test_the_finished_backup_is_visible_to_the_metrics(self):
        self.assertIsNone(last_backup_time(self.data))
        self.backups.create(self.output)
        self.assertIsNotNone(last_backup_time(self.data))


if __name__ == '__main__':
    unittest.main()
