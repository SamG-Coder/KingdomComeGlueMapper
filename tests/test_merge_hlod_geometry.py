from pathlib import Path
import sys
import unittest
import struct
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from hlod_proxy_mesh import compose
from merge_hlod_geometry import merge
from geometry_opacity import cgf_triangles
from test_hlod_proxy_mesh import fixture
from clothing_regions import Chunk,read_chunks,write_chunks


class MergeTests(unittest.TestCase):
    def test_transformed_instances_merge_without_loss_or_duplication(self):
        a=np.column_stack((np.eye(3),[10,20,30]))
        b=np.column_stack(([[0,-2,0],[3,0,0],[0,0,1]],[40,50,60]))
        source,_=compose([(fixture(),a,None),(fixture(),b,None)],[0,0,0])
        result,report=merge(source)
        before,_=cgf_triangles(source);after,_=cgf_triangles(result)
        np.testing.assert_allclose(before,after)
        self.assertEqual(report,dict(merged_render_nodes=1,source_render_nodes=2))

    def test_batches_remain_bounded(self):
        a=np.column_stack((np.eye(3),[10,20,30]))
        source,_=compose([(fixture(),a,None)]*5,[0,0,0])
        result,report=merge(source,max_vertices=6)
        self.assertEqual(report['merged_render_nodes'],3)
        self.assertEqual(len(cgf_triangles(result)[0]),5)

    def test_reflected_geometry_retains_front_facing_winding(self):
        a=np.column_stack((np.diag([-1,1,1]),[0,0,0]))
        source,_=compose([(fixture(),a,None)]*2,[0,0,0])
        result,_=merge(source);tris,_=cgf_triangles(result)
        self.assertGreater(np.cross(tris[0,1]-tris[0,0],tris[0,2]-tris[0,0])[2],0)

    def test_secondary_uvs_and_tangent_rotation_are_preserved(self):
        chunks=read_chunks(fixture());sid=max(c.id for c in chunks)+1
        uv=np.array([[2,3],[4,5],[6,7]],dtype='<f4')
        tangent=np.tile(np.array([32767,0,0,32767,0,32767,0,32767],dtype='<i2'),(3,1))
        chunks.extend([Chunk(0x1016,0x800,sid,struct.pack('<6I',0,8,3,8,0,0)+uv.tobytes()),
                       Chunk(0x1016,0x800,sid+1,struct.pack('<6I',0,6,3,16,0,0)+tangent.tobytes())])
        for i,c in enumerate(chunks):
            if c.kind==0x1000:
                data=bytearray(c.data);struct.pack_into('<I',data,28+8*4,sid);struct.pack_into('<I',data,28+6*4,sid+1)
                chunks[i]=Chunk(c.kind,c.version,c.id,bytes(data))
        matrix=np.column_stack(([[0,-1,0],[1,0,0],[0,0,1]],[20,30,40]))
        source,_=compose([(write_chunks(chunks,{}),matrix,None)]*2,[0,0,0])
        result,_=merge(source)
        for c in read_chunks(result):
            if c.kind!=0x1016:continue
            kind=struct.unpack_from('<I',c.data,4)[0]
            if kind==8:np.testing.assert_array_equal(np.frombuffer(c.data[24:],dtype='<f4').reshape(-1,2),np.tile(uv,(2,1)))
            if kind==6:np.testing.assert_array_equal(np.frombuffer(c.data[24:],dtype='<i2').reshape(-1,8)[0],
                                                   [0,32767,0,32767,-32767,0,0,32767])


if __name__=='__main__':unittest.main()
