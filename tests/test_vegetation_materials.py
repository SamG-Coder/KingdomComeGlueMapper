import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from vegetation_materials import convert_shadow_proxies


class ShadowProxyConversionTests(unittest.TestCase):
    def test_preserves_slots_and_lods_while_reassigning_shadows(self):
        doc = ET.fromstring('''<Material><SubMaterials>
          <Material Name="proxy" Shader="Nodraw" MtlFlags="525440"/>
          <Material Name="shadow_proxy" Shader="Vegetation" MtlFlags="524416" StringGenMask="%LEAVES"/>
          <Material Name="atlas" Shader="Vegetation" MtlFlags="524448" StringGenMask="%LEAVES%NORMAL_MAP"><Textures><Texture File="leaves.dds"/></Textures></Material>
          <Material Name="LOD2" Shader="Vegetation" MtlFlags="524416"/>
          <Material Name="wood" Shader="Illum" MtlFlags="32"/>
        </SubMaterials></Material>''')
        slots = list(doc.find('SubMaterials'))
        self.assertEqual(convert_shadow_proxies(doc), 1)
        self.assertEqual(list(doc.find('SubMaterials')), slots)
        self.assertEqual(slots[1].get('Shader'), 'Nodraw')
        self.assertEqual(slots[2].get('MtlFlags'), '524416')
        self.assertEqual(slots[2].get('StringGenMask'), '%LEAVES%NORMAL_MAP')
        self.assertEqual(slots[2].find('Textures/Texture').get('File'), 'leaves.dds')
        self.assertEqual(slots[3].get('Shader'), 'Vegetation')
        self.assertEqual(slots[4].get('MtlFlags'), '32')
        before = ET.tostring(doc)
        self.assertEqual(convert_shadow_proxies(doc), 0)
        self.assertEqual(ET.tostring(doc), before)

    def test_does_not_hide_lod_or_nonvegetation_proxy_names(self):
        doc = ET.fromstring('<Material><SubMaterials><Material Name="LOD" Shader="Vegetation" MtlFlags="32"/><Material Name="shadow_proxy" Shader="Illum"/></SubMaterials></Material>')
        before = ET.tostring(doc)
        self.assertEqual(convert_shadow_proxies(doc), 0)
        self.assertEqual(ET.tostring(doc), before)


if __name__ == '__main__':
    unittest.main()
