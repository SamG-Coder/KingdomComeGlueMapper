import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from region_travel_dialogue import patch as patch_dialogue
from setup_campaign import MOD_ID, RECEIPT, verify_package
from travel_bridge_package import combine, validate_bridge, LEVEL_TABLE, BRIDGE_PAK


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

    def test_separate_mod_identity_rejected_before_copy(self):
        (self.bridge/'mod.manifest').write_text('<kcd_mod><info><modid>gluemappertravel</modid></info></kcd_mod>')
        with self.assertRaisesRegex(ValueError, 'modid='):
            combine(self.base, self.bridge, 'target', self.root/'combined')
        self.assertFalse((self.root/'combined').exists())


if __name__ == '__main__': unittest.main()
