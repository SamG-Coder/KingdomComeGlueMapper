from copy import deepcopy
from pathlib import Path
import struct
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from scenery_streaming import compile_hierarchy, check_hierarchy, read_near, records, contains
from scenery_materials import restore, signature
from upgrade_world_streaming import retarget_material_names


def instance(kind,x,y,z=5):
    b=bytearray(104 if kind==1 else 64)
    struct.pack_into('<I6f',b,0,kind,x-1,y-1,z-1,x+1,y+1,z+1)
    if kind==1:struct.pack_into('<12f',b,44,1,0,0,x,0,1,0,y,0,0,1,z)
    else:struct.pack_into('<4f',b,44,x,y,z,1)
    return bytes(b)


class StreamingTests(unittest.TestCase):
    def test_native_parent_indices_preserve_all_near_records_and_duplicates(self):
        near=[(4,instance(2,20,20)),(68,instance(2,20,20)),(132,instance(1,210,210)),(236,instance(1,700,700))]
        groups=[dict(id=90,active=True,kind='UberlodVegetation',cell=0,center=(32,32,5),radius=100),
                dict(id=91,active=True,kind='UberlodBrushes',center=(200,200,5),radius=100,
                     points=[(180,180),(240,180),(240,240),(180,240)]),
                dict(id=92,active=False,kind='UberlodBrushes',center=(200,200,5),radius=100,
                     points=[(180,180),(240,180),(240,240),(180,240)])]
        far={90:instance(1,32,32),91:instance(1,200,200)}
        data,doc,report=compile_hierarchy(near,groups,far)
        self.assertEqual(report['proxy_count'],2)
        self.assertEqual(report['near_records'],4)
        self.assertEqual(report['inactive_state_groups'],[92])
        self.assertEqual(check_hierarchy(data,doc)['proxies'],2)
        all_rows=[]
        for n in doc.iter('HLod'):all_rows.extend(r for _,r in records(data,int(n.get('DataOffset')),int(n.get('DataSize'))))
        self.assertCountEqual(all_rows,[b for _,b in near]+list(far.values()))
        # Global mesh IDs / arbitrary positive indices must never be accepted.
        node=next(n for n in doc.iter('HLod') if n.get('ProxyIndex')!='-1')
        node.set('ProxyIndex','1000')
        with self.assertRaisesRegex(ValueError,'parent-local'):check_hierarchy(data,doc)

    def test_rejects_aliased_and_orphaned_data(self):
        data=struct.pack('<III',2,0,64)+instance(2,10,10)
        root=ET.fromstring('<HLod DataOffset="4" DataSize="4" ProxyIndex="-1"/>')
        with self.assertRaisesRegex(ValueError,'Unowned'):read_near(data,root)
        ET.SubElement(root,'HLod',DataOffset='8',DataSize='68',ProxyIndex='-1')
        self.assertEqual(len(read_near(data,root)),1)
        ET.SubElement(root,'HLod',DataOffset='8',DataSize='68',ProxyIndex='-1')
        with self.assertRaisesRegex(ValueError,'multiple owners'):read_near(data,root)

    def test_polygon_ownership_uses_source_outline_and_boundary(self):
        polygon=[(0,0),(10,0),(10,2),(2,2),(2,10),(0,10)]
        self.assertTrue(contains(polygon,1,8));self.assertTrue(contains(polygon,0,8))
        self.assertFalse(contains(polygon,8,8));self.assertFalse(contains(polygon,20,20))

    def test_far_empty_cells_do_not_add_unconditional_geometry(self):
        data,doc,r=compile_hierarchy([(1,instance(2,300,300))],
            [dict(id=1,kind='UberlodVegetation',active=True,cell=0,center=(32,32,0),radius=100)],{1:instance(1,32,32)})
        self.assertEqual(r['proxy_count'],0)
        self.assertEqual(check_hierarchy(data,doc)['proxies'],0)

    def test_restores_source_shadow_flags_without_changing_slots_or_textures(self):
        source=ET.fromstring('<Material><SubMaterials><Material Name="leaf" Shader="Vegetation" MtlFlags="32"/>'
          '<Material Name="shadow_proxy" Shader="Vegetation" StringGenMask="%LEAVES" MtlFlags="128">'
          '<Textures><Texture Map="Diffuse" File="objects/tree/tree_diff.tif"/></Textures></Material></SubMaterials></Material>')
        target=deepcopy(source);leaf,shadow=target.find('SubMaterials')
        leaf.set('MtlFlags','0');shadow.set('Shader','Nodraw');shadow.set('StringGenMask','');shadow.set('GenMask','0');shadow.set('MtlFlags','1152')
        shadow.find('Textures/Texture').set('File','glueveg/setup/t23_tree_diff.dds')
        before=ET.tostring(shadow.find('Textures'))
        self.assertEqual(restore(target,source),1)
        self.assertEqual(leaf.get('MtlFlags'),'32');self.assertEqual(shadow.get('Shader'),'Vegetation')
        self.assertEqual(before,ET.tostring(shadow.find('Textures')))
        self.assertEqual(len(target.find('SubMaterials')),2)
        shadow.set('AlphaTest','.9')
        with self.assertRaisesRegex(ValueError,'identity'):restore(target,source)

    def test_travel_rename_rebinds_proxy_material_without_editing_geometry(self):
        name=b'levels/old/hlods/kcd1/m0'
        blob=b'CrCh'+struct.pack('<III',0x746,1,16)+struct.pack('<HHIII',0x1014,0x802,42,132,32)+name.ljust(128,b'\0')+b'MESH'
        converted=retarget_material_names(blob,'levels/old/','levels/kcd1_travel/')
        self.assertEqual(converted[:32],blob[:32]);self.assertEqual(converted[-4:],b'MESH')
        self.assertEqual(converted[32:160].split(b'\0',1)[0],b'levels/kcd1_travel/hlods/kcd1/m0')


if __name__=='__main__':unittest.main()
