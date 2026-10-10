import struct
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_shared_materials import SharedMaterials, POPULATION, SHARED


class SharedMaterialTests(unittest.TestCase):
    def setUp(self):
        self.pool = SharedMaterials()
        self.a = POPULATION + 'a/part0.mtl'
        self.b = POPULATION + 'b/part0.mtl'
        self.payload = b'<Material Name="cloth"><SubMaterials><Material Name="skin" Shader="HumanSkin" Diffuse="1,1,1"/><Material Name="cloth" Shader="Illum" Diffuse="1,0,0"/></SubMaterials></Material>'
        self.pool.add(self.a, self.payload)
        self.pool.add(self.b, self.payload)

    def test_exact_semantics_shared_but_tints_names_and_slot_order_preserved(self):
        self.assertEqual(len(self.pool.materials), 1)
        for i, variant in enumerate((self.payload.replace(b'1,0,0', b'0,1,0'),
                                     self.payload.replace(b'Name="skin"', b'Name="other"'))):
            self.pool.add(POPULATION + f'c/part{i}.mtl', variant)
        root = ET.fromstring(self.payload)
        root[0][:] = list(reversed(root[0][:]))
        self.pool.add(POPULATION + 'c/reordered.mtl', ET.tostring(root))
        self.assertEqual(len(self.pool.materials), 4)
        self.assertEqual(self.pool.report()['source_shader_slots'], 10)
        self.assertEqual(self.pool.report()['shared_shader_slots'], 8)
        self.assertIsNone(self.pool.rewrite(self.a, self.payload))

    def test_components_keep_models_and_symbolic_features(self):
        source = b'<database><Component FilePath="gluetravel/gmp_a/"><SkinElement Model="part0.skin" Material="part0.mtl"/><Feature Material="cloth"/></Component><Component FilePath="native/"><SkinElement Model="body.skin" Material="body.mtl"/></Component></database>'
        result = ET.fromstring(self.pool.rewrite('Libs/Tables/character/components.xml', source))
        self.assertEqual(result[0].get('FilePath'), 'gluetravel/')
        self.assertEqual(result[0][0].get('Model'), 'gmp_a/part0.skin')
        self.assertEqual('objects/characters/gluetravel/' + result[0][0].get('Material'), self.pool.aliases[self.a])
        self.assertEqual(result[0][1].get('Material'), 'cloth')
        self.assertEqual(result[1].get('FilePath'), 'native/')
        with self.assertRaises(ValueError):
            self.pool.rewrite('Libs/Tables/character/components.xml', source.replace(b'part0.mtl', b'missing.mtl'))

    def test_binary_rewrite_changes_only_material_name_field(self):
        source = bytearray(240)
        struct.pack_into('<II', source, 8, 1, 16)
        struct.pack_into('<HHIII', source, 16, 0x1014, 0x802, 7, 160, 48)
        source[48:176] = self.a.removesuffix('.mtl').encode().ljust(128, b'\0')
        source[176:] = bytes(range(64))
        result = self.pool.rewrite(POPULATION + 'a/part0.skin', bytes(source))
        self.assertEqual(result[:48], source[:48])
        self.assertEqual(result[176:], source[176:])
        self.assertEqual(result[48:176].split(b'\0')[0].decode() + '.mtl', self.pool.aliases[self.a])
        self.assertEqual(self.pool.rewrite('objects/native.skin', bytes(source)), bytes(source))
        self.assertEqual(self.pool.mesh_references, 1)


if __name__ == '__main__': unittest.main()
