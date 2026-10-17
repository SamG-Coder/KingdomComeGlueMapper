"""Male fixed outfits use the same native clothing materials as female ones (issue #1)."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

STEAM = Path(r'C:\Program Files (x86)\Steam\steamapps\common')
KCD1 = Path(os.environ.get('GLUEMAPPER_KCD1', STEAM / 'KingdomComeDeliverance'))
KCD2 = Path(os.environ.get('GLUEMAPPER_KCD2', STEAM / 'KingdomComeDeliverance2'))


@unittest.skipUnless((KCD1 / 'Data/Levels/rataje/level.pak').is_file() and (KCD2 / 'Data/Tables.pak').is_file(),
                     'KCD1 and KCD2 retail data not installed')
class MalePersonMaterialTests(unittest.TestCase):
    def test_male_outfit_gets_native_clothing_materials(self):
        from character_person import resolve_person
        from character_person_appearance import build_appearance
        person = resolve_person(KCD1, 'ska_collierVarlet', 'rataje')
        namespace = 'gm_person_test_male'
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / namespace
            result = build_appearance(KCD1, KCD2, person, namespace, output)
            self.assertEqual(result['gender'], 'male')
            clothing = [m for m in result['materials'] if m['source_shader'] == 'illum']
            self.assertTrue(clothing)
            for material in clothing:
                self.assertIn('%CLOTHING_SYSTEM', material['target_flags'])
            self.assertTrue((output / ('Libs/Tables/Character/ClothingMaterial__' + namespace + '.xml')).is_file())
            component = ET.parse(output / ('Libs/Tables/Character/CharacterComponent__' + namespace + '.xml'))
            self.assertTrue(any(f.get('Name') == 'GarmentBase' for f in component.iter('Feature')))


if __name__ == '__main__':
    unittest.main()
