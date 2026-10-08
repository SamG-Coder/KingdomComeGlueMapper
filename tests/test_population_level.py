from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_population_level import merge_objects


class PopulationLevelTests(unittest.TestCase):
    def test_rejects_collision_before_merge(self):
        base = b'<Objects><Entity Name="existing" EntityId="42"/></Objects>'
        with self.assertRaisesRegex(ValueError, 'EntityId'):
            merge_objects(base, ET.fromstring('<Objects><Entity Name="other" EntityId="42"/></Objects>'))

    def test_existing_world_retained(self):
        result = ET.fromstring(merge_objects(b'<Objects><Entity Name="building"/></Objects>',
            ET.fromstring('<Objects><Entity Name="parent" Pos="1,2,3"/></Objects>')))
        self.assertEqual([e.get('Name') for e in result], ['building', 'parent'])
        self.assertEqual(result[1].get('Pos'), '1,2,3')


if __name__ == '__main__': unittest.main()
