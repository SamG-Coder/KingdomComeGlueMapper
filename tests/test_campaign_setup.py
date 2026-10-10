import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_sources import OPENING_FILES, audit_opening, opening_sources, opening_spawn
from setup_campaign import MOD_ID, RECEIPT, build_campaign, build_probe, install, verify_package, uninstall, refresh_runtime, update_runtime


class CampaignSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / 'old', self.root / 'new'
        for root in (self.source, self.target):
            (root / 'Data').mkdir(parents=True)
        grouped = {}
        for label, (pak, entry) in OPENING_FILES.items():
            grouped.setdefault(pak, {})[entry] = (
                b'<Graph><Nodes><Node Id="1" Class="Quest:Begin"/></Nodes><Edges/></Graph>'
                if label == 'quest_graph' else b'<BehaviorTrees/>')
        for pak, files in grouped.items():
            self.pak(self.source / 'Data' / pak, files)
        for root, relative in [(self.source, 'Levels/rataje/level.pak'),
                               (self.target, 'Levels/trosecko/level.pak'),
                               (self.target, 'Scripts.pak'), (self.target, 'IPL_GameData.pak')]:
            self.pak(root / 'Data' / relative, {})
        self.pak(self.target / 'Data/Levels/trosecko/level.pak', {'tables/ai/scheduler.xml': b'<database name="barbora"><Schedulers version="1"><NativeRecord/></Schedulers></database>'})
        self.pak(self.source / 'Data/Levels/rataje/level.pak', {
            'objects_mission0.xml': b'<Objects><Entity Name="spawnStart" EntityClass="SpawnPoint" Pos="123,456,78" Rotate="1,0,0,0" EntityId="99" EntityGuid="original"/></Objects>'})
        (self.target / 'system.cfg').write_text('wh_sys_version = "1.5.2"')

    def pak(self, path, files):
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as z:
            for name, data in files.items():
                z.writestr(name, data)

    def build(self):
        output = self.root / 'package'
        build_probe(self.source, self.target, output)
        return output

    def test_retail_only_package_and_install(self):
        output = self.build()
        with zipfile.ZipFile(output / 'Data' / (MOD_ID + '.pak')) as z:
            self.assertEqual(z.namelist(), ['Scripts/Mods/' + MOD_ID + '.lua'])
            self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist()))
        self.assertFalse(verify_package(output)['ready_to_play'])
        destination = install(output, self.target)
        self.assertEqual(verify_package(destination), verify_package(output))
        with self.assertRaises(ValueError):
            install(output, self.target)

    def test_campaign_bundle_contains_world_registration_and_isolated_sources(self):
        converted = self.root / 'converted'
        self.pak(converted / 'Levels/probe/level.pak', {
            'levelinfo.xml': b'<LevelInfo Name="data/levels/probe"/>',
            'leveldata.xml': b'<LevelData><LevelInfo Name="probe"/></LevelData>',
            'objects_mission0.xml': b'<Objects><Entity Name="keep_house" EntityClass="GeomEntity" Pos="200,300,70"/></Objects>'})
        self.pak(converted / 'Levels/probe/terrain.pak', {'terrain/terrain.dat': b'terrain'})
        self.pak(self.target / 'Data/Tables.pak', {
            'Libs/Tables/level.xml': b'<database name="barbora"><levels version="1"><LevelData LevelId="2" LevelName="trosecko"/></levels></database>'})
        output = self.root / 'campaign'
        with patch('setup_campaign.menu_assets', return_value=({}, {'synthetic': True})), \
                patch('upgrade_world_streaming.upgrade', return_value={'synthetic': True}) as scenery_upgrade:
            build_campaign(self.source, self.target, converted, 'probe', output)
        self.assertFalse(scenery_upgrade.call_args.kwargs['travel_mode'])
        receipt = verify_package(output)
        self.assertEqual(receipt['campaign_level'], 'kcd1_rataje')
        self.assertFalse(receipt['ready_to_play'])
        with zipfile.ZipFile(output / 'Data' / (MOD_ID + '.pak')) as z:
            registration = z.read('Libs/Tables/level__' + MOD_ID + '.xml')
            self.assertIn(b'LevelName="kcd1_rataje"', registration)
            self.assertNotIn(b'LevelName="trosecko"', registration)
        with zipfile.ZipFile(output / 'Data/kingdomcomegluemapper_sources.pak') as z:
            self.assertEqual(len(z.namelist()), len(OPENING_FILES))
            self.assertTrue(all(n.startswith('GlueMapper/CampaignSource/') for n in z.namelist()))
        campaign = json.loads((output / 'campaign.json').read_text())
        self.assertEqual(campaign['new_game']['level_package'], 'Levels/kcd1_rataje/level.pak')
        self.assertTrue(campaign['new_game']['native_dispatch_connected'])
        self.assertEqual(campaign['new_game']['spawn']['position'], '123,456,78')
        refreshed = self.root / 'refreshed'
        with patch('setup_campaign.menu_assets', return_value=({}, {'synthetic': True})):
            updated = refresh_runtime(output, refreshed, diagnostics=True)
        self.assertTrue(updated['diagnostics'])
        self.assertEqual(updated['campaign_level'], receipt['campaign_level'])
        self.assertEqual(updated['files']['campaign.json'], receipt['files']['campaign.json'])
        self.assertEqual(verify_package(output), receipt)
        self.assertEqual(verify_package(refreshed), updated)
        backup = self.root / 'runtime-backup'
        before = (refreshed / 'Data/Levels/kcd1_rataje/level.pak').read_bytes()
        with patch('setup_campaign.menu_assets', return_value=({}, {'synthetic': True})):
            updated = update_runtime(refreshed, backup, diagnostics=True, start_probe=True)
        self.assertEqual(verify_package(refreshed), updated)
        self.assertTrue(updated['start_probe'])
        self.assertEqual((backup / 'Data/Levels/kcd1_rataje/level.pak').read_bytes(), before)
        with zipfile.ZipFile(refreshed / 'Data/Levels/kcd1_rataje/level.pak') as z:
            shell = z.read('tables/ai/scheduler.xml')
            self.assertIn(b'Schedulers', shell)
            self.assertNotIn(b'NativeRecord', shell)
        for package in (output, refreshed):
            with zipfile.ZipFile(package / 'Data/Levels/kcd1_rataje/level.pak') as z:
                objects = ET.fromstring(z.read('objects_mission0.xml'))
                starts = objects.findall("Entity[@EntityClass='SpawnPoint']")
                self.assertEqual(len(starts), 1)
                self.assertEqual(starts[0].get('Pos'), '123,456,78')
                self.assertNotIn('EntityId', starts[0].attrib)
                self.assertNotIn('EntityGuid', starts[0].attrib)
                self.assertIsNotNone(objects.find("Entity[@Name='keep_house']"))
            with zipfile.ZipFile(package / 'Data' / (MOD_ID + '.pak')) as z:
                self.assertIn('Mods/' + MOD_ID + '/Data/Levels/kcd1_rataje/tables/ai/scheduler.xml', z.namelist())

    def test_opening_spawn_requires_unique_valid_retail_transform(self):
        self.assertEqual(opening_spawn(self.source)['rotation'], '1,0,0,0')
        for objects in (b'<Objects/>',
                        b'<Objects><Entity Name="spawnStart" EntityClass="SpawnPoint" Pos="nan,20,30"/></Objects>',
                        b'<Objects><Entity Name="spawnStart" EntityClass="SpawnPoint" Pos="10,20,30" Rotate="0,0,0,0"/></Objects>',
                        b'<Objects><Entity Name="spawnStart" EntityClass="SpawnPoint"/><Entity Name="spawnStart" EntityClass="SpawnPoint"/></Objects>'):
            with self.subTest(objects=objects):
                self.pak(self.source / 'Data/Levels/rataje/level.pak', {'objects_mission0.xml': objects})
                with self.assertRaises(ValueError):
                    opening_spawn(self.source)

    def test_failed_campaign_leaves_no_partial_output(self):
        output = self.root / 'failed-campaign'
        with patch('setup_campaign.menu_assets', return_value=({}, {})):
            with self.assertRaisesRegex(ValueError, 'Missing converted world'):
                build_campaign(self.source, self.target, self.root / 'missing', 'probe', output)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('.gluemapper-campaign-*')))

    def test_patch_revision_beats_prefix_sort(self):
        entry = OPENING_FILES['master'][1]
        self.pak(self.source / 'Data/patch/patch_010300.pak', {entry: b'<Old/>'})
        self.pak(self.source / 'Data/patch/ipl_patch_010900.pak', {entry: b'<New/>'})
        self.assertEqual(opening_sources(self.source)['master']['data'], b'<New/>')

    def test_ambiguous_patch_is_rejected(self):
        entry = OPENING_FILES['master'][1]
        self.pak(self.source / 'Data/patch/patch_010900.pak', {entry: b'<One/>'})
        self.pak(self.source / 'Data/patch/ipl_patch_010900.pak', {entry: b'<Two/>'})
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            opening_sources(self.source)

    def test_tampering_cannot_be_installed(self):
        output = self.build()
        (output / 'mod.manifest').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            install(output, self.target)
        self.assertFalse((self.target / 'Mods').exists())

    def test_path_escape_rejected(self):
        output = self.build()
        external = self.root / 'outside'
        external.write_bytes(b'data')
        receipt = json.loads((output / RECEIPT).read_text())
        receipt['files']['../outside'] = hashlib.sha256(b'data').hexdigest()
        (output / RECEIPT).write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'Invalid package path'):
            verify_package(output)

    def test_existing_mod_order_is_preserved(self):
        output = self.build()
        (self.target / 'Mods').mkdir()
        order = self.target / 'Mods/mod_order.txt'
        order.write_text('some_other_mod\n')
        with self.assertRaisesRegex(ValueError, 'excludes'):
            install(output, self.target)
        self.assertEqual(order.read_text(), 'some_other_mod\n')

    def test_version_mismatch_rejected(self):
        output = self.build()
        (self.target / 'system.cfg').write_text('wh_sys_version = "1.6"')
        with self.assertRaisesRegex(ValueError, 'version differs'):
            install(output, self.target)

    def test_uninstall_preserves_package_and_other_mods(self):
        output = self.build()
        destination = install(output, self.target)
        other = self.target / 'Mods/other_mod'
        other.mkdir()
        (other / 'important').write_text('keep')
        backup = self.root / 'backup'
        uninstall(self.target, backup)
        self.assertFalse(destination.exists())
        self.assertEqual(verify_package(backup), verify_package(output))
        self.assertEqual((other / 'important').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
