"""Log rotation must bound the directory without breaking a service that is still writing."""
import gzip
import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / 'bin' / 'ep-rotate-logs.py'


@unittest.skipUnless(SCRIPT.is_file(), 'tool not deployed')
class RotateTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('ep_rotate_logs', SCRIPT)
        self.rot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.rot)
        self.rot.MAX_BYTES = 1000
        self.rot.IDLE_MIN_BYTES = 100
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_writer_keeps_appending_after_its_log_is_rotated(self):
        log = self.dir / 'worker.log'
        writer = log.open('ab', buffering=0)
        writer.write(b'x' * 1500)
        report = self.rot.run(self.dir)
        writer.write(b'after\n')
        writer.close()
        self.assertEqual(report['rotated'], ['worker.log'])
        self.assertEqual(log.read_bytes(), b'after\n')  # O_APPEND: lands at the new end, no hole
        archived = next((self.dir / 'archive').glob('worker.*.gz'))
        self.assertEqual(gzip.open(archived).read(), b'x' * 1500)

    def test_a_small_or_busy_log_is_left_alone(self):
        (self.dir / 'small.log').write_bytes(b'x' * 50)
        (self.dir / 'busy.log').write_bytes(b'x' * 500)
        report = self.rot.run(self.dir)
        self.assertEqual((report['rotated'], report['compressed']), ([], []))

    def test_an_idle_log_is_compressed_in_place_and_nothing_is_lost(self):
        log = self.dir / 'old.log'
        log.write_bytes(b'y' * 500)
        old = time.time() - 5 * 24 * 3600
        os.utime(log, (old, old))
        report = self.rot.run(self.dir)
        self.assertEqual(report['compressed'], ['old.log'])
        self.assertFalse(log.exists())
        self.assertEqual(gzip.open(next((self.dir / 'archive').glob('old.*.gz'))).read(), b'y' * 500)

    def test_only_the_newest_archives_are_kept(self):
        log = self.dir / 'a.log'
        for i in range(self.rot.KEEP_ARCHIVES + 3):
            log.write_bytes(b'z' * 2000)
            self.rot.run(self.dir, now=time.time() + i * 10)
        self.assertEqual(len(list((self.dir / 'archive').glob('a.*.gz'))), self.rot.KEEP_ARCHIVES)

    def test_dry_run_changes_nothing(self):
        log = self.dir / 'big.log'
        log.write_bytes(b'x' * 2000)
        report = self.rot.run(self.dir, dry_run=True)
        self.assertEqual(report['rotated'], ['big.log'])
        self.assertEqual(log.stat().st_size, 2000)
        self.assertFalse((self.dir / 'archive').exists())


if __name__ == '__main__':
    unittest.main()
