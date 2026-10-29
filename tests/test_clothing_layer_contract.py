from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from character_layer_contract import region_layers, project_regions, source_mesh_regions, source_mesh_region
from clothing_lab.repair import repair_table
from clothing_coverage import converted_coverage_surface
from test_clothing_coverage import encoded
from test_clothing_lab import FakeCatalog, mesh, part
from clothing_lab.validation import validate
from clothing_regions import split_skin_regions
from character_skin_upgrade import CompiledSkin
from test_character_upgrade import sample_skin
from clothing_lab.catalog import Catalog
from types import MethodType


class NativeLayerTests(unittest.TestCase):
    def test_feature_only_role_uses_its_native_equipment_occupancy(self):
        role=ET.fromstring('<Clothing Name="MaterialLegs"><Features><Feature Name="Mail"/></Features></Clothing>')
        equipment=SimpleNamespace(layers={'native-id': {('legs','native-layer')}})
        self.assertEqual(source_mesh_regions(role,{},equipment,'native-id'), {'legs':'native-layer'})
        equipment.layers['native-id'].add(('legs','different-layer'))
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            source_mesh_regions(role,{},equipment,'native-id')

    def test_caparison_keeps_native_visual_region_instead_of_occupied_slots(self):
        role=ET.fromstring('<Clothing Name="HorseCover"><Elements><VClothElement EquipmentPart="horse_torso"/></Elements></Clothing>')
        equipment=SimpleNamespace(layers={'native-id': {('horse_back','1'),('horse_torso','1')}})
        contract=source_mesh_regions(role,{},equipment,'native-id')
        self.assertEqual(contract, {'horse_torso':None})
        self.assertEqual(source_mesh_region('horse_back',contract),'horse_torso')
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            source_mesh_region('legs',{'head':'1','feet':'2'})
        with self.assertRaisesRegex(ValueError,'no mesh'):
            source_mesh_regions(ET.fromstring('<Clothing Name="Missing"/>'),{},equipment,'native-id')

    def test_native_order_not_numeric_layer_ids_controls_geometry(self):
        for inner_layer,outer_layer in [('5','4'),('8','1')]:
            c=FakeCatalog()
            c.parts['dress']['attributes']['HidingGroups']=''
            c.parts['dress']['elements'][0]['BodyLayerId']=inner_layer
            c.parts['coat']=part('coat',layer=outer_layer)
            c.meshes['dress.skin']=mesh(.03)
            c.meshes['coat.skin']=mesh(.02) # outer garment is incorrectly inside inner
            result=validate(c,['body','dress','coat'],True)
            # The native attachment selects only the outer mesh, even when it
            # lies inside the unused lower geometry. Do not diagnose a draw
            # intersection with a mesh the engine never renders.
            self.assertEqual(next(m for m in result['meshes'] if m['component']=='dress')['visible_triangles'], 0)
            self.assertNotIn('outer_layer_under_inner',{f['code'] for f in result['findings']})
            self.assertEqual([f['components'] for f in result['findings'] if f['code']=='native_region_selection'], [['dress']])
            c.meshes['coat.skin']=mesh(.04)
            self.assertNotIn('outer_layer_under_inner',{f['code'] for f in validate(c,['body','dress','coat'],True)['findings']})

    def test_unknown_or_missing_layer_order_cannot_be_confirmed(self):
        c=FakeCatalog();c.parts['dress']['elements'][0]['BodyLayerId']='99'
        self.assertIn('unknown_native_layer',{f['code'] for f in validate(c,['body','dress'])['findings']})
        c.layer_order={}
        result=validate(c,['body','dress'],True)
        self.assertFalse(result['native_rules_checked'])
        self.assertIn('native_rules_unavailable',{f['code'] for f in result['findings']})

    def test_rigid_and_vcloth_layers_share_inheritance(self):
        root=ET.fromstring('''<Clothing Name="base"><Elements><VClothElement EquipmentPart="waist" BodyLayerId="5"/>
        <SkinElement EquipmentPart="torso" BodyLayerId="5"/></Elements><DerivedComponents>
        <Clothing Name="Tunic" ArmorType="Tunic"><Elements><SkinElement EquipmentPart="waist"/>
        <VClothElement EquipmentPart="torso" BodyLayerId="6"/></Elements></Clothing></DerivedComponents></Clothing>''')
        parents={c:p for p in root.iter() for c in p}
        self.assertEqual(region_layers(root.find('.//Clothing'),parents),dict(waist='5',torso='6'))
        self.assertEqual(project_regions(['arms','torso','waist'],dict(torso='7')),['torso']*3)

    def test_saved_layer_repair_is_owned_and_idempotent(self):
        native=ET.fromstring('<Clothing Name="Tunic" ArmorType="Tunic"><Elements><VClothElement EquipmentPart="waist" BodyLayerId="5"/></Elements></Clothing>')
        data=b'<database><CharacterComponents><Component Name="worlditems_cloth_x_root" Race="Human" Gender="Male" FilePath="gluetravel/worlditems/x/"><DerivedComponents><Clothing Name="worlditems_cloth_x" ArmorType="Tunic"><Elements><SkinElement EquipmentPart="waist"/></Elements></Clothing></DerivedComponents></Component></CharacterComponents></database>'
        fixed,files,rows=repair_table(data,native,None)
        self.assertEqual(ET.fromstring(fixed).find('.//SkinElement').get('BodyLayerId'),'5')
        self.assertEqual(len(rows),1);self.assertEqual(files,{})
        self.assertEqual(repair_table(fixed,native,None),(fixed,{},[]))
        unowned=data.replace(b'worlditems_cloth_x_root',b'native_x')
        self.assertEqual(repair_table(unowned,native,None),(unowned,{},[]))

    def test_cached_source_opaque_triangle_still_checks_converted_cutout(self):
        skin=mesh(.02)
        rows=[dict(skin='a',material='a',opaque_triangle_mask=encoded([1]),not_culled_triangle_mask=encoded([1]))]
        with patch('clothing_coverage.native_alpha_surfaces',return_value=([(0.1,[np.zeros((4,4))])],['converted'])):
            selected,accept,evidence=converted_coverage_surface(skin,rows,'a','a',None,None,None)
        self.assertTrue(selected[0])
        self.assertFalse(accept(np.array([0]),np.array([.2]),np.array([.2]))[0])
        self.assertEqual(evidence,['converted'])

    def test_saved_unsupported_regions_merge_without_losing_triangles(self):
        native=ET.fromstring('<Clothing Name="Plate" ArmorType="Plate"><Elements><SkinElement EquipmentPart="torso" BodyLayerId="7"/></Elements></Clothing>')
        partitions=split_skin_regions(sample_skin(),['torso','arms','torso','arms'])
        assets=SimpleNamespace(read=lambda name:partitions[Path(name).stem])
        data=b'<database><CharacterComponents><Component Name="worlditems_cloth_x_root" Race="Human" Gender="Male" FilePath="gluetravel/worlditems/x/"><DerivedComponents><Clothing Name="worlditems_cloth_x" ArmorType="Plate"><Elements><SkinElement EquipmentPart="torso" Model="torso.skin" Material="a.mtl" BodyLayerId="7"/><SkinElement EquipmentPart="arms" Model="arms.skin" Material="a.mtl"/></Elements></Clothing></DerivedComponents></Component></CharacterComponents></database>'
        fixed,files,rows=repair_table(data,native,assets)
        elements=ET.fromstring(fixed).findall('.//SkinElement')
        self.assertEqual(len(elements),1);self.assertEqual(elements[0].get('EquipmentPart'),'torso')
        merged=CompiledSkin(next(iter(files.values())))
        self.assertEqual(len(merged.faces),4)
        self.assertEqual(rows[0]['changes'][0]['before_triangles'],rows[0]['changes'][0]['after_triangles'])
        original_points=np.concatenate([CompiledSkin(blob).vertices[CompiledSkin(blob).faces].reshape(-1,3) for blob in partitions.values()])
        actual=merged.vertices[merged.faces].reshape(-1,3)
        self.assertEqual(sorted(map(tuple,original_points)),sorted(map(tuple,actual)))

    def test_cached_geometry_matches_uncached_masked_and_unmasked_results(self):
        c=FakeCatalog()
        c.materials=lambda name:[dict(shader='Illum',texture='cloth.dds',threshold=.1,opacity=1)]
        expected=validate(c,['body','dress'],True)
        c.coverage_surface=MethodType(Catalog.coverage_surface,c)
        c.coverage_hits=MethodType(Catalog.coverage_hits,c)
        actual=validate(c,['body','dress'],True)
        self.assertEqual(actual,expected)
        c.parts['dress']['attributes']['HidingGroups']=''
        cached=validate(c,['body','dress'],True)
        del c.coverage_surface;del c.coverage_hits
        self.assertEqual(cached,validate(c,['body','dress'],True))

    def test_unused_partition_vertices_are_not_skin_holes(self):
        c=FakeCatalog();skin=c.meshes['body.skin']
        # The retained fourth vertex is not referenced by this region's faces.
        skin.n=4;skin.ninternal=4;skin.vertices=np.vstack((skin.vertices,[5,5,5]));skin.remap=np.arange(4)
        skin.streams[3].data+=bytes([255,255,255,239])
        result=validate(c,['body','dress'],True)
        self.assertNotIn('hidden_skin_without_cover',{f['code'] for f in result['findings']})
        self.assertEqual(result['meshes'][0]['referenced_vertices'],3)

    def test_native_shared_masks_are_not_private_outfit_masks(self):
        c=FakeCatalog();c.private_groups=set();c.parts['dress']['family']='another'
        self.assertNotIn('cross_outfit_masks',{f['code'] for f in validate(c,['body','dress'])['findings']})
        c.private_groups={'cover'}
        self.assertIn('cross_outfit_masks',{f['code'] for f in validate(c,['body','dress'])['findings']})

    def test_translucent_material_does_not_prove_coverage(self):
        c=FakeCatalog();c.materials=lambda p:[dict(texture='cloth.dds',threshold=.1,opacity=.5)]
        self.assertIn('opacity_unverified',{f['code'] for f in validate(c,['body','dress'],True)['findings']})


if __name__=='__main__':unittest.main()
