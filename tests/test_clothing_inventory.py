from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from clothing_inventory import validate_equipment, require_equipment


def fixture():
    return {
        'Libs/Storm/equipment/person.xml': '<storm><rules><rule><selectors><hasName name="actor"/></selectors>'
            '<operations><setInventory preset="person_equipped"/></operations></rule></rules></storm>',
        'Libs/Tables/item/InventoryPreset__person.xml': '<database>'
            '<InventoryPreset Name="person_equipped"><InventoryPresetRef Name="person_clothes"/>'
            '<InventoryPresetRef Name="carried"/></InventoryPreset>'
            '<InventoryPreset Name="carried"/>'
            '<InventoryPreset Name="person_clothes"><ClothingPresetRef Name="person_outfit"/></InventoryPreset></database>',
        'Libs/Tables/item/clothing_preset__person.xml': '<database><clothing_preset clothing_preset_name="person_outfit">'
            '<Items><Guid>item-guid</Guid></Items></clothing_preset></database>',
        'Libs/Tables/item/item__person.xml': '<database><Armor Id="item-guid" Clothing="garment"/></database>',
        'Libs/Tables/Character/CharacterComponent__person.xml': '<database><Clothing Name="garment"/></database>'}


class ClothingInventoryTests(unittest.TestCase):
    def check(self, data):
        return validate_equipment(data, {'person': 'actor'})[0]

    def test_actual_nested_equipment_resolves_through_clothing_items_to_components(self):
        row = self.check(fixture())
        self.assertEqual(row['status'], 'checked')
        self.assertEqual(row['inventories'], ['carried', 'person_clothes', 'person_equipped'])
        self.assertEqual(row['clothing_presets'], ['person_outfit'])
        self.assertEqual(row['components'], ['garment'])
        self.assertFalse(row['runtime_verified'])

    def test_existing_mesh_family_cannot_hide_missing_equipped_inventory(self):
        data = fixture()
        data['Libs/Tables/item/InventoryPreset__person.xml'] = (
            '<database><InventoryPreset Name="person_clothes"><ClothingPresetRef Name="person_outfit"/>'
            '</InventoryPreset></database>')
        row = self.check(data)
        self.assertEqual(row['status'], 'error')
        self.assertIn('equipment_reference_missing', [i['code'] for i in row['issues']])
        self.assertEqual(row['clothing_presets'], [])
        with self.assertRaisesRegex(ValueError, 'person_equipped'):
            require_equipment(data, {'person': 'actor'})

    def test_each_link_is_validated(self):
        for name in ('clothing_preset', 'item', 'charactercomponent'):
            data = fixture()
            key = next(k for k in data if '/'+name+'__' in k.lower())
            data[key] = '<database/>'
            with self.subTest(name=name):
                self.assertEqual(self.check(data)['status'], 'error')

    def test_cycle_is_an_error_even_with_a_reachable_outfit(self):
        data = fixture()
        key = 'Libs/Tables/item/InventoryPreset__person.xml'
        data[key] = data[key].replace('<InventoryPreset Name="carried"/>',
            '<InventoryPreset Name="carried"><InventoryPresetRef Name="person_equipped"/></InventoryPreset>')
        self.assertIn('equipment_inventory_cycle', [i['code'] for i in self.check(data)['issues']])

    def test_ambiguous_reached_definitions_fail_but_unrelated_loot_does_not(self):
        data = fixture()
        data['Libs/Tables/item/InventoryPreset__loot1.xml'] = '<database><InventoryPreset Name="other" Health="1"/></database>'
        data['Libs/Tables/item/InventoryPreset__loot2.xml'] = '<database><InventoryPreset Name="other" Health="2"/></database>'
        self.assertEqual(self.check(data)['status'], 'checked')
        data['Libs/Tables/item/InventoryPreset__loot2.xml'] = '<database><InventoryPreset Name="carried" Health="2"/></database>'
        self.assertIn('equipment_reference_ambiguous', [i['code'] for i in self.check(data)['issues']])

    def test_empty_outfit_and_missing_storm_are_errors(self):
        data = fixture()
        data['Libs/Tables/item/clothing_preset__person.xml'] = '<database><clothing_preset clothing_preset_name="person_outfit"/></database>'
        self.assertIn('equipment_outfit_empty', [i['code'] for i in self.check(data)['issues']])
        del data['Libs/Storm/equipment/person.xml']
        self.assertIn('equipment_storm_binding_missing', [i['code'] for i in self.check(data)['issues']])


if __name__ == '__main__':
    unittest.main()
