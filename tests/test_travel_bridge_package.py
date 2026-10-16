import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from lupa import LuaRuntime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from compare_packages import compare
from region_travel_companions import horse_recovery_resources
from region_travel_dialogue import patch as patch_dialogue
from setup_campaign import MOD_ID, RECEIPT, verify_package
from travel_bridge_package import combine, validate_bridge, LEVEL_TABLE, BRIDGE_PAK, PLAYER_HOOK


def pak(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as z:
        for name, data in files.items(): z.writestr(name, data)


class BridgePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root/'base'; self.base.mkdir()
        self.bridge = self.root/'bridge'; self.bridge.mkdir()
        self.native_level = b'<database><levels version="1"><level LevelId="1000"/></levels></database>'
        manifest = f'<kcd_mod><info><name>World</name><modid>{MOD_ID}</modid></info></kcd_mod>'
        for folder in (self.base, self.bridge): (folder/'mod.manifest').write_text(manifest)
        pak(self.base/f'Data/{MOD_ID}.pak', {
            'Libs/UI/Menu.gfx': b'old menu',
            'Scripts/Mods/kingdomcomegluemapper.lua': b'old startup',
            LEVEL_TABLE: self.native_level, 'unrelated/resource': b'keep'})
        for name in ('campaign-audit.json', 'campaign.json'):
            (self.base/name).write_text('{"world":{"asset_archives":[]}}')
        for name in (f'Data/{MOD_ID}_sources.pak', 'Data/Levels/kcd1_rataje/level.pak',
                     'Data/Levels/kcd1_rataje/terrain.pak', 'Data/Levels/kcd1_rataje/levelinfo.xml'):
            p = self.base/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'unchanged world')
        receipt = dict(schema=1, owner=MOD_ID, kind='retail-campaign-package', target_version='1.5.6',
                       files={p.relative_to(self.base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in self.base.rglob('*') if p.is_file()})
        (self.base/RECEIPT).write_text(json.dumps(receipt))
        dialogue = b'<Database><Skald><FaderDialog><Ports/><Dialogue><Decision><Sequences><Sequence><UiPrompt StringName="ui_prechod_z_seq1_Gr38"/></Sequence></Sequences></Decision></Dialogue></FaderDialog></Skald></Database>'
        host = b'<Database><Skald><Gameplay><Nodes/></Gameplay></Skald></Database>'
        files, strings = patch_dialogue(dialogue, host)
        files[LEVEL_TABLE] = b'<database><levels version="1"><level LevelId="1001"/></levels></database>'
        files[f'Libs/Tables/LevelSwitch__{MOD_ID}.xml'] = b'<database><LevelSwitches><LevelSwitchData Name="gluemappertravel_to_kcd1" TargetLevelId="1001"/></LevelSwitches></database>'
        files[f'Mods/{MOD_ID}/Data/Levels/kcd1_travel/tables/ai/scheduler.xml'] = b'<database/>'
        # The shipped player hook is what carries the bounded horse arrival code.
        files.update(horse_recovery_resources(
            b'function Player:OnInit() end\nfunction Player:OnLoadAI(saved) end\n',
            {'kcd1_travel': {'arrival': (1., 2., 3.), 'candidates': [{'position': (4., 5., 6.)}]}}))
        pak(self.bridge/BRIDGE_PAK, files)
        from region_travel_dialogue import merge_localization
        strings = merge_localization(b'<Table><Row><Cell>ui_prechod_z_seq1_Gr38</Cell><Cell>Original</Cell><Cell>Original</Cell></Row></Table>', strings)
        pak(self.bridge/'Localization/English_xml.pak', {'text_ui_dialog.xml': strings})

    def test_single_mod_preserves_world_and_rebuild_has_no_duplicate_levels(self):
        with patch('travel_bridge_package.level_registration', return_value=self.native_level):
            output = combine(self.base, self.bridge, 'target', self.root/'combined')
            again = combine(output, self.bridge, 'target', self.root/'again')
        verify_package(again); validate_bridge(again)
        with zipfile.ZipFile(again/f'Data/{MOD_ID}.pak') as z:
            self.assertEqual(z.read('unrelated/resource'), b'keep')
            self.assertNotIn('Libs/UI/Menu.gfx', z.namelist())
            levels = ET.fromstring(z.read(LEVEL_TABLE))[0]
            self.assertEqual([r.get('LevelId') for r in levels], ['1000', '1001'])
        with zipfile.ZipFile(again/BRIDGE_PAK) as z:
            self.assertNotIn(LEVEL_TABLE, z.namelist())
        self.assertEqual((again/'Data/Levels/kcd1_rataje/terrain.pak').read_bytes(), b'unchanged world')
        self.assertEqual((self.base/'Data/Levels/kcd1_rataje/terrain.pak').read_bytes(), b'unchanged world')
        with zipfile.ZipFile(self.base/f'Data/{MOD_ID}.pak') as z:
            self.assertIn('Libs/UI/Menu.gfx', z.namelist())

    def test_linked_world_matches_copy_and_never_changes_base(self):
        files = lambda root: {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        before = files(self.base)
        # This test compares exact bytes; ZIP write timestamps must match too.
        # The separate day-apart test verifies timestamp-independent comparison.
        with patch('travel_bridge_package.level_registration', return_value=self.native_level), \
             patch('time.time', return_value=1_790_000_000):
            linked = combine(self.base, self.bridge, 'target', self.root/'linked')
            with patch('travel_bridge_package.os.link', side_effect=OSError('no hard links')):
                copied = combine(self.base, self.bridge, 'target', self.root/'copied')
        self.assertEqual(files(self.base), before)
        self.assertEqual(files(linked), files(copied))
        world = 'Data/Levels/kcd1_rataje/terrain.pak'
        self.assertTrue((linked/world).samefile(self.base/world))
        for rewritten in (f'Data/{MOD_ID}.pak', 'mod.manifest', RECEIPT):
            self.assertFalse((linked/rewritten).samefile(self.base/rewritten))

    def test_rebuilds_a_day_apart_differ_only_in_write_times(self):
        with patch('travel_bridge_package.level_registration', return_value=self.native_level):
            with patch('time.time', return_value=1_790_000_000):
                first = combine(self.base, self.bridge, 'target', self.root/'first')
            with patch('time.time', return_value=1_790_090_000):
                second = combine(self.base, self.bridge, 'target', self.root/'second')
        self.assertNotEqual((first/RECEIPT).read_bytes(), (second/RECEIPT).read_bytes())
        self.assertEqual(compare(first, second, deep=True), [])

    def test_separate_mod_identity_rejected_before_copy(self):
        (self.bridge/'mod.manifest').write_text('<kcd_mod><info><modid>gluemappertravel</modid></info></kcd_mod>')
        with self.assertRaisesRegex(ValueError, 'modid='):
            combine(self.base, self.bridge, 'target', self.root/'combined')
        self.assertFalse((self.root/'combined').exists())

    def test_roof_repair_runs_after_assets_are_combined_and_is_receipted(self):
        receipt = json.loads((self.base/RECEIPT).read_text())
        receipt['sources'] = {'kcd1': 'source'}
        (self.base/RECEIPT).write_text(json.dumps(receipt))
        level = 'Data/Levels/kcd1_travel/level.pak'
        pak(self.bridge/level, {'original': b'level'})
        def activate(package, source):
            self.assertEqual(source, 'source')
            (package/'probe-activation-repair.json').write_text('{"restored_count": 1}')
            return {'restored_count': 1}
        def finish(package, source):
            self.assertEqual(source, 'source')
            self.assertTrue((package/'probe-activation-repair.json').is_file())
            self.assertTrue((package/f'Data/{MOD_ID}.pak').is_file())
            (package/level).write_bytes(b'repaired')
            (package/'roof-clip-repair.json').write_text('{}')
            return dict(roof_placements=3, changed_clip_count=2, linked_probe_count=1)
        with patch('travel_bridge_package.level_registration', return_value=self.native_level), \
             patch('probe_activation.repair_package', side_effect=activate) as probes, \
             patch('roof_clip_repair.repair_package', side_effect=finish) as repair, \
             patch('brush_registration.repair_package', return_value={'changed_brushes': 7}) as brushes, \
             patch('water_layers.repair_package', return_value={'removed_water_volumes': 4}) as water:
            output = combine(self.base, self.bridge, 'target', self.root/'combined')
        self.assertEqual(repair.call_count, 1)
        probes.assert_called_once_with(output, 'source')
        result = verify_package(output)
        self.assertEqual(result['files'][level], hashlib.sha256(b'repaired').hexdigest())
        self.assertIn('roof-clip-repair.json', result['files'])
        self.assertIn('probe-activation-repair.json', result['files'])
        self.assertEqual(result['travel_bridge']['probe_activation']['restored_count'], 1)
        self.assertEqual(result['travel_bridge']['roof_clipping']['changed_clip_count'], 2)
        brushes.assert_called_once_with(output)
        water.assert_called_once_with(output, 'source')
        self.assertEqual(result['travel_bridge']['water_layers']['removed_water_volumes'], 4)
        self.assertEqual(result['travel_bridge']['brush_registration']['changed_brushes'], 7)


    def test_bridge_pak_ships_the_player_hook_markers_and_road_table(self):
        with zipfile.ZipFile(self.bridge/BRIDGE_PAK) as z:
            self.assertIn(PLAYER_HOOK, z.namelist())
            player = z.read(PLAYER_HOOK)
        for marker in (b'GlueTravelHorseRecovery', b'GluePlayerAnimationUpdates'):
            self.assertIn(marker, player)
        table = next(line for line in player.split(b'\n') if line.startswith(b'GlueTravelHorseRoadArrivals='))
        arrivals = LuaRuntime(unpack_returned_tuples=True).execute(table.decode()+'\nreturn GlueTravelHorseRoadArrivals')
        self.assertEqual(list(arrivals.keys()), ['kcd1_travel'])
        self.assertEqual(arrivals['kcd1_travel']['arrival'].x, 1.)
        self.assertEqual(arrivals['kcd1_travel']['positions'][1].y, 5.)

    def test_validate_bridge_requires_the_player_hook(self):
        with zipfile.ZipFile(self.bridge/BRIDGE_PAK) as z:
            entries = [(info, z.read(info.filename)) for info in z.infolist()]
        with zipfile.ZipFile(self.bridge/BRIDGE_PAK, 'w', zipfile.ZIP_STORED) as z:
            for info, blob in entries:
                if info.filename != PLAYER_HOOK: z.writestr(info, blob)
        with self.assertRaisesRegex(ValueError, 'player hook'):
            validate_bridge(self.bridge)

    def test_bridge_without_level_table_raises_value_error(self):
        with zipfile.ZipFile(self.bridge/BRIDGE_PAK) as z:
            entries = [(info, z.read(info.filename)) for info in z.infolist()]
        with zipfile.ZipFile(self.bridge/BRIDGE_PAK, 'w', zipfile.ZIP_STORED) as z:
            for info, blob in entries:
                if info.filename != LEVEL_TABLE: z.writestr(info, blob)
        with patch('travel_bridge_package.level_registration', return_value=self.native_level):
            with self.assertRaisesRegex(ValueError, 'missing'):
                combine(self.base, self.bridge, 'target', self.root/'combined')


if __name__ == '__main__': unittest.main()
