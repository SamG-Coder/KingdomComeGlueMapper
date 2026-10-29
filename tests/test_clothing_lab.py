"""Developer lab contracts: inheritance, assets, coverage, enumeration and source completeness."""
import base64
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from clothing_lab.assets import Assets, key
from clothing_lab.catalog import resolved_nodes, Catalog
from clothing_lab.validation import active_masks, validate, plan, element_replaced
from clothing_lab.geometry import Surface
from clothing_coverage import covered_points
from clothing_lab.service import Lab
from clothing_lab.source import soul_records
from npc_effects import write_float_dds
from texture_streaming import split_generated


def part(name,kind='Clothing',layer='5',gender='Female',family='person',groups=''):
    return dict(id=name,kind=kind,label=name,family=family,gender=gender,
        attributes=dict(HidingGroups=groups,**(dict(ArmorType='Dress',ArmorArchetypeId='27') if kind=='Clothing' else {})),
        elements=[dict(EquipmentPart='torso',BodyLayerId=layer,KeepBodyLayer='true',ModelResolved=name+'.skin',MaterialResolved='cloth.mtl')])


def mesh(z=0,hidden=False):
    vertices=np.array([[-.5,-.5,z],[.5,-.5,z],[0,.5,z]],float)
    rgba=np.full((3,4),255,np.uint8)
    if hidden:rgba[:,3]=239
    uv=np.full((3,2),.5,dtype='<f4')
    return SimpleNamespace(n=3,ninternal=3,vertices=vertices,faces=np.array([[0,1,2]]),remap=np.arange(3),
        info=dict(subset_records=[dict(first_index=0,num_indices=3,material_id=0)]),
        streams={3:SimpleNamespace(data=bytes(24)+rgba.tobytes()),2:SimpleNamespace(data=struct.pack('<6I',0,2,3,8,0,0)+uv.tobytes())})


class FakeCatalog:
    def __init__(self):
        self.parts={'body':part('body','Body','0'),'dress':part('dress',groups='cover')}
        self.groups={'cover':dict(index=4,region='torso')}
        self.native_roles={'Dress':dict(archetype='27',gender='Female',layers=[('torso','5')])}
        self.layer_order={'0':0,'1':2,'2':3,'3':4,'4':8,'5':5,'6':6,'7':7,'8':1,'9':9}
        self.presets={'person':dict(id='person',label='actor',gender='Female',parts=['body','dress'])}
        self.meshes={'body.skin':mesh(hidden=True),'dress.skin':mesh(.04)}
        self.assets=SimpleNamespace(index={'cloth.dds':None},pixels=lambda p:(np.ones((4,4,4)),False))
    def mesh(self,name):return self.meshes[name]
    def materials(self,name):return [dict(texture='cloth.dds',threshold=.1)]


