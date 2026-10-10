import sys
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from region_travel_tables import managed_patches, validate_required_fields


class TravelTableTests(unittest.TestCase):
    def test_faction_patch_validates_nested_names_and_uses_manifest_suffix(self):
        def tree(parent, child):
            return f'<database><FactionTree version="1"><Faction Name="{parent}"><Children><Faction Name="{child}"/></Children></Faction></FactionTree></database>'.encode()
        files = {'Libs/Tables/rpg/FactionTree__people.xml': tree('source_root', 'source_child')}
        result = managed_patches(files, 'travel')
        self.assertIn('Libs/Tables/rpg/FactionTree__travel.xml', result)
        files['Libs/Tables/rpg/FactionTree__other.xml'] = tree('other_root', 'source_child')
        with self.assertRaisesRegex(ValueError, 'duplicate nested faction'):
            managed_patches(files, 'travel')

    def test_character_materials_use_the_manifest_patch_suffix_and_keep_both_people(self):
        def material(name):
            return f'<database><ClothingMaterials version="1"><Material Name="{name}"/></ClothingMaterials></database>'.encode()
        result = managed_patches({
            'Libs/Tables/Character/ClothingMaterial__first.xml': material('first_fabric'),
            'Libs/Tables/Character/ClothingMaterial__second.xml': material('second_fabric'),
        }, 'travel')
        self.assertEqual(list(result), ['Libs/Tables/Character/ClothingMaterial__travel.xml'])
        self.assertEqual([e.get('Name') for e in ET.fromstring(next(iter(result.values())))[0]],
                         ['first_fabric', 'second_fabric'])

    def test_merge_preserves_coach_and_merchant_and_unrelated_resources(self):
        files = {
            'Libs/Tables/rpg/role__gluemappertravel.xml': b'<database name="barbora"><roles version="1"><role role_name="COACH" metarole_name="NPC"/></roles></database>',
            'Libs/Tables/rpg/role__rattay.xml': b'<database name="barbora"><roles version="1"><role role_name="INNKEEPER" metarole_name="NPC"/></roles></database>',
            'Quests/test.xml': b'unchanged',
        }
        result = managed_patches(files, 'gluemappertravel')
        self.assertEqual(set(result), {'Libs/Tables/rpg/role__gluemappertravel.xml', 'Quests/test.xml'})
        self.assertEqual(result['Quests/test.xml'], b'unchanged')
        self.assertEqual([e.get('role_name') for e in ET.fromstring(result['Libs/Tables/rpg/role__gluemappertravel.xml'])[0]], ['COACH', 'INNKEEPER'])
        self.assertEqual(managed_patches(result, 'gluemappertravel'), result)

    def test_reject_duplicate_identity_and_mismatched_version(self):
        a = b'<database><souls version="2"><soul soul_id="same"/></souls></database>'
        for b in (a, a.replace(b'version="2"', b'version="1"')):
            with self.assertRaises(ValueError):
                managed_patches({'Libs/Tables/rpg/soul__a.xml': a, 'Libs/Tables/rpg/soul__b.xml': b}, 'testmod')

    def test_inventory_parts_combine_without_losing_stock_or_clothing(self):
        def preset(name, child):
            return f'<database><InventoryPresets version="2"><InventoryPreset Name="{name}">{child}</InventoryPreset></InventoryPresets></database>'.encode()
        result = managed_patches({
            'Libs/Tables/item/InventoryPreset__stock.xml': preset('stock', '<PresetItem Name="bread" ItemClassId="food" Amount="5"/>'),
            'Libs/Tables/item/InventoryPreset__clothes.xml': preset('clothes', '<ClothingPresetRef Name="outfit"/>'),
        }, 'travel')
        rows = ET.fromstring(result['Libs/Tables/item/InventoryPreset__travel.xml'])[0]
        self.assertEqual(rows[0][0].get('Amount'), '5')
        self.assertEqual(rows[1][0].get('Name'), 'outfit')

    def test_reject_v17_stock_guid_without_required_name(self):
        row = ET.fromstring('<PresetItem ItemClassId="c352d8ae-4021-4f9b-b49c-b1f087f2cd2c" Amount="20"/>')
        with self.assertRaisesRegex(ValueError, 'missing required attributes: Name'):
            validate_required_fields(row, 'stock.xml')
        row.set('Name', 'bread')
        validate_required_fields(row, 'stock.xml')

    def test_reject_v17_incomplete_outfit_item(self):
        row = ET.fromstring('<Armor Id="outfit-id" Name="outfit" Clothing="outfit" MaxStatus="100" Weight="1" Price="1" UIName="ui_nm_warning" UIInfo="ui_in_warning"/>')
        with self.assertRaisesRegex(ValueError, 'DefenseStab.*IconId'):
            validate_required_fields(row, 'item.xml')
