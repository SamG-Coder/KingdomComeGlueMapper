import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from retail_pak import RetailPakWriter, PakSet, validate_paks
from region_travel_policy import update_resource, JOURNEY_HOURS, LABELS
from travel_world_package import prepare_world_for_travel
import xml.etree.ElementTree as E


class RetailPakTests(unittest.TestCase):
    def test_roundtrip_splits_by_size_and_entry_count_without_zip64(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'Data'; data.mkdir()
            entries = {f'objects/{i}.dds': os.urandom(400) for i in range(7)}
            with RetailPakWriter(data, 'test', max_bytes=1400, max_entries=2) as out:
                for name, payload in entries.items(): out.writestr(name, payload)
            self.assertGreater(len(list(data.glob('*.pak'))), 1)
            for p in data.glob('*.pak'):
                self.assertLessEqual(p.stat().st_size, 1400)
                with zipfile.ZipFile(p) as z:
                    self.assertLessEqual(len(z.filelist), 2)
                    self.assertTrue(all(i.extract_version < 45 for i in z.filelist))
            with PakSet(data) as z:
                self.assertEqual({n: z.read(n) for n in z.namelist()}, entries)
            self.assertTrue(validate_paks(tmp))

    def test_duplicate_across_archives_and_oversized_member_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            with RetailPakWriter(tmp, 'test', max_bytes=1024) as out:
                with self.assertRaises(ValueError): out.writestr('huge.dds', b'a' * 1024)
            for n in ('a', 'b'):
                with zipfile.ZipFile(Path(tmp) / (n + '.pak'), 'w') as z: z.writestr('duplicate', b'x')
            with self.assertRaises(ValueError):
                with PakSet(tmp): pass

    def test_native_travel_preserved_and_custom_routes_gain_elapsed_time(self):
        source = b'<database><LevelSwitches><LevelSwitchData Name="trosecko" WorldTimeDurationInHours="100"/><LevelSwitchData Name="gluemappertravel_to_kcd1"/><LevelSwitchData Name="gluemappertravel_to_trosky"/></LevelSwitches></database>'
        root = E.fromstring(update_resource('Libs/Tables/LevelSwitch.xml', source))
        self.assertEqual(root[0][0].get('WorldTimeDurationInHours'), '100')
        self.assertEqual([r.get('WorldTimeDurationInHours') for r in root[0][1:]], [str(JOURNEY_HOURS)] * 2)
        text = b'<Table><Row><Cell>native</Cell><Cell>Keep</Cell><Cell>Keep</Cell></Row><Row><Cell>ui_gluemapper_travel_kcd1</Cell><Cell>Old</Cell><Cell>Old</Cell></Row></Table>'
        changed = E.fromstring(update_resource('text_ui_dialog.xml', text))
        self.assertEqual(changed[0][1].text, 'Keep')
        self.assertEqual(changed[1][2].text, LABELS['ui_gluemapper_travel_kcd1'])

    def test_travel_world_omits_campaign_menu_and_keeps_world_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'Data'; data.mkdir()
            path = data / 'kingdomcomegluemapper.pak'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('Libs/UI/Menu.gfx', b'oldmenu')
                z.writestr('Libs/UI/UIElements/Menu.xml', b'oldinterface')
                z.writestr('Scripts/Mods/kingdomcomegluemapper.lua', b'AddBasicButton Play KDC1')
                z.writestr('Libs/Tables/level.xml', b'level registration')
            prepare_world_for_travel(tmp)
            with zipfile.ZipFile(path) as z:
                self.assertNotIn('Libs/UI/Menu.gfx', z.namelist())
                self.assertNotIn(b'AddBasicButton', z.read('Scripts/Mods/kingdomcomegluemapper.lua'))
                self.assertEqual(z.read('Libs/Tables/level.xml'), b'level registration')


if __name__ == '__main__': unittest.main()
