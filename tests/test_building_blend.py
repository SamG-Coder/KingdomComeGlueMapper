"""Synthetic source/target blend equivalence and binary preservation checks."""
import importlib.util
import io
from pathlib import Path
import struct
import sys
import unittest
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from building_blend import bake_atlas, rgba_dds, decode_texture, sample_wrap
from building_blend_mesh import reatlas, render_material_ids
from building_materials import blend_inputs, convert
from clothing_regions import Chunk, read_chunks, write_chunks
from static_assets import convert_material_features
from build_building_blend_test import merge_baked_material


def triangle_mesh():
    chunks=[]
    def stream(kind,array,width):
        ident=10+kind
        chunks.append(Chunk(0x1016,0x800,ident,struct.pack('<6I',0,kind,len(array),width,0,0)+array.tobytes()))
        return ident
    ids=[0]*16
    ids[0]=stream(0,np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1],[1,0,1],[0,1,1]],'<f4'),12)
    ids[2]=stream(2,np.array([[0,0],[1,0],[0,1],[1,0],[2,0],[1,1]],'<f4'),8)
    # Coincident secondary UV islands with distinct primary coordinates.
    ids[8]=stream(8,np.array([[0,0],[1,0],[0,1]]*2,'<f4'),8)
    ids[5]=stream(5,np.arange(6,dtype='<u2'),2)
    mesh=bytearray(264);struct.pack_into('<6I',mesh,0,2,0,6,6,1,40)
    struct.pack_into('<16I',mesh,28,*ids)
    chunks.append(Chunk(0x1000,0x801,41,bytes(mesh)))
    subset=struct.pack('<4I',0,1,0,0)+struct.pack('<5I4f',0,6,0,6,0,1,0,0,0)
    chunks.append(Chunk(0x1017,0x800,40,subset))
    chunks.append(Chunk(0x100c,0x800,42,b'opaque physics payload'))
    return write_chunks(chunks,{})


