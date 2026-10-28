import unittest

from paper_author_searcher.benchmark.cli import replay_settings
from paper_author_searcher.benchmark.settings import RunSettings


class ReplaySettingsTests(unittest.TestCase):
    def test_a_strict_replay_reads_the_cache_only_and_stops_at_metadata(self):
        run = replay_settings(RunSettings(mode='full'), record_misses=False, mode='full')
        self.assertEqual((run.http_cache_mode, run.mode), ('replay', 'metadata'))

    def test_recording_misses_runs_the_requested_mode(self):
        run = replay_settings(RunSettings(), record_misses=True, mode='full')
        self.assertEqual((run.http_cache_mode, run.mode), ('record', 'full'))

    def test_recording_misses_defaults_to_metadata(self):
        self.assertEqual(replay_settings(RunSettings(), record_misses=True, mode=None).mode, 'metadata')


if __name__ == '__main__':
    unittest.main()
