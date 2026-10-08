import sys
from pathlib import Path
import unittest
import struct
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_npc_probe import asset_path, resolve_parts, bind_material
from static_assets import StagedAssets


class NpcProbeTests(unittest.TestCase):
    def test_flat_material_expands_to_authored_subset_slot(self):
        name = b'wh_geometry'.ljust(128,b'\0')
        subsets = bytearray(52)
        struct.pack_into('<I',subsets,4,1)
        struct.pack_into('<I',subsets,32,4)
        blob = bytearray(b'CrCh'+struct.pack('<III',0x746,2,16))
        blob += struct.pack('<HHIII',0x1014,0x802,1,128,48)
        blob += struct.pack('<HHIII',0x1017,0x800,2,52,176)
        blob += name+subsets
        with tempfile.TemporaryDirectory() as directory:
            emitted=StagedAssets(directory)
            emitted['base.mtl']=b'<Material Shader="Illum"/>'
            result,mat=bind_material(blob,'base',emitted,'slots')
            self.assertEqual(mat,'slots')
            self.assertEqual(result[48:176],b'slots'.ljust(128,b'\0'))
            self.assertEqual(result[176:],subsets)
            self.assertEqual(len(ET.parse(emitted['slots.mtl']).findall('SubMaterials/Material')),5)

    def test_human_material_does_not_resolve_to_horse_material(self):
        index = {'objects/characters/humans/body/common.mtl': None,
                 'objects/characters/animals/horse/body/common.mtl': None}
        self.assertEqual(asset_path(index, {'kind': 'body', 'material': 'common'},
                                    'material', '.mtl'),
                         'objects/characters/humans/body/common.mtl')

    def test_ambiguous_asset_is_rejected(self):
        with self.assertRaises(ValueError):
            asset_path({'objects/characters/humans/head/a/shared.skin': None,
                        'objects/characters/humans/head/b/shared.skin': None},
                       {'kind': 'head', 'model': 'shared'}, 'model', '.skin')

    def test_preset_resolves_both_clothing_slots_only_for_selected_armor(self):
        tables = {
            'character_body': [{'character_body_id': 'body', 'model': 'body'}],
            'item/armor2clothing_preset': [dict(armor_id='a', clothing_preset_id='p'),
                                         dict(armor_id='b', clothing_preset_id='other')],
            'item/armor': [dict(item_id='a', clothing_id='c', clothing2_id='d'),
                           dict(item_id='b', clothing_id='unselected')],
            'item/clothing': [dict(clothing_id='c', model='shirt'),
                              dict(clothing_id='d', model='sleeves')]}
        parts = resolve_parts(dict(character_body_id='body', initial_clothing_preset_id='p'),
                              tables.__getitem__)
        self.assertEqual([p['model'] for p in parts], ['body', 'shirt', 'sleeves'])


if __name__ == '__main__':
    unittest.main()