class ClothingLabTests(unittest.TestCase):
    def test_head_mask_audit_catches_inner_collar_and_respects_opaque_outer_cover(self):
        c = FakeCatalog()
        c.parts['body']['kind'] = 'Head'
        c.meshes['dress.skin'] = mesh(-.02)
        c.meshes['dress.skin'].vertices[:,:2] *= 2
        code = 'hidden_head_without_exterior_cover'
        findings = validate(c, ['body','dress'], geometry=True)['findings']
        self.assertEqual(next(f for f in findings if f['code']==code)['vertices'], 3)
        c.meshes['dress.skin'].vertices[:,2] = .02
        self.assertNotIn(code, {f['code'] for f in validate(c,['body','dress'],geometry=True)['findings']})
        c.assets.pixels = lambda _: (np.zeros((4,4,4)), False)
        self.assertIn(code, {f['code'] for f in validate(c,['body','dress'],geometry=True)['findings']})
        self.assertNotIn(code, {f['code'] for f in validate(c,['body'],geometry=True)['findings']})

    def test_equipped_nonvisual_accessory_is_valid_without_a_mesh(self):
        from test_clothing_inventory import fixture
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Data').mkdir()
            resources=fixture()
            resources['Libs/Tables/Character/CharacterComponent__person.xml']=(
                '<database><CharacterComponents><Component Name="person_root" FilePath="test"><DerivedComponents>'
                '<Body Name="body"><Elements><SkinElement EquipmentPart="torso" Model="body.skin"/></Elements></Body>'
                '<Clothing Name="garment"/>'
                '</DerivedComponents></Component></CharacterComponents></database>')
            with zipfile.ZipFile(root/'Data/test.pak','w') as z:
                for name,blob in resources.items(): z.writestr(name,blob)
                z.writestr('Libs/GlueMapper/PersonImport/person.json',json.dumps(dict(namespace='person',person='actor')))
            c=Catalog(root)
            self.assertEqual(c.errors,[])
            self.assertEqual(c.presets['person_root']['parts'],['body'])
            self.assertEqual(c.presets['person_root']['equipment_status'],'checked')

    def test_outfit_validation_fails_when_meshes_exist_but_gameplay_cannot_equip_them(self):
        c=FakeCatalog()
        c.presets['person_root']=c.presets.pop('person')
        c.inventory_checks=[dict(namespace='person',person='actor',status='error',
            issues=[dict(code='equipment_reference_missing',message='Missing carried inventory')])]
        result=validate(c,['body','dress'],inspect_assets=False)
        self.assertEqual(result['status'],'error')
        self.assertIn('equipped_outfit_unreachable',{f['code'] for f in result['findings']})

    def test_source_import_receipt_labels_named_legacy_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Data').mkdir()
            component=b'<database><CharacterComponents><Component Name="original_alias_root" FilePath="test"><DerivedComponents><Body Name="body"><Elements><SkinElement EquipmentPart="torso" Model="body.skin"/></Elements></Body></DerivedComponents></Component></CharacterComponents></database>'
            with zipfile.ZipFile(root/'Data/test.pak','w') as z:
                z.writestr('Libs/Tables/Character/CharacterComponent__alias.xml',component)
                z.writestr('Libs/GlueMapper/PersonImport/original_alias.json',json.dumps(dict(namespace='original_alias',person='source_person')))
            c=Catalog(root)
            self.assertEqual(c.presets['original_alias_root']['label'],'source_person')
            self.assertEqual({e['code'] for e in c.errors},
                {'equipment_storm_binding_missing', 'equipment_outfit_unreachable'})
            self.assertEqual(c.inventory_checks[0]['status'], 'error')

    def test_hidden_garment_without_cover_is_not_reported_as_clean(self):
        c = FakeCatalog(); c.meshes['dress.skin'] = mesh(.04, hidden=True)
        result = validate(c, ['body','dress'], geometry=True)
        issue = next(f for f in result['findings'] if f['code']=='hidden_cloth_without_cover')
        self.assertEqual(issue['triangles'], 1)
        self.assertEqual(issue['sample_triangles'], [0])
        # A surviving surface in another region of a composed outfit covers it.
        c.parts['dress']['elements'].append(dict(c.parts['dress']['elements'][0], EquipmentPart='arms', ModelResolved='sleeve.skin'))
        c.meshes['sleeve.skin'] = mesh(.06)
        self.assertNotIn('hidden_cloth_without_cover', {f['code'] for f in validate(c,['body','dress'],geometry=True)['findings']})

    def test_hidden_transparent_or_nodraw_cloth_is_not_a_missing_surface(self):
        c = FakeCatalog(); c.meshes['dress.skin'] = mesh(.04, hidden=True)
        c.assets.pixels = lambda _: (np.zeros((4,4,4)), False)
        self.assertNotIn('hidden_cloth_without_cover', {f['code'] for f in validate(c,['body','dress'],geometry=True)['findings']})
        c.materials = lambda _: [dict(shader='Nodraw')]
        self.assertNotIn('hidden_cloth_without_cover', {f['code'] for f in validate(c,['body','dress'],geometry=True)['findings']})

    def test_unmasked_hair_under_headwear_is_a_review_candidate(self):
        c = FakeCatalog(); c.parts['hair'] = part('hair', 'Hair', '0')
        c.parts['hair']['elements'][0]['EquipmentPart'] = 'head'
        c.parts['dress']['elements'][0]['EquipmentPart'] = 'head'
        c.groups['cover']['region'] = 'head'
        c.meshes['hair.skin'] = mesh(.02)
        selected = ['hair','dress']
        self.assertIn('unmasked_hair_under_headwear', {f['code'] for f in validate(c,selected,geometry=True)['findings']})
        c.meshes['hair.skin'] = mesh(.02, hidden=True)
        self.assertNotIn('unmasked_hair_under_headwear', {f['code'] for f in validate(c,selected,geometry=True)['findings']})

    def test_duplicate_native_material_feature_declarations_fail_validation(self):
        c=object.__new__(Catalog)
        c.assets=SimpleNamespace(read=lambda _:b'<Material><SubMaterials><Material><FeatureSlots Slot00="Fabric"/></Material><Material><FeatureSlots Slot00="Fabric"/></Material></SubMaterials></Material>')
        with self.assertRaisesRegex(ValueError,'multiple submaterials'):
            c.materials('duplicate.mtl')

    def test_native_selects_one_region_mesh_but_hidden_inner_mask_still_applies(self):
        c=FakeCatalog()
        c.parts['outer']=part('outer',layer='6')
        c.meshes['outer.skin']=mesh(.04)
        c.meshes['outer.skin'].vertices[:,0]+=5  # Short sleeves leave this skin uncovered.
        selected=['body','dress','outer']
        self.assertTrue(element_replaced(c,selected,c.parts['dress'],c.parts['dress']['elements'][0]))
        self.assertFalse(element_replaced(c,selected,c.parts['outer'],c.parts['outer']['elements'][0]))
        self.assertEqual(active_masks(c,selected),{'torso':16})
        result=validate(c,selected,geometry=True)
        self.assertEqual(next(m for m in result['meshes'] if m['component']=='dress')['visible_triangles'],0)
        self.assertIn('hidden_skin_without_cover',{f['code'] for f in result['findings']})
        self.assertNotIn('hidden_skin_without_cover',{f['code'] for f in validate(c,['body','dress'],geometry=True)['findings']})

    def test_native_uses_layer_order_not_id_and_only_winning_keep_body(self):
        c=FakeCatalog()
        c.parts['underwear']=part('underwear',layer='8')  # native order 1, below layer 5
        c.parts['underwear']['elements'][0]['KeepBodyLayer']='false'
        selected=['body','dress','underwear']
        self.assertTrue(element_replaced(c,selected,c.parts['underwear'],c.parts['underwear']['elements'][0]))
        self.assertFalse(element_replaced(c,selected,c.parts['body'],c.parts['body']['elements'][0]))

    def test_material_culling_matches_native_two_sided_flag(self):
        c=object.__new__(Catalog)
        c.assets=SimpleNamespace(read=lambda _:b'<Material><SubMaterials><Material Shader="HumanSkin" MtlFlags="524288"/><Material Shader="Illum" MtlFlags="524290"/></SubMaterials></Material>')
        self.assertEqual([m['two_sided'] for m in c.materials('culling.mtl')],[False,True])

    def test_native_skinning_buffer_disagreement_is_an_error_even_without_geometry_audit(self):
        c=FakeCatalog()
        c.meshes['dress.skin'].native_colour_mismatches=lambda:np.array([0,2])
        result=validate(c,['body','dress'])
        issue=next(f for f in result['findings'] if f['code']=='native_skinning_colour_mismatch')
        self.assertEqual(issue['severity'],'error')
        self.assertEqual(issue['sample_vertices'],[0,2])
        c.meshes['dress.skin'].native_colour_mismatches=lambda:np.array([],dtype=int)
        self.assertNotIn('native_skinning_colour_mismatch',{f['code'] for f in validate(c,['body','dress'])['findings']})

    def test_lod_selection_uses_each_parts_authored_chain_and_reports_the_effective_level(self):
        catalog=object.__new__(Catalog)
        catalog.assets=SimpleNamespace(index={name:None for name in ('body.skin','head.skin','head_lod1.skin','head_lod2.skin')})
        self.assertEqual(catalog.model_at_lod('body.skin',2),('body.skin',0))
        self.assertEqual(catalog.model_at_lod('head.skin',1),('head_lod1.skin',1))
        self.assertEqual(catalog.model_at_lod('head.skin',4),('head_lod2.skin',2))
        with self.assertRaisesRegex(ValueError,'Missing base'):
            catalog.model_at_lod('missing.skin',1)
        catalog.assets.index['broken.skin']=None;catalog.assets.index['broken_lod2.skin']=None
        with self.assertRaisesRegex(ValueError,'Non-contiguous'):
            catalog.model_at_lod('broken.skin',2)

    def test_inherited_layer_and_asset_paths_resolve(self):
        doc=ET.fromstring('''<database><CharacterComponents><Component Name="Female" Gender="Female" Race="Human" FilePath="humans/female/">
        <DerivedComponents><Clothing Name="Dress" ArmorType="Dress" ArmorArchetypeId="27"><Elements><SkinElement EquipmentPart="torso" BodyLayerId="5" Model="base.skin" KeepBodyLayer="true"/></Elements>
        <DerivedComponents><Clothing Name="green"><Elements><SkinElement EquipmentPart="torso" Model="green.skin" Material="green.mtl"/></Elements></Clothing></DerivedComponents>
        </Clothing></DerivedComponents></Component></CharacterComponents></database>''')
        node=list(resolved_nodes(doc))[-1]
        self.assertEqual(node['attributes']['ArmorArchetypeId'],'27')
        self.assertEqual(node['elements'][0]['BodyLayerId'],'5')
        self.assertEqual(node['elements'][0]['ModelResolved'],'objects/characters/humans/female/green.skin')

    def test_unsafe_paths_and_ambiguous_layers_fail(self):
        for path in ('../secret','C:/secret','/secret','a/../secret'):
            with self.assertRaises(ValueError):key(path)
        doc=ET.fromstring('<database><CharacterComponents><Clothing Name="a"><Elements><SkinElement EquipmentPart="torso" BodyLayerId="1"/><SkinElement EquipmentPart="torso" BodyLayerId="2"/></Elements><DerivedComponents><Clothing Name="b"><Elements><SkinElement EquipmentPart="torso"/></Elements></Clothing></DerivedComponents></Clothing></CharacterComponents></database>')
        with self.assertRaisesRegex(ValueError,'Ambiguous'):list(resolved_nodes(doc))

    def test_split_float_texture_alpha_is_reconstructed_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Data').mkdir();pixels=np.ones((64,64,4),np.float32);pixels[10:30,10:30,3]=0
            write_float_dds(root/'cloth.dds',pixels)
            family=split_generated((root/'cloth.dds').read_bytes())
            with zipfile.ZipFile(root/'Data/a.pak','w') as z:
                for suffix,data in family.items():z.writestr('cloth.dds'+suffix,data)
            assets=Assets(root);image,linear=assets.pixels('cloth.dds')
            self.assertTrue(linear);np.testing.assert_array_equal(image[:,:,3],pixels[:,:,3])
            self.assertTrue(assets.texture('cloth.dds').startswith(b'\x89PNG'))
            with zipfile.ZipFile(root/'Data/b.pak','w') as z:z.writestr('cloth.dds',b'conflict')
            with self.assertRaisesRegex(ValueError,'Conflicting'):Assets(root).read('cloth.dds')

    def test_package_changed_after_indexing_requires_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Data').mkdir();archive=root/'Data/a.pak'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('mesh.skin',b'first')
            assets=Assets(root)
            with zipfile.ZipFile(archive,'w') as z:z.writestr('mesh.skin',b'changed')
            with self.assertRaisesRegex(ValueError,'Package changed'):assets.read('mesh.skin')

    def test_same_native_slot_and_wrong_gender_are_not_valid_combinations(self):
        c=FakeCatalog();c.parts['other']=part('other',gender='Male');c.meshes['other.skin']=mesh(.06)
        result=validate(c,['body','dress','other'])
        self.assertEqual(result['status'],'error')
        codes={f['code'] for f in result['findings']}
        self.assertTrue({'gender_conflict','same_layer_overlap','native_role_mismatch','shared_armor_archetype'}<=codes)

    def test_transparent_cloth_does_not_justify_hiding_skin(self):
        c=FakeCatalog()
        opaque=validate(c,['body','dress'],True)
        self.assertNotIn('hidden_skin_without_cover',{f['code'] for f in opaque['findings']})
        pixels=np.ones((4,4,4));pixels[:,:,3]=0
        c.assets.pixels=lambda p:(pixels,False)
        transparent=validate(c,['body','dress'],True)
        found=next(f for f in transparent['findings'] if f['code']=='hidden_skin_without_cover')
        self.assertEqual(found['vertices'],3)
        self.assertFalse(transparent['runtime_verified'])

    def test_garment_inside_visible_skin_is_reported(self):
        c=FakeCatalog();c.parts['dress']['attributes']['HidingGroups']='';c.meshes['dress.skin']=mesh(-.01)
        result=validate(c,['body','dress'],True)
        self.assertIn('cloth_under_visible_skin',{f['code'] for f in result['findings']})

    def test_inner_garment_behind_skin_is_not_visible_through_an_opaque_outer_garment(self):
        c=FakeCatalog();c.parts['dress']['attributes']['HidingGroups']='';c.meshes['dress.skin']=mesh(-.01)
        c.parts['outer']=part('outer',layer='6');c.meshes['outer.skin']=mesh(.02)
        result=validate(c,['body','dress','outer'],True)
        self.assertNotIn('cloth_under_visible_skin',{f['code'] for f in result['findings']})
        # Removing the opaque cover must restore the diagnostic.
        self.assertIn('cloth_under_visible_skin',{f['code'] for f in validate(c,['body','dress'],True)['findings']})

    def test_hiding_group_is_scoped_to_equipment_part(self):
        c=FakeCatalog();c.groups['cover']['region']='feet'
        self.assertEqual(active_masks(c,['dress']),{'feet':16})
        result=validate(c,['body','dress'])
        self.assertEqual(result['meshes'][0]['hidden_vertices'],0)

    def test_native_body_replacement_and_nodraw_are_not_visible_surfaces(self):
        c=FakeCatalog();c.parts['dress']['elements'][0]['KeepBodyLayer']='false'
        c.materials=lambda name:[dict(shader='Nodraw',texture=None,threshold=0)]
        result=validate(c,['body','dress'],True)
        self.assertEqual(result['meshes'][0]['hidden_vertices'],3)
        self.assertNotIn('opacity_unverified',{f['code'] for f in result['findings']})
        self.assertIn('hidden_skin_without_cover',{f['code'] for f in result['findings']})

    def test_unlayered_native_slot_is_not_invented_or_reported_as_missing(self):
        c=FakeCatalog();c.parts['dress']['elements'][0].pop('BodyLayerId')
        c.native_roles['Dress']['layers']=[('torso',None)]
        result=validate(c,['body','dress'])
        self.assertNotIn('unresolved_layer',{f['code'] for f in result['findings']})
        c.native_roles['Dress']['layers']=[('torso','5')]
        result=validate(c,['body','dress'])
        self.assertIn('unresolved_layer',{f['code'] for f in result['findings']})

    def test_spatial_broadphase_matches_exhaustive_segment_test(self):
        rng=np.random.default_rng(72);triangles=rng.uniform(-.3,.3,(60,3,3));points=rng.uniform(-.3,.3,(130,3))
        directions=rng.normal(size=(130,3));directions/=np.linalg.norm(directions,axis=1)[:,None]
        accept=lambda t,u,v:(t%3!=0)&(u>.1)
        expected=covered_points(points,directions,triangles,accept_hit=accept)
        np.testing.assert_array_equal(Surface(triangles,accept).covers(points,directions),expected)

    def test_pairs_and_layer_product_have_explicit_total_and_resume(self):
        c=FakeCatalog();c.parts['coat']=part('coat',layer='9');c.parts['dress2']=part('dress2')
        a=plan(c,'pairs','person',limit=1);b=plan(c,'pairs','person',limit=2,offset=a['next_offset'])
        self.assertEqual(a['total'],3);self.assertEqual(len(a['cases']+b['cases']),3)
        self.assertEqual(len({tuple(r['selected']) for r in a['cases']+b['cases']}),3)
        all_layers=plan(c,'layers','person');self.assertEqual(all_layers['total'],6)
        self.assertIn(['body'],[r['selected'] for r in all_layers['cases']])
        self.assertFalse(any('dress' in r['selected'] and 'dress2' in r['selected'] for r in all_layers['cases']))

    def test_all_souls_include_unconverted_missing_and_secondary_clothing(self):
        rows={'rpg/soul':[dict(soul_id='1',soul_name='actor',initial_clothing_preset_id='p'),dict(soul_id='2',soul_name='not_converted',initial_clothing_preset_id='p'),dict(soul_id='3',soul_name='bad',initial_clothing_preset_id='missing')],
            'item/armor':[dict(item_id='a',clothing_id='c1',clothing2_id='c2')],
            'item/clothing':[dict(clothing_id='c1',model='dress.skin'),dict(clothing_id='c2',model='sleeve.skin')],
            'item/armor2clothing_preset':[dict(clothing_preset_id='p',armor_id='a')],
            'item/clothing_preset':[dict(clothing_preset_id='p')],'item/attachment2clothing_preset':[],
            'item/clothing_attachment':[dict(clothing_attachment_id='detail',model='strap.skin',gender_id='1')],
            'item/armor2clothing_attachment':[dict(item_id='a',clothing_attachment_id='detail')]}
        fake=SimpleNamespace(get=lambda name:dict(rows=rows[name]))
        rows.update({'character_'+kind:[] for kind in ('body','head','hair','beard')})
        with patch('clothing_lab.source.RetailSourceReader'),patch('clothing_lab.source.SourceTables',return_value=fake):
            records=soul_records(Path('.'),FakeCatalog())
        self.assertEqual(len(records),3)
        self.assertEqual([r['status'] for r in records],['ready','not_converted','source_error'])
        self.assertEqual([r['slot'] for r in records[0]['source_clothing']],['clothing_id','clothing2_id'])
        self.assertEqual([r['model'] for r in records[0]['source_clothing_attachments']],['strap.skin'])
        rows['item/clothing_attachment'] = []
        with patch('clothing_lab.source.RetailSourceReader'),patch('clothing_lab.source.SourceTables',return_value=fake):
            records=soul_records(Path('.'),FakeCatalog())
        self.assertIn('Missing clothing attachment record: detail',records[0]['source_issues'])

    def test_cancelled_batch_saves_partial_report_without_marking_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab=Lab(tmp);c=FakeCatalog();c.assets.root=Path(tmp);c.assets.signature=lambda:'fixture';c.rule_evidence=[]
            c.presets['second']=dict(c.presets['person'],id='second',label='second')
            c.errors=[];lab.catalog=c
            def checked(*args):
                lab.cancel();return dict(status='review',findings=[])
            with patch('clothing_lab.service.validate',side_effect=checked):
                lab.run(dict(mode='presets'));lab.worker.join(5)
            state=lab.poll();report=json.loads(Path(state['report']).read_text())
            self.assertEqual(state['done'],1);self.assertTrue(report['cancelled']);self.assertFalse(report['complete'])

    def test_converted_soul_does_not_hide_broken_source_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab=Lab(tmp);c=FakeCatalog();c.assets.root=Path(tmp);c.assets.signature=lambda:'fixture'
            c.rule_evidence=[];c.errors=[];lab.catalog=c
            case=dict(id='soul',label='actor',selected=['body','dress'],source_issues=['Missing secondary garment'],status='source_error')
            with patch('clothing_lab.service.soul_records',return_value=[case]):
                lab.run(dict(mode='souls',source=tmp));lab.worker.join(5)
            report=json.loads(Path(lab.poll()['report']).read_text())
            self.assertEqual(report['results'][0]['status'],'source_error')
            self.assertFalse(report['complete'])


if __name__=='__main__':unittest.main()
