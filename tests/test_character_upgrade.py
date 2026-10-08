import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from character_skin_upgrade import CompiledSkin, upgrade_skin, read_morphs, canonicalize_skin_skeletons, spine_mapping, limit_skin_influences
from clothing_regions import Chunk, read_chunks, write_chunks
from test_clothing_assembly import garment_skin
from upgrade_npc_characters import stage_upgrade, install_upgrade, rollback_upgrade, digest, TABLES, covered_source_layers
from merge_population_package import signature


def sample_skin(extra=False, morph=False):
    chunks=read_chunks(garment_skin(morph));bones=bytearray(32+2*584)
    identity=np.eye(4)[:3].ravel()
    for i,name in enumerate((b'Hips',b'Child')):
        o=32+584*i;struct.pack_into('<I',bones,o,i+1)
        struct.pack_into('<24f',bones,o+216,*identity,*identity)
        bones[o+312:o+312+len(name)]=name
    struct.pack_into('<iIi',bones,32+572,0,1,1)
    struct.pack_into('<iIi',bones,32+584+572,-1,0,0)
    replacements={39:bytes(bones)}
    if extra:
        mesh=bytearray(next(c.data for c in chunks if c.kind==0x1000));struct.pack_into('<I',mesh,0,6);replacements[10]=bytes(mesh)
        replacements[59]=struct.pack('<6I',0,9,12,12,0,0)+struct.pack('<4H4B',0,0,0,0,200,0,0,0)*6+struct.pack('<4H4B',1,0,0,0,55,0,0,0)*6
    return write_chunks(chunks,replacements)


def target_skin(source, rotate=False, child_translation=0):
    chunks=read_chunks(source);chunk=next(c for c in chunks if c.kind==0x2000);bones=bytearray(chunk.data)
    for i in range(2):
        m=np.eye(4)
        if rotate:m[:3,:3]=[[0,0,1],[0,1,0],[-1,0,0]];m[0,3]=2
        if i==1:m[0,3]+=child_translation
        struct.pack_into('<24f',bones,32+584*i+216,*np.linalg.inv(m)[:3].ravel(),*m[:3].ravel())
    return write_chunks(chunks,{chunk.id:bytes(bones)})


