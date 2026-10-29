"""Character effect masks may share pixels without sharing surface parameters."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from character_native_materials import convert_character_materials
from npc_effects import bake_macro_diffuse
from test_clothing_assembly import garment_skin


class CharacterMaterialSharingTests(unittest.TestCase):
    def test_shared_decals_preserve_skin_cloth_channels_tint_and_distinct_inputs(self):
        donor = E.fromstring('''<Material Shader="Illum" GenMask="0"
            StringGenMask="%CLOTHING_SYSTEM" MtlFlags="524290" Opacity="1" vertModifType="0">
            <Textures/><PublicParams BloodTintCenter="1,0,0" GrimeDiffuse=".1,.1,.1"/>
            <SubMaterials><Material Name="haircap_LOD" Shader="Hair"/>
            <Material Shader="HumanSkin"><PublicParams BloodTintCenter="1,0,0" GrimeDiffuse=".1,.1,.1"/></Material></SubMaterials>
            </Material>''')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output/'Libs/Tables/Character').mkdir(parents=True)
            prefix = 'objects/characters/gluetravel/test/'
            folder = output/prefix; folder.mkdir(parents=True)
            image = Image.new('RGBA', (4,4), (130,90,50,255))
            image.save(folder/'diff.dds')
            Image.new('RGBA', (4,4), (13,47,81,159)).save(folder/'decal.dds')
            Image.new('RGBA', (4,4), (13,99,81,160)).save(folder/'other.dds')
            component = E.Element('Component', FilePath='gluetravel/test/')
            derived = E.SubElement(component, 'DerivedComponents')
            source_materials = {}
            for name, shader, tint, decal in (
                    ('first', 'Illum', '1,1,1', 'decal'),
                    ('second', 'Illum', '.5,1,1', 'decal'),
                    ('skin', 'HumanSkin', '1,1,1', 'decal'),
                    ('different', 'Illum', '1,1,1', 'other')):
                material = E.Element('Material', Name=name, Shader=shader, Diffuse=tint)
                textures = E.SubElement(material, 'Textures')
                E.SubElement(textures, 'Texture', Map='Diffuse', File=prefix+'diff.dds')
                E.SubElement(textures, 'Texture', Map='Decal', File=prefix+decal+'.dds')
                source_materials[name] = material
                (folder/(name+'.mtl')).write_bytes(E.tostring(material))
                (folder/(name+'.skin')).write_bytes(garment_skin())
                node = E.SubElement(derived, 'Body' if shader == 'HumanSkin' else 'Clothing')
                E.SubElement(E.SubElement(node, 'Elements'), 'SkinElement',
                             Model=name+'.skin', Material=name+'.mtl')
            def reference(assets, path):
                import copy
                root = copy.deepcopy(donor)
                if '/body/' in path or '/clothing/' in path:
                    root.remove(root.find('SubMaterials'))
                    if '/body/' in path:
                        root.set('Shader', 'HumanSkin')
                return root
            with patch('character_native_materials.reference', side_effect=reference):
                convert_character_materials(output, component, None, 'test', 'male')
            converted = {n:E.parse(folder/(n+'.mtl')).getroot() for n in source_materials}
            def effect(name):
                slot = 'Emittance' if name == 'skin' else 'Custom'
                return converted[name].find(f"Textures/Texture[@Map='{slot}']").get('File')
            self.assertEqual(effect('first'), effect('second'))
            self.assertEqual(len({effect(n) for n in converted}), 3)
            self.assertEqual(len(list(folder.glob('*_bgs.dds'))), 3)
            for name, expected in (('first',(159,47,192,255)), ('second',(159,47,192,255)),
                                   ('skin',(159,47,0,255)), ('different',(160,99,192,255))):
                with Image.open(output/effect(name)) as mask:
                    self.assertEqual(mask.getpixel((0,0)), expected)
            # Sharing effect inputs must not erase the item's independent tint.
            import numpy as np
            for name in ('first', 'second'):
                texture = converted[name].find("Textures/Texture[@Map='Diffuse']").get('File')
                actual = np.frombuffer((output/texture).read_bytes()[148:], '<f2')[:64].reshape(4,4,4)
                np.testing.assert_array_equal(actual, bake_macro_diffuse(image, source_materials[name]))


if __name__ == '__main__':
    unittest.main()
