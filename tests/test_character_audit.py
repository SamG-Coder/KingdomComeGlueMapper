import struct
import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from audit_character_bodies import inspect_mesh, resolve_component
from clothing_regions import Chunk, read_chunks, write_chunks


def weighted_triangle():
    """Two bone-weight blocks: three render vertices, one extra influence each."""
    mesh=bytearray(264)
    struct.pack_into('<6I',mesh,0,6,0,3,3,1,20)
    for kind,ident in ((0,21),(5,22),(9,23)):
        struct.pack_into('<I',mesh,28+kind*4,ident)
    bones=bytearray(32+2*584)
    for i,name in enumerate((b'Root',b'Child')):
        start=32+i*584;bones[start+312:start+312+len(name)]=name
    struct.pack_into('<i',bones,32+584+572,-1)
    weights=struct.pack('<6I',0,9,6,12,0,0)
    weights+=struct.pack('<4H4B',0,0,0,0,200,0,0,0)*3
    weights+=struct.pack('<4H4B',1,0,0,0,55,0,0,0)*3
    chunks=[Chunk(0x1000,0x801,10,bytes(mesh)),
            Chunk(0x1017,0x800,20,struct.pack('<4I5I4f',0,1,0,0,0,3,0,3,0,1,0,0,0)),
            Chunk(0x1016,0x800,21,struct.pack('<6I9f',0,0,3,12,0,0,0,0,0,1,0,0,0,0,1)),
            Chunk(0x1016,0x800,22,struct.pack('<6I3H',0,5,3,2,0,0,0,1,2)),
            Chunk(0x1016,0x800,23,weights),Chunk(0x2000,0x800,24,bytes(bones))]
    return write_chunks(chunks,{})


class CharacterAuditTests(unittest.TestCase):
    def test_extra_weights_count_as_two_blocks_for_one_vertex_set(self):
        info,positions,faces=inspect_mesh(weighted_triangle())
        self.assertEqual(len(positions),3)
        self.assertEqual(faces,[(0,1,2)])
        self.assertEqual(info['extra_influenced_vertices'],3)
        self.assertEqual(info['weight_sum_histogram'],{255:3})
        self.assertEqual(info['primary_weight_totals'],{'Root':600})
        self.assertEqual(info['extra_weight_totals'],{'Child':165})
        self.assertEqual(info['bones'][1]['parent'],'Root')

    def test_rejects_unflagged_second_weight_block(self):
        blob=weighted_triangle();chunks=read_chunks(blob);mesh=bytearray(chunks[0].data)
        struct.pack_into('<I',mesh,0,2)
        with self.assertRaisesRegex(ValueError,'Stream count'):
            inspect_mesh(write_chunks(chunks,{10:bytes(mesh)}))

    def test_resolves_body_inheritance_without_moving_parent_material_path(self):
        root=ET.fromstring('''<Component Name="Male" FilePath="humans/male/body/">
          <DerivedComponents><Body Name="Base"><Elements>
            <SkinElement EquipmentPart="torso" BodyLayerId="0" Model="body.skin" Material="body.mtl"/>
          </Elements><DerivedComponents><Body Name="Target" FilePath="custom/">
            <Elements><SkinElement EquipmentPart="torso" Model="variant.skin"/></Elements>
          </Body></DerivedComponents></Body></DerivedComponents></Component>''')
        e=resolve_component(root,'Target')['elements'][0]
        self.assertEqual(e['BodyLayerId'],'0')
        self.assertEqual(e['ModelResolved'],'objects/characters/custom/variant.skin')
        self.assertEqual(e['MaterialResolved'],'objects/characters/humans/male/body/body.mtl')

    def test_vcloth_placeholder_is_completed_instead_of_duplicated(self):
        root=ET.fromstring('''<Component Name="Female" FilePath="humans/female/clothing/">
          <DerivedComponents><Clothing Name="Base"><Elements>
            <VClothElement EquipmentPart="waist" GravityFactor="0.1"/>
          </Elements><DerivedComponents><Clothing Name="Layer"><Elements>
            <VClothElement EquipmentPart="waist" BodyLayerId="5" Model="old.skin"/>
          </Elements><DerivedComponents><Clothing Name="Target"><Elements>
            <VClothElement EquipmentPart="waist" Model="new.skin" SimBinding="sim.skin"/>
          </Elements></Clothing></DerivedComponents></Clothing></DerivedComponents></Clothing></DerivedComponents></Component>''')
        elements=resolve_component(root,'Target')['elements']
        self.assertEqual(len(elements),1)
        self.assertEqual(elements[0]['BodyLayerId'],'5')
        self.assertEqual(elements[0]['GravityFactor'],'0.1')
        self.assertTrue(elements[0]['SimBindingResolved'].endswith('/sim.skin'))

    def test_empty_material_override_clears_resolved_parent_binding(self):
        root=ET.fromstring('''<Body Name="Base" FilePath="body"><Elements>
          <SkinElement EquipmentPart="torso" BodyLayerId="0" Material="old.mtl"/>
          </Elements><DerivedComponents><Body Name="Target"><Elements>
          <SkinElement EquipmentPart="torso" Material=""/>
          </Elements></Body></DerivedComponents></Body>''')
        e=resolve_component(root,'Target')['elements'][0]
        self.assertEqual(e['Material'],'')
        self.assertNotIn('MaterialResolved',e)


if __name__=='__main__':unittest.main()
