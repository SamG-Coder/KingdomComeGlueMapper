import sys
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from npc_effects import (legacy_bg_mask, native_skin_material, native_vertex_features,
                         read_dds_family, bake_macro_diffuse, native_clothing_material, write_float_dds)
from clothing_regions import Skin
from test_clothing_assembly import garment_skin


class NPCEffectsTests(unittest.TestCase):
    def test_legacy_mask_channels_are_not_rebound_blindly(self):
        source = Image.new('RGBA',(1,1),(13,47,81,159))
        self.assertEqual(legacy_bg_mask(source).getpixel((0,0)),(159,47,0,255))
        self.assertEqual(source.getpixel((0,0)),(13,47,81,159))

    def test_native_skin_preserves_surface_and_source(self):
        source = ET.fromstring('<Material Shader="HumanSkin"><Textures><Texture Map="Diffuse" File="face.dds"/></Textures></Material>')
        ref = ET.fromstring('<Material><PublicParams BloodTintCenter="1,0,0" GrimeDiffuse="0.1,0.1,0.1" Melanin="9"/></Material>')
        result = native_skin_material(source,'bg.dds',ref)
        self.assertIsNone(source.find('PublicParams'))
        self.assertEqual(result.find('Textures/Texture[@Map="Diffuse"]').get('File'),'face.dds')
        self.assertEqual(result.find('Textures/Texture[@Map="Emittance"]').get('File'),'bg.dds')
        self.assertNotIn('Melanin', result.find('PublicParams').attrib)

    def test_vertex_conversion_preserves_geometry_and_skinning(self):
        source = garment_skin()
        a, b = Skin(source), Skin(native_vertex_features(source,3))
        self.assertEqual(a.faces,b.faces)
        self.assertEqual(a.remap,b.remap)
        for key,stream in a.streams.items():
            if key != 3: self.assertEqual(stream.data,b.streams[key].data)
        self.assertEqual(b.streams[3].data[24:],bytes((255,255,227,255))*a.vertex_count)
        self.assertEqual(native_vertex_features(native_vertex_features(source,3),3),native_vertex_features(source,3))
        with self.assertRaises(ValueError): native_vertex_features(source,27)

    def test_dds_roundtrip_and_incomplete_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'mask.dds'
            im=Image.new('RGBA',(4,4),(23,55,79,11)); im.save(path)
            self.assertEqual(read_dds_family(path).tobytes(),im.tobytes())
            (Path(tmp)/'mask.dds.2').write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'Incomplete'): read_dds_family(path)

    def test_macro_conversion_compensates_native_sampling(self):
        source=Image.new('RGBA',(1,1),(128,64,32,99))
        result=bake_macro_diffuse(source,ET.Element('Material'))
        sampled=result[0,0,:3].astype(np.float32)**(1/2.2)
        original=np.array(source.getpixel((0,0))[:3])/255
        expected=np.where(original<=.04045,original/12.92,((original+.055)/1.055)**2.4)
        np.testing.assert_allclose(sampled,expected,atol=.0001)
        self.assertAlmostEqual(float(result[0,0,3]),99/255,places=3)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'macro.dds';write_float_dds(path,result)
            payload=path.read_bytes()
            self.assertEqual(payload[84:88],b'DX10')
            np.testing.assert_array_equal(np.frombuffer(payload[148:],dtype='<f2'),result.reshape(-1))

    def test_clothing_removes_conflicting_bindings(self):
        source=ET.fromstring('<Material Shader="Illum" StringGenMask="%COLORIZING%DETAIL_MAPPING%NORMAL_MAP"><Textures><Texture Map="Bumpmap" File="normal.dds"/><Texture Map="Custom" File="old_damage.dds"/></Textures></Material>')
        ref=ET.fromstring('<Material><PublicParams BloodBreakup="0.2"/></Material>')
        result=native_clothing_material(source,'macro.dds','bgs.dds',ref,'LegacySurface')
        self.assertEqual(result.get('StringGenMask'),'%CLOTHING_SYSTEM%NORMAL_MAP')
        self.assertEqual(result.find('FeatureSlots').get('Slot00'),'LegacySurface')
        self.assertEqual(result.find('Textures/Texture[@Map="Custom"]').get('File'),'bgs.dds')
        self.assertEqual(source.find('Textures/Texture[@Map="Custom"]').get('File'),'old_damage.dds')


if __name__=='__main__': unittest.main()
