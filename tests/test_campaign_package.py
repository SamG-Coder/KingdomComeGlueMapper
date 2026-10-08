from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_package import LEVEL, LEVEL_ID, dependency_closure, level_registration, package_world, references, safe_name, write_asset_shards, mounted_level_tables, campaign_spawn_objects


class CampaignPackageTests(unittest.TestCase):
    def test_spawn_rejects_ambiguous_start_selection(self):
        spawn = {'position': '100,200,30', 'rotation': '1,0,0,0'}
        for data in (b'<Objects><Entity Name="other" EntityClass="SpawnPoint"/></Objects>',
                     b'<Objects><Entity Name="gluemapper_new_game_spawn" EntityClass="GeomEntity"/></Objects>'):
            with self.assertRaises(ValueError):
                campaign_spawn_objects(data, spawn)

    def test_retail_table_mount_matches_data_prefixed_mod_level_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            level = Path(temporary) / 'level.pak'
            contents = b'<database><WeatherProfiles><WeatherProfile Name="converted"/></WeatherProfiles></database>'
            with zipfile.ZipFile(level, 'w') as z:
                z.writestr('tables/weatherprofiles.xml', contents)
                z.writestr('mission_mission0.xml', b'<Mission/>')
            entries = mounted_level_tables(level, 'kingdomcomegluemapper')
            requested = 'data/mods/kingdomcomegluemapper/data/levels/kcd1_rataje/Tables/WeatherProfiles.xml'
            mounted = {'data/' + name.lower(): data for name, data in entries.items()}
            self.assertEqual(mounted[requested.lower()], contents)
            self.assertEqual(len(entries), 1)
            with self.assertRaises(ValueError):
                mounted_level_tables(level, '../escape')

    def test_shards_respect_size_count_and_preserve_every_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            entries = [(f'glueitems/x{i}.cgf', bytes([i]) * 100) for i in range(7)]
            shards, assets = write_asset_shards(temporary, entries, max_bytes=512, max_entries=2)
            self.assertEqual(len(shards), 4)
            actual = {}
            for name in shards:
                path = Path(temporary) / name
                self.assertLessEqual(path.stat().st_size, 512)
                with zipfile.ZipFile(path) as z:
                    self.assertLessEqual(len(z.namelist()), 2)
                    actual.update({n: z.read(n) for n in z.namelist()})
            self.assertEqual(actual, dict(entries))
            self.assertEqual(len(assets), 7)
    def test_level_registration_does_not_replace_native_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'Data').mkdir()
            path = root / 'Data/Tables.pak'
            def table(level_id):
                with zipfile.ZipFile(path, 'w') as z:
                    z.writestr('Libs/Tables/level.xml', f'<database name="barbora"><levels version="1"><LevelData LevelId="{level_id}" LevelName="trosecko"/></levels></database>')
            table(2)
            before = path.read_bytes()
            patch = ET.fromstring(level_registration(root))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual([r.attrib for r in patch.find('levels')],
                             [{'LevelId': str(LEVEL_ID), 'LevelName': LEVEL, 'CompassOffset': '0'}])
            table(LEVEL_ID)
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                level_registration(root)

    def test_transitive_material_texture_and_streamed_parts(self):
        files = {'gluebuild/old/tree.cgf': b'gluebuild/old/tree\0',
                 'gluebuild/old/tree.mtl': b'<Texture File="gluebuild/old/leaf.dds"/>',
                 'gluebuild/old/leaf.dds': b'pixels',
                 'gluebuild/old/leaf.dds.1': b'mip',
                 'gluebuild/old/tree.cgfm': b'geometry',
                 'gluebuild/unused.cgf': b'unused'}
        # Material paths in compiled meshes omit their extension; here tree.cgf
        # and tree.mtl share the same stem, so resolve that as ambiguous.
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            dependency_closure({'gluebuild/old/tree.cgf'}, files, files.__getitem__)
        files['gluebuild/old/tree.cgf'] = b'gluebuild/old/tree.mtl\0'
        result = dependency_closure({'gluebuild/old/tree.cgf'}, files, files.__getitem__)
        self.assertEqual(set(result), set(files) - {'gluebuild/unused.cgf'})

    def test_missing_asset_fails_instead_of_silent_omission(self):
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            dependency_closure({'glueitems/missing.cgf'}, {}, lambda _: b'')

    def test_rejects_traversal(self):
        for name in ('glueitems/../base.cgf', '/absolute', 'C:/absolute', 'glueitems//x'):
            with self.assertRaises(ValueError):
                safe_name(name)
        self.assertEqual(references(b'<Texture File="gluebuild/old/a.dds"/>'), {'gluebuild/old/a.dds'})

    def test_stable_level_and_transitive_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, output = root / 'converted', root / 'package'
            level = data / 'Levels/old_probe'
            level.mkdir(parents=True)
            with zipfile.ZipFile(level / 'level.pak', 'w') as z:
                z.writestr('levelinfo.xml', b'<LevelInfo Name="data/levels/old_probe"/>')
                z.writestr('leveldata.xml', b'<LevelData><LevelInfo Name="old_probe"/></LevelData>')
                z.writestr('objects.xml', b'<Object Geometry="glueitems/old/item.cgf"/>')
            with zipfile.ZipFile(level / 'terrain.pak', 'w') as z:
                z.writestr('terrain/terrain.dat', b'unchanged terrain')
            asset = data / 'glueitems/old/item.cgf'
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b'geometry')
            report = package_world(data, 'old_probe', output)
            self.assertEqual(report['level'], LEVEL)
            self.assertEqual(len(report['imported_assets']), 1)
            target = output / 'Data/Levels' / LEVEL
            self.assertEqual((target / 'terrain.pak').read_bytes(), (level / 'terrain.pak').read_bytes())
            with zipfile.ZipFile(target / 'level.pak') as z:
                self.assertEqual(ET.fromstring(z.read('leveldata.xml')).find('LevelInfo').get('Name'), LEVEL)
            with zipfile.ZipFile(output / 'Data' / report['asset_archives'][0]) as z:
                self.assertEqual(z.read('glueitems/old/item.cgf'), b'geometry')


if __name__ == '__main__':
    unittest.main()
