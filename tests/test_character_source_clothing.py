import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_source_clothing import female_role, native_role, source_item
from character_native_materials import native_surface


class FemaleSourceClothingTests(unittest.TestCase):
    def test_roles_come_from_authored_part_and_layer_not_actor_or_item_name(self):
        for name in ('arbitrary_a', 'unrelated_b'):
            self.assertEqual(female_role(dict(model='folder/s2_p2_l1_v9.skin', clothing_name=name)),
                             ('F_SimpleDress', 'torso', 5))
            self.assertEqual(female_role(dict(model='s2_p2_l2_v0', clothing_name=name)),
                             ('F_SimpleDress', 'torso', 9))
        self.assertEqual(female_role(dict(model='s2_p4_l1_v0')), ('F_Shoes', 'feet', 2))
        with self.assertRaisesRegex(ValueError, 'female source'):
            female_role(dict(model='s1_p2_l1_v0'))
        with self.assertRaisesRegex(ValueError, 'No native female'):
            female_role(dict(model='s2_p3_l1_v0'))

    def test_template_resolves_inherited_role_not_first_coat(self):
        components = ET.fromstring('<Root><Clothing Name="Coat" ArmorType="Coat" ArmorArchetypeId="99"/>'
            '<Clothing Name="F_Dress" ArmorType="F_SimpleDress" ArmorArchetypeId="27"><DerivedComponents>'
            '<Clothing Name="F_SimpleDress" ArmorType="F_SimpleDress"><DerivedComponents>'
            '<Clothing Name="dress_variant"/></DerivedComponents></Clothing></DerivedComponents></Clothing></Root>')
        items = ET.fromstring('<Root><Armor Clothing="Coat" UIName="coat"/>'
                             '<Armor Clothing="dress_variant" UIName="dress"/></Root>')
        archetype, template = native_role(components, items, 'F_SimpleDress')
        self.assertEqual(archetype, '27'); self.assertEqual(template.get('UIName'), 'dress')

    def test_import_retains_source_item_identity_and_localized_text(self):
        data = {'item': {'item_name': 'source_gown'}, 'armor': {'max_status': '40'},
                'player_item': {'ui_name': 'ui_gown', 'ui_info': 'ui_gown_info'},
                'pickable_item': {'weight': '2', 'price': '274'}, 'equippable_item': {'charisma': '8'}}
        class Tables:
            def row(self, table, field, identity):
                return data[table.split('/')[-1]]
        strings = ET.fromstring('<Table><Row><Cell>ui_gown</Cell><Cell>Gown</Cell><Cell>Gown</Cell></Row>'
            '<Row><Cell>ui_gown_info</Cell><Cell>A source dress.</Cell><Cell>A source dress.</Cell></Row></Table>')
        template = ET.Element('Armor', UIName='wrong_coat', Price='1', Weight='1', IconId='native_dress')
        a, text = source_item(Tables(), 'source-id', 'female_a', 'gown_component', template, strings)
        b, _ = source_item(Tables(), 'source-id', 'female_a', 'gown_component', template, strings)
        self.assertEqual(a.get('Id'), b.get('Id'))
        self.assertEqual(a.get('Clothing'), 'gown_component')
        self.assertEqual(a.get('UIName'), 'female_a_ui_gown')
        self.assertEqual(a.get('Price'), '274'); self.assertEqual(a.get('Weight'), '2')
        self.assertEqual(a.get('MaxStatus'), '40'); self.assertEqual(a.get('Charisma'), '8')
        self.assertEqual(text[0].findall('Cell')[1].text, 'Gown')
        self.assertEqual(template.get('UIName'), 'wrong_coat')

    def test_native_material_state_preserves_source_hair_tint_and_maps(self):
        native = ET.fromstring('<Material Shader="Hair" GenMask="11004008" Opacity="1" StringGenMask="%HAIR_PASS">'
            '<Textures><Texture Map="Diffuse" File="native.dds"/><Texture Map="Detail" File="direction.dds"/></Textures>'
            '<PublicParams/></Material>')
        old = ET.fromstring('<Material Shader="Hair" GenMask="261" Opacity="0.99" Diffuse="0.37,0.30,0.20" AlphaTest="0.32">'
            '<Textures><Texture Map="Diffuse" File="source.dds"/></Textures></Material>')
        result = native_surface(old, native)
        self.assertEqual(result.get('GenMask'), '11004008')
        self.assertEqual(result.get('Opacity'), '1')
        self.assertEqual(result.get('Diffuse'), '0.37,0.30,0.20')
        self.assertEqual(result.find("Textures/Texture[@Map='Diffuse']").get('File'), 'source.dds')
        self.assertEqual(result.find("Textures/Texture[@Map='Detail']").get('File'), 'direction.dds')
        self.assertEqual(old.get('Opacity'), '0.99')


if __name__ == '__main__':
    unittest.main()