class SkinUpgradeTests(unittest.TestCase):
    def test_identity_bind_keeps_surface_weights_uvs_and_morph_bytes(self):
        source=sample_skin(morph=True);out,report=upgrade_skin(source,source)
        a,b=CompiledSkin(source),CompiledSkin(out)
        for kind in (0,1,2,5,6,9):self.assertEqual(a.streams[kind].data,b.streams[kind].data)
        self.assertEqual(a.one(0x2002).data,b.one(0x2002).data)
        self.assertEqual(report['moved_vertices'],0)

    def test_rigid_repose_updates_render_internal_normals_and_morphs_together(self):
        source=sample_skin(morph=True);target=target_skin(source,rotate=True)
        out,report=upgrade_skin(source,target,geometry_mode='repose');a,b=CompiledSkin(source),CompiledSkin(out)
        expected=np.column_stack((2+a.vertices[:,2],a.vertices[:,1],-a.vertices[:,0]))
        np.testing.assert_allclose(b.vertices,expected,atol=1e-6)
        normals=np.frombuffer(b.streams[1].data[24:],dtype='<f4').reshape(-1,3)
        np.testing.assert_allclose(normals,np.tile([1,0,0],(6,1)),atol=1e-6)
        delta=read_morphs(b.one(0x2002))[0]['delta']
        np.testing.assert_allclose(delta,np.tile([2,0,0],(6,1)),atol=1e-6)
        self.assertEqual(report['preserved_morphs'],1)
        self.assertEqual(b.info['vertex_color_channels'][3],{'255':6})
        np.testing.assert_array_equal(a.faces,b.faces)
        self.assertEqual(a.streams[9].data,b.streams[9].data)

    def test_eight_influences_are_preserved_and_all_contribute(self):
        source=sample_skin(extra=True);out,report=upgrade_skin(source,target_skin(source,child_translation=2),geometry_mode='repose')
        a,b=CompiledSkin(source),CompiledSkin(out)
        np.testing.assert_allclose(b.vertices[:,0]-a.vertices[:,0],2*55/255,atol=1e-6)
        self.assertEqual(a.streams[9].data,b.streams[9].data)
        self.assertEqual(report['extra_influenced_vertices'],6)

    def test_source_proportions_and_morphs_survive_native_joint_rebinding(self):
        source=sample_skin(extra=True,morph=True);target=target_skin(source,rotate=True,child_translation=2)
        out,report=upgrade_skin(source,target)
        a,b,native=CompiledSkin(source),CompiledSkin(out),CompiledSkin(target)
        for kind in (0,1,2,5,6,9):self.assertEqual(a.streams[kind].data,b.streams[kind].data)
        self.assertEqual(a.one(0x2002).data,b.one(0x2002).data)
        for result,expected in zip(b.info['bones'],native.info['bones']):
            np.testing.assert_allclose(result['bind'],expected['bind'],atol=1e-6)
        self.assertEqual(report['geometry_mode'],'preserve')
        self.assertEqual(report['moved_vertices'],0)
        with self.assertRaisesRegex(ValueError,'geometry mode'):upgrade_skin(source,target,geometry_mode='unknown')

    def test_native_four_weight_packing_keeps_geometry_and_normalizes_internal_weights(self):
        source=sample_skin(extra=True,morph=True);a=CompiledSkin(source)
        output,report=limit_skin_influences(source);b=CompiledSkin(output)
        self.assertFalse(b.info['has_extra_weights'])
        self.assertEqual(b.ids.shape,(6,4))
        # The nonzero influence from the second block must survive the packing.
        np.testing.assert_array_equal(b.ids[:,1],np.ones(6))
        np.testing.assert_allclose(b.weights[:,:2],np.tile([200/255,55/255],(6,1)))
        for kind in (0,1,2,5,6):self.assertEqual(a.streams[kind].data,b.streams[kind].data)
        self.assertEqual(a.one(0x2002).data,b.one(0x2002).data)
        for i,index in enumerate(b.remap):
            np.testing.assert_allclose(struct.unpack_from('<4f',b.internal.data,32+64*int(index)+44),b.weights[i],atol=1e-7)
        self.assertEqual(report['maximum_discarded_weight'],0)
        self.assertEqual(limit_skin_influences(output)[0],output)

    def test_native_four_weight_limit_does_not_leave_subunit_weight_sums(self):
        source=sample_skin(extra=True);chunks=read_chunks(source);s=CompiledSkin(source)
        bones=bytearray(s.bones.data)
        for index in range(2,8):
            record=bytearray(bones[32+584:32+2*584])
            struct.pack_into('<I',record,0,index+1)
            record[312:568]=('Joint'+str(index)).encode().ljust(256,b'\0')
            struct.pack_into('<iIi',record,572,-index,0,0)
            bones.extend(record)
        struct.pack_into('<iIi',bones,32+572,0,7,1)
        stream=s.streams[9]
        payload=stream.data[:24]+struct.pack('<4H4B',0,1,2,3,70,60,50,40)*6+struct.pack('<4H4B',4,5,6,7,15,10,6,4)*6
        source=write_chunks(chunks,{stream.id:payload,s.bones.id:bytes(bones)})
        output,report=limit_skin_influences(source);s=CompiledSkin(output)
        np.testing.assert_array_equal(np.rint(s.weights*255).sum(axis=1),np.full(6,255))
        self.assertEqual(report['changed_vertices'],6)
        self.assertAlmostEqual(report['maximum_discarded_weight'],35/255)

    def test_native_packing_merges_duplicate_joints_before_limiting(self):
        source=sample_skin(extra=True);s=CompiledSkin(source);stream=s.streams[9]
        payload=stream.data[:24]+struct.pack('<4H4B',0,1,0,1,70,60,50,40)*6+struct.pack('<4H4B',0,1,0,1,15,10,6,4)*6
        source=write_chunks(s.chunks,{stream.id:payload})
        output,report=limit_skin_influences(source);s=CompiledSkin(output)
        np.testing.assert_array_equal(np.rint(s.weights*255),np.tile([141,114,0,0],(6,1)))
        self.assertEqual(report['maximum_discarded_weight'],0)

    def test_vertex_rgb_features_survive_hiding_conversion(self):
        source=sample_skin();chunks=read_chunks(source);mesh=bytearray(chunks[0].data)
        struct.pack_into('<I',mesh,40,70)
        chunks.append(Chunk(0x1016,0x800,70,struct.pack('<6I',0,3,6,4,0,0)+bytes((11,22,230,0))*6))
        source=write_chunks(chunks,{10:bytes(mesh)});out,_=upgrade_skin(source,source)
        self.assertEqual(CompiledSkin(out).streams[3].data[24:],bytes((11,22,230,255))*6)

    def test_legacy_float_variant_bakes_once_without_dropping_vertices(self):
        source=sample_skin();chunks=read_chunks(source);c=next(c for c in chunks if c.kind==0x2002)
        name=b'#shape\0';s=CompiledSkin(source);internal=int(s.remap[0])
        payload=struct.pack('<I4I',1,0,len(name),1,1)+name+struct.pack('<I3f',internal,0,1,0)+struct.pack('<I3f',0,0,1,0)
        chunks=[Chunk(c.kind,0x801,c.id,payload) if x.id==c.id else x for x in chunks]
        source=write_chunks(chunks,{});out,report=upgrade_skin(source,source,variant='#shape')
        a,b=CompiledSkin(source),CompiledSkin(out)
        expected=a.vertices.copy();expected[0,1]+=1
        np.testing.assert_allclose(b.vertices,expected)
        self.assertEqual(b.one(0x2002).data,bytes(4))
        with self.assertRaisesRegex(ValueError,'variant missing'):upgrade_skin(out,out,variant='#shape')

    def test_garment_variant_and_authored_fit_shape_are_baked_together(self):
        source=sample_skin(morph=True);chunks=read_chunks(source)
        c=next(c for c in chunks if c.kind==0x2002);name=b'#A_Shrink\0'
        header=struct.pack('<4I6fIf',0xffffffff,len(name),6,6,-1,0,0,0,0,0,1,1)
        payload=struct.pack('<I',2)+c.data[4:]+header+name+struct.pack('<2H',0,6)+bytes(18)
        source=write_chunks(chunks,{c.id:payload})
        output,report=upgrade_skin(source,source,variant='#V_001',morph_weights={'#A_Shrink':1})
        expected=CompiledSkin(source).vertices+[-1,0,2]
        np.testing.assert_allclose(CompiledSkin(output).vertices,expected)
        self.assertEqual(report['preserved_morphs'],0)
        self.assertEqual(report['baked_morphs'],{'#V_001':1,'#A_Shrink':1})

    def test_palette_unification_preserves_weighted_names(self):
        source=sample_skin(extra=True)
        outputs=canonicalize_skin_skeletons([source,source])
        for out in outputs:
            a,b=CompiledSkin(source),CompiledSkin(out)
            self.assertEqual([v['name'] for v in a.info['bones']],[v['name'] for v in b.info['bones']])
            np.testing.assert_array_equal(a.ids,b.ids)
            np.testing.assert_array_equal(a.weights,b.weights)

    def test_palette_merges_case_aliases_instead_of_duplicate_controller_crc(self):
        source=sample_skin(extra=True);chunks=read_chunks(source)
        bones=bytearray(next(c for c in chunks if c.kind==0x2000).data)
        bones[32+584+312:32+584+568]=b'child'.ljust(256,b'\0')
        alias=write_chunks(chunks,{39:bytes(bones)})
        for blob in canonicalize_skin_skeletons([source,alias]):
            result=CompiledSkin(blob)
            self.assertEqual([b['name'] for b in result.info['bones']],['Hips','Child'])
            np.testing.assert_array_equal(result.ids,CompiledSkin(source).ids)

    def test_axial_mapping_preserves_endpoints_and_matching_male_chain(self):
        def chain(count):
            result=[];parent=None
            for i in range(count):
                m=np.eye(4);m[2,3]=i/(count-1)
                name='Spine'+(str(i) if i else '')
                result.append(dict(name=name,parent=parent,bind=m[:3].ravel().tolist()))
                parent=name
            result.append(dict(name='Neck',parent=parent));return result
        source=chain(4);target=chain(5)
        self.assertEqual(spine_mapping(source,source),{})
        mapped=spine_mapping(source,target)
        self.assertEqual(mapped['Spine3'],'Spine4')
        names=[mapped.get(b['name'],b['name']) for b in source[:-1]]
        self.assertEqual(names[0],'Spine');self.assertEqual(len(set(names)),4)
        with self.assertRaisesRegex(ValueError,'fewer joints'):spine_mapping(target,source)


