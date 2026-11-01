"""Package digests are reused only while a file is provably unchanged."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import upgrade_map
from upgrade_map import digest


class DigestMemoTests(unittest.TestCase):
    def setUp(self):
        upgrade_map._DIGESTS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def settled(self, name, data):
        path = self.root/name
        path.write_bytes(data)
        old = path.stat().st_mtime_ns - 60_000_000_000
        os.utime(path, ns=(old, old))
        return path

    def reads(self, path):
        real = hashlib.file_digest
        with patch('upgrade_map.hashlib.file_digest', side_effect=real) as counted:
            value = digest(path)
        return value, counted.call_count

    def test_unchanged_file_is_read_once(self):
        path = self.settled('a.pak', b'payload')
        self.assertEqual(self.reads(path), (hashlib.sha256(b'payload').hexdigest(), 1))
        self.assertEqual(self.reads(path), (hashlib.sha256(b'payload').hexdigest(), 0))

    def test_rewritten_file_is_read_again(self):
        path = self.settled('a.pak', b'payload')
        digest(path)
        path.write_bytes(b'changed')
        self.assertEqual(self.reads(path), (hashlib.sha256(b'changed').hexdigest(), 1))

    def test_recently_written_file_is_never_remembered(self):
        path = self.root/'fresh.pak'
        path.write_bytes(b'payload')
        digest(path)
        self.assertEqual(self.reads(path)[1], 1)

    def test_hard_links_share_one_digest(self):
        path = self.settled('a.pak', b'payload')
        digest(path)
        os.link(path, self.root/'b.pak')
        self.assertEqual(self.reads(self.root/'b.pak')[1], 0)

    def test_full_verify_reads_every_time(self):
        path = self.settled('a.pak', b'payload')
        digest(path)
        with patch.dict(os.environ, {'GLUEMAPPER_FULL_VERIFY': '1'}):
            self.assertEqual(self.reads(path)[1], 1)

    def test_memo_stays_bounded(self):
        with patch.object(upgrade_map, '_DIGEST_LIMIT', 2):
            for index in range(5):
                digest(self.settled(f'{index}.pak', bytes([index])))
            self.assertLessEqual(len(upgrade_map._DIGESTS), 2)


if __name__ == '__main__':
    unittest.main()
