from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_sources import RetailSourceReader, retail_sources


class RetailSourceReaderTests(unittest.TestCase):
    def test_installed_dlc_source_is_discovered_and_patch_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / 'Data/patch').mkdir(parents=True)
            for name, entries in [('Scripts.pak', {'base.xml': b'base'}),
                                  ('Scripts_DLC4.pak', {'dlc.xml': b'dlc'}),
                                  ('patch/patch_12.pak', {'dlc.xml': b'patched'})]:
                with zipfile.ZipFile(game / 'Data' / name, 'w') as z:
                    for path, data in entries.items(): z.writestr(path, data)
            with RetailSourceReader(game) as reader:
                result = reader(game, {'program': ('Scripts.pak','dlc.xml')})['program']
                self.assertEqual(result['data'], b'patched')
                self.assertTrue(any('Scripts_DLC4.pak' in c['archive'] for c in result['candidates']))

    def test_patch_only_source_and_revision_order_match_uncached_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / 'Data/patch').mkdir(parents=True)
            for file, entries in [('Data/Scripts.pak', {'existing.xml': b'base'}),
                                  ('Data/patch/patch_9.pak', {'new.xml': b'old', 'existing.xml': b'changed'}),
                                  ('Data/patch/ipl_patch_10.pak', {'new.xml': b'newest'})]:
                with zipfile.ZipFile(game / file, 'w') as z:
                    for name, data in entries.items(): z.writestr(name, data)
            request = dict(new=('Scripts.pak', 'new.xml'), existing=('Scripts.pak', 'existing.xml'))
            expected = retail_sources(game, request)
            self.assertEqual(expected['new']['data'], b'newest')
            with RetailSourceReader(game) as reader:
                self.assertEqual(reader(game, request), expected)
                self.assertEqual(reader(game, {'again': request['new']})['again'], expected['new'])
                self.assertEqual(len(reader.cache), 2)
                with self.assertRaisesRegex(ValueError, 'different installation'):
                    reader(game / 'different', request)

    def test_conflicting_same_revision_and_missing_sources_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            (game / 'Data/patch').mkdir(parents=True)
            for name, payload in [('Scripts.pak', b'base'), ('patch/patch_7.pak', b'a'), ('patch/ipl_patch_7.pak', b'b')]:
                with zipfile.ZipFile(game / 'Data' / name, 'w') as z: z.writestr('file.xml', payload)
            for resolver in (retail_sources,):
                with self.assertRaisesRegex(ValueError, 'Ambiguous'): resolver(game, {'file': ('Scripts.pak', 'file.xml')})
            with RetailSourceReader(game) as reader:
                with self.assertRaisesRegex(ValueError, 'Ambiguous'): reader(game, {'file': ('Scripts.pak', 'file.xml')})
                with self.assertRaisesRegex(ValueError, 'Missing effective'): reader(game, {'file': ('Scripts.pak', 'missing.xml')})


if __name__ == '__main__': unittest.main()
