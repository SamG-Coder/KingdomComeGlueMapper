from pathlib import Path
import struct
import sys
import unittest
import xml.etree.ElementTree as ET
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from clothing_regions import Chunk,read_chunks,write_chunks
from hlod_proxy_mesh import compose
from geometry_opacity import cgf_triangles
import test_geometry_opacity


def fixture():
    chunks=read_chunks(test_geometry_opacity.GeometryOpacityTests().mesh_fixture())
    out=[]
    for c in chunks:
        if c.kind==0x1000:
            data=bytearray(c.data);struct.pack_into('<6f',data,108,0,0,0,1,1,0)
            c=Chunk(c.kind,0x801,c.id,bytes(data))
        out.append(c)
    return write_chunks(out,{})


class ProxyTests(unittest.TestCase):
    def test_single_object_partition_preserves_triangles_and_transform(self):
        chunks=read_chunks(fixture());changed=[]
        for c in chunks:
            data=bytearray(c.data)
            if c.kind==0x1016 and struct.unpack_from('<I',data,4)[0]==5:
                struct.pack_into('<I',data,8,6);data.extend(data[24:])
            if c.kind==0x1017:struct.pack_into('<I',data,20,6)
            if c.kind==0x1000:struct.pack_into('<I',data,12,6)
            changed.append(Chunk(c.kind,c.version,c.id,bytes(data)))
        m=np.column_stack((np.eye(3),[10,20,30]))
        blob,report=compose([(write_chunks(changed,{}),m,None)],[0,0,0])
        tris,_=cgf_triangles(blob)
        self.assertEqual(len(tris),2)
        self.assertEqual(report['triangles'],2)
        np.testing.assert_allclose(tris[0],[[10,20,30],[11,20,30],[10,21,30]])
        np.testing.assert_array_equal(tris[0],tris[1])

    def test_single_material_override_covers_all_subset_ids(self):
        chunks=read_chunks(fixture());changed=[]
        for c in chunks:
            data=bytearray(c.data)
            if c.kind==0x1017:struct.pack_into('<i',data,32,4)
            changed.append(Chunk(c.kind,c.version,c.id,bytes(data)))
        m=np.column_stack((np.eye(3),[0,0,0]));blob=write_chunks(changed,{})
        result,report=compose([(blob,m,'single'),(blob,m,'single')],[0,0,0],
                             lambda _:b'<Material Shader="Illum"/>','levels/test/proxy.mtl')
        self.assertEqual(report['submaterials'],1)
        for c in read_chunks(result):
            if c.kind==0x1017:self.assertEqual(struct.unpack_from('<i',c.data,32)[0],0)

    def test_simple_mesh_authoring_transform_is_not_runtime_placement(self):
        chunks=read_chunks(fixture());changed=[]
        for c in chunks:
            if c.kind==0x100b and not c.data.startswith(b'$physics'):
                data=bytearray(c.data);struct.pack_into('<f',data,84+13*4,-13000)
                c=Chunk(c.kind,c.version,c.id,bytes(data))
            changed.append(c)
        blob=write_chunks(changed,{})
        m=np.column_stack((np.eye(3),[0,0,0]));n=m.copy();n[0,3]=10
        proxy,_=compose([(blob,m,None),(blob,n,None)],[0,0,0])
        triangles,_=cgf_triangles(proxy)
        self.assertAlmostEqual(float(triangles[:,:,1].min()),0)
        self.assertAlmostEqual(float(triangles[:,:,1].max()),1)

    def test_geometry_is_placed_relative_to_cluster_not_shared_asset(self):
        blob=fixture();a=np.column_stack((np.eye(3),[100,200,30]));b=a.copy();b[:,3]+=[10,0,0]
        proxy,report=compose([(blob,a,None),(blob,b,None)],[100,200,30])
        triangles,_=cgf_triangles(proxy)
        np.testing.assert_allclose(triangles[0],[[0,0,0],[1,0,0],[0,1,0]])
        np.testing.assert_allclose(triangles[1],[[10,0,0],[11,0,0],[10,1,0]])
        self.assertEqual(report['triangles'],2)
        self.assertEqual(len([c for c in read_chunks(proxy) if c.kind==0x100b]),2)
        self.assertEqual(proxy,compose([(blob,a,None),(blob,b,None)],[100,200,30])[0])

    def test_material_ids_remapped_and_textures_preserved(self):
        blob=fixture();m=np.column_stack((np.eye(3),[0,0,0]))
        materials={'a.mtl':b'<Material Shader="Illum"><Textures><Texture Map="Diffuse" File="a.dds"/></Textures></Material>',
                   'b.mtl':b'<Material Shader="Vegetation"><Textures><Texture Map="Diffuse" File="b.dds"/></Textures></Material>'}
        proxy,report=compose([(blob,m,'a.mtl'),(blob,m,'b.mtl')],[0,0,0],materials.__getitem__,'levels/test/proxy.mtl')
        root=ET.fromstring(report['material_xml'])
        self.assertEqual([t.get('File') for t in root.iter('Texture')],['a.dds','b.dds'])
        subs=[c for c in read_chunks(proxy) if c.kind==0x1017]
        self.assertEqual([struct.unpack_from('<i',c.data,32)[0] for c in subs],[0,1])
        for c in read_chunks(proxy):
            if c.kind==0x1014:self.assertEqual(c.data[:128].split(b'\0')[0],b'levels/test/proxy')

    def test_missing_dependency_and_empty_cluster_rejected(self):
        with self.assertRaisesRegex(ValueError,'Empty cluster'):compose([], [0,0,0])
        m=np.column_stack((np.eye(3),[0,0,0]))
        with self.assertRaises(KeyError):
            compose([(fixture(),m,'missing')],[0,0,0],{}.__getitem__,'levels/test/proxy.mtl')


if __name__=='__main__':unittest.main()
