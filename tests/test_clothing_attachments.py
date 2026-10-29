from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from character_clothing_attachments import resolve_attachments, merge_details, coalesce_material_slots
from character_skin_upgrade import CompiledSkin, upgrade_skin
from clothing_regions import split_skin_regions
from test_character_upgrade import sample_skin


class ClothingAttachmentTests(unittest.TestCase):
    def test_material_aliases_do_not_duplicate_textures_or_change_face_coverage(self):
        skin,_=upgrade_skin(sample_skin(),sample_skin(),clear_hiding=True)
        material=ET.fromstring('<Material><SubMaterials>'+'<Material Shader="Illum" AlphaTest=".1"/>'*10+'</SubMaterials></Material>')
        family, result=coalesce_material_slots({0:skin,1:skin},material)
        self.assertEqual(len(result.find('SubMaterials')),1)
        for blob in family.values():
            before,after=CompiledSkin(skin),CompiledSkin(blob)
            np.testing.assert_array_equal(before.faces,after.faces)
            np.testing.assert_array_equal(before.vertices,after.vertices)
            self.assertEqual({r['material_id'] for r in after.info['subset_records']},{0})
        self.assertEqual(len(material.find('SubMaterials')),10)

    def test_armour_links_preserve_attachment_tint_gender_and_single_ownership(self):
        data = {'item/clothing_attachment':[
            dict(clothing_attachment_id='band',gender_id='1',model='strap',material='leather'),
            dict(clothing_attachment_id='female',gender_id='2',model='strap2',material='leather')],
            'item/armor2clothing_attachment':[
                dict(item_id='apron',clothing_attachment_id='band',color_hue='.25'),
                dict(item_id='apron',clothing_attachment_id='female')]}
        table = SimpleNamespace(get=lambda name:dict(rows=data[name]))
        parts = [dict(armor_id='apron',gender_id='1')]*2
        result = resolve_attachments(parts,table)
        self.assertEqual(list(result),[0])
        self.assertEqual(result[0][0]['model'],'strap')
        self.assertEqual(result[0][0]['armor_settings']['color_hue'],'.25')
        data['item/clothing_attachment'].pop(0)
        with self.assertRaisesRegex(ValueError,'Missing source clothing attachment'):
            resolve_attachments(parts,table)

    def test_attached_geometry_materials_and_lods_join_the_owning_garment(self):
        skin,_ = upgrade_skin(sample_skin(),sample_skin(),clear_hiding=True)
        lower = split_skin_regions(skin,['keep','discard','discard','discard'])['keep']
        material = ET.fromstring('<Material><SubMaterials>'+ '<Material Shader="Illum"/>'*10 +'</SubMaterials></Material>')
        owner = dict(name='garment',region='torso',skin=skin,lods={},material=material,
                     source_coverage={'outer':np.ones(6,bool)},lod_coverage={})
        detail = dict(name='strap',skin=skin,lods={1:lower},material=material)
        original = CompiledSkin(skin)
        merge_details(owner,[detail],'test/garment')
        self.assertEqual(len(CompiledSkin(owner['skin']).faces),8)
        self.assertEqual(len(CompiledSkin(owner['lods'][1]).faces),5)
        self.assertEqual(len(owner['material'].find('SubMaterials')),20)
        for level,blob in {0:owner['skin'],**owner['lods']}.items():
            result=CompiledSkin(blob)
            np.testing.assert_array_equal(result.vertices[:6],original.vertices)
            np.testing.assert_array_equal(result.vertices[6:],original.vertices)
            membership=owner['source_coverage'] if level==0 else owner['lod_coverage'][level]
            np.testing.assert_array_equal(membership['outer'],[True]*6+[False]*6)
            records=result.info['subset_records']
            self.assertTrue(any(s['material_id']>=10 for s in records))


if __name__=='__main__':unittest.main()
