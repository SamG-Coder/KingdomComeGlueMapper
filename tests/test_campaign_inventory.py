from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_inventory import resolve_inventory


class InventoryTests(unittest.TestCase):
    def test_preserves_weighted_choices_and_random_counts(self):
        tables = dict(inventory=[dict(inventory_id='i', max_items='3', restock_interval='48')],
                      inventory2item=[], inventory2inventory_preset=[dict(inventory_id='i', inventory_preset_id='p', priority='0.3')],
                      inventory_preset=[dict(inventory_preset_id='p')],
                      inventory_preset2item=[dict(inventory_preset_id='p', item_id='x', amount='0', amount_random_add='2')])
        result = resolve_inventory('i', tables)
        self.assertEqual(result['preset_choices'][0]['reference']['priority'], '0.3')
        self.assertEqual(result['preset_choices'][0]['items'][0]['amount_random_add'], '2')
        self.assertEqual(result['item_dependencies'], ['x'])
        self.assertFalse(result['selection_evaluated'])
        tables['inventory_preset'] = []
        with self.assertRaises(ValueError): resolve_inventory('i', tables)


if __name__ == '__main__': unittest.main()
