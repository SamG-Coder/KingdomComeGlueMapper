from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from quest_lab.item_bindings import from_definitions, installed

A = '00000000-0000-0000-0000-000000000001'
B = '00000000-0000-0000-0000-000000000002'
C = '00000000-0000-0000-0000-000000000003'


class ItemBindingTests(unittest.TestCase):
    def test_uses_registered_replacement_and_rejects_placeholder(self):
        rows = {
            A: ET.Element('Item', Id=A, Model='objects/preproduction/traffic_cone/cone.cgf'),
            B: ET.Element('Item', Id=B, Name='containeritems_item_' + A.replace('-', '_')),
            C: ET.Element('Item', Id=C, Model='objects/preproduction/traffic_cone/cone.cgf'),
        }
        bindings = from_definitions(rows, 'player')
        self.assertEqual(bindings['item_map'], {A:B, B:B})
        self.assertEqual(bindings['registered'], [B])
        self.assertEqual(bindings['player_asset'], 'player')
        with self.assertRaises(ValueError): from_definitions(rows, 'invalid alias')

    def test_invalid_installation_paths_do_not_fall_back_to_empty_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'Tables.pak'):
                installed(folder, None, 'player')
            root = Path(folder)
            (root/'Data').mkdir()
            (root/'Data/Tables.pak').touch()
            with self.assertRaisesRegex(ValueError, 'Mod is missing'):
                installed(root, root/'absent', 'player')