class BuildingBlendTests(unittest.TestCase):
    def test_unused_shared_material_slot_needs_no_atlas(self):
        blob = triangle_mesh()
        self.assertEqual(render_material_ids(blob), {0})
        doc = ET.fromstring('<Material><SubMaterials><Material Shader="Illum"/><Material Shader="Illum" StringGenMask="%SNDUVS%BLENDLAYER%DETAIL_MAPPING"><Textures><Texture Map="[1] Diffuse" File="unused.dds"/><Texture Map="Opacity" File="unused_mask.dds"/></Textures></Material></SubMaterials></Material>')
        def unexpected_read(name): raise AssertionError('Unused texture read: '+name)
        result, emitted = convert(doc, blob, 'test', unexpected_read, {})
        self.assertEqual(result, blob)
        self.assertEqual(emitted, {})

    def test_focused_package_keeps_native_secondary_detail_dependencies(self):
        current = ET.fromstring('''<Material><SubMaterials>
          <Material Name="roof" Shader="Illum" StringGenMask="%BLENDLAYER%DETAIL_MAPPING%SNDUVS">
            <Textures>
              <Texture Map="Opacity" File="installed/old_second.dds"/>
              <Texture Map="Detail" File="installed/wood_dt.dds"><TexMod TileU="8" TileV="4"/></Texture>
              <Texture Map="Custom" File="installed/moss.dds"/>
            </Textures>
            <PublicParams DetailBumpScale="1" DetailDiffuseScale="0.8" DetailGlossScale="0.6"/>
          </Material>
          <Material Name="plain" Shader="Illum"><Textures><Texture Map="Diffuse" File="installed/plain.dds"/></Textures></Material>
        </SubMaterials></Material>''')
        generated = ET.fromstring(ET.tostring(current))
        roof = generated.find('SubMaterials/Material')
        roof.find("Textures/Texture[@Map='Opacity']").set('File', 'new/roof_blend2_diff.dds')
        roof.find("Textures/Texture[@Map='Detail']").set('File', 'not_packaged/wood_dt.dds')
        roof.find('PublicParams').set('SndUVsTileU', '1')
        before = ET.tostring(current)
        result, bakes = merge_baked_material(current, generated)
        fixed = result.find('SubMaterials/Material')
        self.assertEqual(bakes, ['new/roof_blend2_diff.dds'])
        self.assertEqual(fixed.find("Textures/Texture[@Map='Opacity']").get('File'), bakes[0])
        self.assertEqual(fixed.find("Textures/Texture[@Map='Emittance']").get('File'), 'installed/wood_dt.dds')
        self.assertEqual(fixed.find("Textures/Texture[@Map='Custom']/TexMod").attrib, {'TileU':'8', 'TileV':'4'})
        self.assertEqual(fixed.find('PublicParams').get('Detail2DiffuseScale'), '0.8')
        self.assertEqual(ET.tostring(result.find('SubMaterials')[1]), ET.tostring(current.find('SubMaterials')[1]))
        self.assertEqual(ET.tostring(current), before)
        again, _ = merge_baked_material(result, generated)
        self.assertEqual(ET.tostring(again), ET.tostring(result))

    def test_source_mask_product_survives_target_formula(self):
        second=np.full((8,8,4),[80,120,160,192],dtype='u1')
        mask=np.full((8,8),.25)
        uv=np.array([[0,0],[1,0],[0,1]],float)
        result,stats=bake_atlas(second,mask,[(uv,uv,uv)],(32,32))
        actual=result[5,5]/255
        np.testing.assert_array_equal(result[5,5,:3],second[0,0,:3])
        source=(192/255*.25*(1+2.65))**4.4
        target=(actual[3]*(1+2.65))**4.4
        self.assertAlmostEqual(source,target,delta=.005)
        self.assertGreater(stats['covered_pixels'],0)

    def test_negative_factor_is_folded_without_native_inversion(self):
        uv=np.array([[0,0],[1,0],[0,1]],float)
        source=np.full((4,4,4),255,dtype='u1')
        result,_=bake_atlas(source,np.full((4,4),.8),[(uv,uv,uv)],(16,16),factor=-.5)
        self.assertAlmostEqual(result[3,3,3]/255,.4,delta=1/255)

    def test_mask_samples_primary_uv_with_wrap(self):
        mask=np.array([[0.,1.],[0.,1.]])
        values=sample_wrap(mask,np.array([[.25,.25],[.75,.25],[1.25,-.75]]))
        np.testing.assert_array_equal(values,[0,1,0])

    def test_dds_mips_do_not_premultiply_alpha_or_average_srgb(self):
        image=np.array([[[0,0,0,0],[255,255,255,255]],[[0,0,0,0],[255,255,255,255]]],dtype='u1')
        data=rgba_dds(image)
        self.assertEqual(struct.unpack_from('<I',data,28)[0],2)
        self.assertEqual(struct.unpack_from('<I',data,128)[0],29)
        self.assertEqual(tuple(data[-4:]),(188,188,188,128))
        np.testing.assert_array_equal(np.array(Image.open(io.BytesIO(data))),image)

    def test_streaming_decoder_ignores_stale_hd_tail(self):
        # BC1 solid red, 8x8 top mip split off, 4x4 resident mip.
        block=struct.pack('<HHI',0xf800,0,0)
        header=bytearray(148);header[:4]=b'DDS '
        struct.pack_into('<7I',header,4,124,0x2100f,8,8,32,0,2)
        struct.pack_into('<II4s',header,76,32,4,b'DX10')
        struct.pack_into('<I',header,108,0x401008)
        struct.pack_into('<5I',header,128,72,3,0,1,0)
        image=decode_texture(bytes(header)+block,{'1':block*4,'2':b'stale higher HD mip'})
        self.assertEqual(image.shape,(8,8,4));np.testing.assert_array_equal(image[0,0],[255,0,0,255])
        with self.assertRaisesRegex(ValueError,'Missing or incompatible'):
            decode_texture(bytes(header)+block,{})

    def test_bc4_single_channel_mask_reassembles_streamed_mips(self):
        header=bytearray(148);header[:4]=b'DDS '
        struct.pack_into('<7I',header,4,124,0x2100f,8,8,32,0,2)
        struct.pack_into('<II4s',header,76,32,4,b'DX10')
        struct.pack_into('<I',header,108,0x401008)
        struct.pack_into('<5I',header,128,80,3,0,1,0)
        # Endpoint 64, index zero at every pixel; BC4 carries linear mask values.
        block=bytes([64,192])+bytes(6)
        image=decode_texture(bytes(header)+block,{'1':block*4})
        self.assertEqual(image.shape,(8,8,4))
        np.testing.assert_array_equal(image[:,:,0],np.full((8,8),64))
        np.testing.assert_array_equal(image[:,:,3],np.full((8,8),255))
        with self.assertRaisesRegex(ValueError,'Missing or incompatible'):
            decode_texture(bytes(header)+block,{'1':block})

    def test_chart_repack_preserves_geometry_and_physics(self):
        if importlib.util.find_spec('xatlas') is None:
            self.skipTest('optional xatlas backend not installed')
        original=triangle_mesh()
        converted,triangles,size=reatlas(original,0,resolution=64)
        old={c.id:c.data for c in read_chunks(original)};new={c.id:c.data for c in read_chunks(converted)}
        self.assertEqual(old[42],new[42])
        indices=np.frombuffer(new[15],'<u2',offset=24)
        positions=np.frombuffer(new[10],'<f4',offset=24).reshape(-1,3)
        primary=np.frombuffer(new[12],'<f4',offset=24).reshape(-1,2)
        np.testing.assert_array_equal(positions[indices],np.frombuffer(old[10],'<f4',offset=24).reshape(-1,3))
        np.testing.assert_array_equal(primary[indices],np.frombuffer(old[12],'<f4',offset=24).reshape(-1,2))
        self.assertEqual(len(triangles),2)
        # Original secondary islands overlap. New charts must still bake
        # different mask values without an overlap conflict.
        result,stats=bake_atlas(np.full((4,4,4),255,dtype='u1'),np.array([[0.,1.],[0.,1.]]),triangles,size,primary_tiling=.5)
        self.assertGreater(stats['covered_pixels'],0)
        self.assertGreater(np.ptp(result[:,:,3]),100)

    def test_slot_conversion_cannot_silently_drop_unbaked_mask(self):
        element=ET.fromstring('''<Material Shader="Illum" StringGenMask="%BLENDLAYER%SNDUVS">
          <Textures><Texture Map="[1] Diffuse" File="second.dds"/><Texture Map="Opacity" File="mask.dds"/></Textures></Material>''')
        self.assertEqual(len(list(blend_inputs(element))),1)
        features={'%BLENDLAYER':1,'%SNDUVS':2}
        with self.assertRaisesRegex(ValueError,'Bake the primary'):
            convert_material_features(element,features,features)


if __name__=='__main__':unittest.main()