class StagingTests(unittest.TestCase):
    def test_source_layer_fit_does_not_use_inventory_order_or_gender(self):
        parts=[dict(kind='cloth',mesh_path=name) for name in
               ('s1_p2_l4_v2.skin','s1_p2_l1_v0.skin','s1_p2_l2_v0.skin','s1_p3_l1_v0.skin','unknown.skin')]
        self.assertEqual(covered_source_layers(parts),{1,2})
        for p in parts:p['mesh_path']=p['mesh_path'].replace('s1_','s2_')
        self.assertEqual(covered_source_layers(parts),{1,2})

    def test_composition_keeps_underwear_surfaces_and_original_item_identities(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'game';stage=root/'stage';namespace='sample_female'
            prefix=data/'objects/characters/gluenpc'/namespace;prefix.mkdir(parents=True)
            name='Glue_'+namespace
            components=ET.Element('CharacterComponents')
            component=ET.SubElement(components,'Component',Name=name,Gender='Female',FilePath='gluenpc/'+namespace+'/')
            derived=ET.SubElement(component,'DerivedComponents');parts=[]
            material='<Material><SubMaterials>'+''.join(f'<Material Name="slot{i}" Shader="Illum"/>' for i in range(10))+'</SubMaterials></Material>'
            items=ET.fromstring('<Table><ItemClasses/></Table>')
            presets=ET.fromstring(f'<Table><clothing_preset clothing_preset_name="{name}"><Items/></clothing_preset></Table>')
            skin=sample_skin();chunks=read_chunks(skin)
            bones=bytearray(next(c for c in chunks if c.kind==0x2000).data)
            bones[32+584+312:32+584+568]=b'LeftArm'.ljust(256,b'\0')
            skin=write_chunks(chunks,{39:bytes(bones)})
            for index,kind in enumerate(('body','cloth','cloth')):
                (prefix/f'part{index}.skin').write_bytes(skin)
                (prefix/f'part{index}.mtl').write_text(material)
                part=dict(kind=kind,gender_id='2',clothing_name=f'garment{index}',armor_settings={'is_underwear':str(index==1)})
                parts.append(part)
                attrs=dict(Name=name+'_'+str(index))
                if kind=='cloth':attrs.update(ArmorType='F_SimpleDress',ArmorArchetypeId='27')
                node=ET.SubElement(derived,'Body' if kind=='body' else 'Clothing',attrs)
                ET.SubElement(ET.SubElement(node,'Elements'),'SkinElement',EquipmentPart='torso',BodyLayerId='0' if kind=='body' else '9',Model=f'part{index}.skin',Material=f'part{index}.mtl')
                if kind=='cloth':
                    ET.SubElement(items.find('ItemClasses'),'Armor',Id=f'item-{index}',Name=attrs['Name'],Clothing=attrs['Name'],Price='17')
                    ET.SubElement(presets.find('.//Items'),'Guid').text=f'item-{index}'
            (prefix/'npc-report.json').write_text(json.dumps(dict(npc='sample_person',parts=parts)))
            docs=dict(components=components,items=items,presets=presets,
                      config=ET.fromstring(f'<ClothingConfigs><ClothingConfig Name="{name}"/></ClothingConfigs>'),
                      appearance=ET.fromstring(f'<rules><rule name="gluemapper_{namespace}_appearance"><operations><setUnderwear name="native"/></operations></rule></rules>'))
            for key,doc in docs.items():
                path=data/TABLES[key];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(ET.tostring(doc))
            with patch('upgrade_npc_characters.Assets') as assets:
                assets.return_value.get.return_value=(skin,['native-rig'])
                plan=stage_upgrade(data,root/'native',[namespace],'revision1',stage,compose_outfit=True)
            result=plan['characters'][0]
            self.assertEqual(result['composition']['item_id'],'item-2')
            self.assertEqual(sum(r['triangles'] for r in result['composition']['regions'].values()),8)
            self.assertTrue(result['composition']['source_underwear_separate'])
            output=ET.parse(stage/'Data'/TABLES['items']).getroot()
            self.assertEqual([e.get('Id') for e in output.iter('Armor')],['item-1','item-2'])
            self.assertEqual([e.get('Price') for e in output.iter('Armor')],['17','17'])
            self.assertEqual(signature(ET.parse(stage/'Data'/TABLES['presets']).getroot()),signature(presets))
            output=ET.parse(stage/'Data'/TABLES['components']).getroot()
            underwear=next(e for e in output.iter('Clothing') if e.get('Name')==result['source_underwear'])
            self.assertEqual(underwear.find('Elements/SkinElement').get('BodyLayerId'),'8')
            self.assertTrue(underwear.find('Elements/SkinElement').get('Model').endswith('part1_0.skin'))

    def test_both_genders_preserve_unrelated_rows_and_current_materials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'game';stage=root/'stage';data.mkdir()
            components=ET.Element('CharacterComponents');configs=ET.Element('ClothingConfigs')
            presets=ET.Element('Table');items=ET.fromstring('<Table><ItemClasses><Armor Id="prior-item" Name="prior"/></ItemClasses></Table>')
            appearance=ET.Element('rules')
            for namespace,sex,gender in [('person_a','male','1'),('person_b','female','2')]:
                prefix=data/'objects/characters/gluenpc'/namespace;prefix.mkdir(parents=True)
                (prefix/'source.skin').write_bytes(sample_skin())
                material=b'<Material Shader="HumanSkin"><PublicParams Blood="preserve"/></Material>'
                (prefix/'effects_v4.mtl').write_bytes(material)
                report=dict(npc=namespace,parts=[dict(kind='body',gender_id=gender)])
                (prefix/'npc-report.json').write_text(json.dumps(report))
                component=ET.SubElement(components,'Component',Name='Glue_'+namespace,Gender=sex,FilePath='gluenpc/'+namespace+'/')
                derived=ET.SubElement(component,'DerivedComponents')
                body=ET.SubElement(derived,'Body',Name='Glue_'+namespace+'_0')
                elements=ET.SubElement(body,'Elements')
                ET.SubElement(elements,'SkinElement',EquipmentPart='torso',Model='source.skin',Material='effects_v4.mtl')
                ET.SubElement(configs,'ClothingConfig',Name='Glue_'+namespace,DefaultBody='Glue_'+namespace+'_0')
                ET.SubElement(presets,'clothing_preset',clothing_preset_name='Glue_'+namespace)
                ET.SubElement(appearance,'rule',name='gluemapper_'+namespace+'_appearance')
            docs=dict(components=components,config=configs,presets=presets,items=items,appearance=appearance)
            snapshots={}
            for name,doc in docs.items():
                ET.SubElement(doc,'Unrelated',Name='campaign-other-work',Value='preserve')
                path=data/TABLES[name];path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(ET.tostring(doc));snapshots[TABLES[name]]=path.read_bytes()
            with patch('upgrade_npc_characters.Assets') as assets:
                assets.return_value.get.return_value=(sample_skin(),['original-native-rig'])
                plan=stage_upgrade(data,root/'native',['person_a','person_b'],'revision1',stage)
            self.assertEqual([p['gender'] for p in plan['characters']],['male','female'])
            self.assertTrue(plan['unrelated_table_records_preserved'])
            for path,blob in snapshots.items():self.assertEqual((data/path).read_bytes(),blob)
            for name,doc in docs.items():
                output=ET.parse(stage/'Data'/TABLES[name]).getroot()
                self.assertEqual(signature(doc.find('Unrelated')),signature(output.find('Unrelated')))
            output=ET.parse(stage/'Data'/TABLES['components']).getroot()
            for element in output.iter('SkinElement'):
                self.assertEqual(element.get('Material'),'effects_v4.mtl')
                self.assertTrue(element.get('Model').startswith('revision1/'))


class InstallationTests(unittest.TestCase):
    def make_plan(self, root):
        data=root/'game';stage=root/'stage';data.mkdir();(stage/'Data').mkdir(parents=True)
        before=b'other campaign work';after=b'updated selected record';asset=b'new mesh'
        (data/'table.xml').write_bytes(before);(data/'unrelated.bin').write_bytes(b'preserve me')
        (stage/'Data/table.xml').write_bytes(after);(stage/'Data/model.skin').write_bytes(asset)
        plan=dict(data_root=str(data),installed=False,sources={'table.xml':digest(before)},files=[
            dict(path='model.skin',before=None,after=digest(asset)),
            dict(path='table.xml',before=digest(before),after=digest(after))])
        (stage/'upgrade-plan.json').write_text(json.dumps(plan));return data,stage,before

    def test_install_and_rollback_preserve_exact_existing_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            data,stage,before=self.make_plan(Path(tmp));install_upgrade(stage)
            self.assertEqual((stage/'backup/table.xml').read_bytes(),before)
            self.assertEqual((data/'unrelated.bin').read_bytes(),b'preserve me')
            rollback_upgrade(stage)
            self.assertEqual((data/'table.xml').read_bytes(),before);self.assertFalse((data/'model.skin').exists())

    def test_stale_inputs_abort_before_writing_any_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data,stage,_=self.make_plan(Path(tmp));(data/'table.xml').write_bytes(b'later user edit')
            with self.assertRaisesRegex(ValueError,'Source changed'):install_upgrade(stage)
            self.assertFalse((data/'model.skin').exists());self.assertFalse((stage/'backup').exists())

    def test_rollback_refuses_to_overwrite_later_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            data,stage,_=self.make_plan(Path(tmp));install_upgrade(stage)
            (data/'table.xml').write_bytes(b'later user edit')
            with self.assertRaisesRegex(ValueError,'later edits'):rollback_upgrade(stage)
            self.assertEqual((data/'table.xml').read_bytes(),b'later user edit')

    def test_failed_copy_restores_even_partially_written_file(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            data,stage,before=self.make_plan(Path(tmp));real=shutil.copy2
            def fail(source,target):
                if Path(source)==stage/'Data/table.xml':
                    Path(target).write_bytes(b'partial');raise OSError('injected failure')
                return real(source,target)
            with patch('upgrade_npc_characters.shutil.copy2',side_effect=fail):
                with self.assertRaisesRegex(OSError,'injected'):install_upgrade(stage)
            self.assertEqual((data/'table.xml').read_bytes(),before);self.assertFalse((data/'model.skin').exists())


if __name__=='__main__':unittest.main()
