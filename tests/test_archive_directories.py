"""Parsed ZIP directories are shared only between readers of a provably unchanged file."""
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import archive_directories
from archive_directories import CachedZipFile


class ArchiveDirectoryTests(unittest.TestCase):
    def setUp(self):
        archive_directories.uninstall()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        archive_directories.uninstall()
        self.tmp.cleanup()

    def archive(self, name, members, age=60):
        """An archive whose modification time is `age` seconds in the past."""
        path = self.root/name
        with zipfile.ZipFile(path, 'w') as out:
            for member, data in members.items():
                out.writestr(member, data)
        if age:
            old = path.stat().st_mtime_ns - age*1_000_000_000
            os.utime(path, ns=(old, old))
        return path

    def opened(self, source, **kw):
        """The directory, the member bytes and how often the stdlib parsed a directory."""
        real = archive_directories._STDLIB._RealGetContents
        with patch.object(archive_directories._STDLIB, '_RealGetContents', autospec=True, side_effect=real) as parses:
            with CachedZipFile(source, **kw) as archive:
                listing = [(i.filename, i.CRC, i.header_offset, i.file_size) for i in archive.infolist()]
                data = {n: archive.read(n) for n in archive.namelist()}
        return listing, data, parses.call_count

    def test_an_unchanged_file_is_parsed_once(self):
        path = self.archive('a.pak', {'x': b'one', 'y': b'two'*50})
        first, second = self.opened(path), self.opened(path)
        self.assertEqual((first[2], second[2]), (1, 0))
        self.assertEqual(first[:2], second[:2])
        self.assertEqual(second[1], {'x': b'one', 'y': b'two'*50})

    def test_a_changed_file_is_parsed_again(self):
        path = self.archive('a.pak', {'x': b'one'})
        self.opened(path)
        # Same size, different bytes: the identity changes with the time.
        self.archive('a.pak', {'x': b'two'}, age=30)
        self.assertEqual(self.opened(path)[1:], ({'x': b'two'}, 1))

    def test_recent_files_are_not_remembered(self):
        path = self.archive('a.pak', {'x': b'one'}, age=0)
        self.assertEqual((self.opened(path)[2], self.opened(path)[2]), (1, 1))

    def test_streams_and_writers_keep_the_stdlib_path(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as out:
            out.writestr('x', b'one')
        self.assertEqual((self.opened(io.BytesIO(buffer.getvalue()))[2], self.opened(io.BytesIO(buffer.getvalue()))[2]), (1, 1))
        path = self.archive('a.pak', {'x': b'one'})
        self.opened(path)
        with CachedZipFile(path, 'a') as out:
            out.writestr('y', b'two')
        old = path.stat().st_mtime_ns - 60_000_000_000
        os.utime(path, ns=(old, old))
        self.assertEqual(self.opened(path)[1:], ({'x': b'one', 'y': b'two'}, 1))

    def test_a_writer_never_edits_a_shared_directory(self):
        path = self.archive('a.pak', {'x': b'one'})
        with CachedZipFile(path) as reader:
            info = reader.getinfo('x')
            before = (info.header_offset, info.CRC, info.file_size, info.compress_size)
            with CachedZipFile(self.root/'b.pak', 'w') as out:
                out.writestr(info, b'a much longer replacement')
            with CachedZipFile(self.root/'c.pak', 'w') as out:
                with out.open(info, 'w') as member:
                    member.write(b'again')
            self.assertEqual((info.header_offset, info.CRC, info.file_size, info.compress_size), before)
        for name, data in (('b.pak', b'a much longer replacement'), ('c.pak', b'again')):
            with zipfile.ZipFile(self.root/name) as out:
                self.assertEqual(out.read('x'), data)
        self.assertEqual(self.opened(path), ([('x', before[1], before[0], before[2])], {'x': b'one'}, 0))

    def test_the_memo_is_bounded_by_entries_and_drops_the_oldest(self):
        a = self.archive('a.pak', {'1': b'', '2': b''})
        b = self.archive('b.pak', {'1': b'', '2': b''})
        big = self.archive('c.pak', {str(i): b'' for i in range(4)})
        with patch.object(archive_directories, '_ENTRY_LIMIT', 3):
            self.assertEqual(self.opened(a)[2], 1)
            self.assertEqual(self.opened(b)[2], 1)
            self.assertEqual(self.opened(a)[2], 1)  # dropped to keep b under the bound
            self.assertEqual((self.opened(big)[2], self.opened(big)[2]), (1, 1))  # too large to keep

    def test_missing_file_ids_are_not_remembered(self):
        path = self.archive('a.pak', {'x': b'one'})
        real = os.fstat
        def no_ids(fd):
            stat = real(fd)
            return SimpleNamespace(st_dev=stat.st_dev, st_ino=0, st_size=stat.st_size, st_mtime_ns=stat.st_mtime_ns)
        with patch.object(archive_directories.os, 'fstat', no_ids):
            self.assertEqual((self.opened(path)[2], self.opened(path)[2]), (1, 1))

    def test_install_serves_every_zipfile_open_in_the_process(self):
        path = self.archive('a.pak', {'x': b'one'})
        with patch.dict(os.environ, {'GLUEMAPPER_ARCHIVE_DIRECTORY_CACHE': '0'}):
            self.assertFalse(archive_directories.install())
            self.assertIs(zipfile.ZipFile, archive_directories._STDLIB)
        self.assertTrue(archive_directories.install())
        with zipfile.ZipFile(path) as archive:
            self.assertIsInstance(archive, CachedZipFile)
            self.assertIsInstance(archive, archive_directories._STDLIB)
            self.assertEqual(archive.read('x'), b'one')
        self.assertEqual(self.opened(path)[2], 0)
        archive_directories.uninstall()
        self.assertIs(zipfile.ZipFile, archive_directories._STDLIB)
        self.assertEqual(self.opened(path)[2], 1)


if __name__ == '__main__':
    unittest.main()
