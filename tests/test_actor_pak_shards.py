from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from build_quest_people_test import write_actor_paks


class ActorPakShardTests(unittest.TestCase):
    def stage(self, root, files):
        appearance = root/'appearance'; data = root/'Data'
        data.mkdir(); appearance.mkdir()
        for name, blob in files.items():
            path = appearance/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        return appearance, data

    def test_libs_returned_and_other_files_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            appearance, data = self.stage(Path(directory), {
                'Libs/a.xml': b'<a/>', 'objects/o.bin': b'x', 'notes.txt': b'n',
                'Localization/t.xml': b'<t/>'})
            paks, libs = write_actor_paks(appearance, data, 'npc')
            self.assertEqual(libs, {'Libs/a.xml': b'<a/>'})
            self.assertEqual([p.name for p in paks], ['npc.pak'])
            with zipfile.ZipFile(paks[0]) as z: self.assertEqual(z.namelist(), ['objects/o.bin'])

    def test_shards_split_without_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            files = {'objects/f%d.bin' % i: bytes([i]) * 100 for i in range(5)}
            appearance, data = self.stage(Path(directory), files)
            paks, _ = write_actor_paks(appearance, data, 'npc', limit=520)
            self.assertEqual([p.name for p in paks], ['npc.pak', 'npc_01.pak', 'npc_02.pak'])
            seen = {}
            for pak in paks:
                self.assertTrue(pak.exists())
                with zipfile.ZipFile(pak) as z:
                    self.assertIsNone(z.testzip())
                    for info in z.infolist():
                        self.assertNotIn(info.filename, seen)
                        self.assertLess(info.header_offset, 2**32)
                        self.assertEqual(info.extra, b'')
                        seen[info.filename] = z.read(info)
            self.assertEqual(seen, files)

    def test_oversized_file_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            appearance, data = self.stage(Path(directory), {'objects/big.bin': b'x' * 600})
            with self.assertRaisesRegex(ValueError, 'objects/big.bin'):
                write_actor_paks(appearance, data, 'npc', limit=520)

    def test_empty_input_makes_empty_pak(self):
        with tempfile.TemporaryDirectory() as directory:
            appearance, data = self.stage(Path(directory), {})
            paks, libs = write_actor_paks(appearance, data, 'npc', limit=520)
            self.assertEqual(libs, {})
            self.assertEqual([p.name for p in paks], ['npc.pak'])
            with zipfile.ZipFile(paks[0]) as z: self.assertEqual(z.namelist(), [])


if __name__ == '__main__':
    unittest.main()
