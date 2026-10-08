import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_skald_level_probe import inject_entities
from skald_state_probe import project_entities


class SkaldLevelTests(unittest.TestCase):
    def test_avoids_reserved_low_ids_and_existing_high_ids(self):
        fragment = project_entities('Campaign', 'Quests/Campaign.xml')
        for source, expected in [(b'<Objects/>', '1000'),
                                  (b'<Objects><Entity EntityId="200000"/></Objects>', '200001')]:
            result = ET.fromstring(inject_entities(source, fragment))
            self.assertEqual(result.find("Entity[@Name='Campaign']").get('EntityId'), expected)

    def test_preserves_existing_objects_and_allocates_unique_ids(self):
        source = b'<Objects><Entity Name="existing" EntityId="900"/><Brush Name="terrain"/></Objects>'
        fragment = project_entities('Campaign', 'Quests/Campaign.xml')
        result = ET.fromstring(inject_entities(source, fragment))
        self.assertEqual([e.get('EntityId') for e in result.findall('Entity')], ['900', '1001', '1002'])
        self.assertIsNotNone(result.find('Brush'))
        with self.assertRaises(ValueError):
            inject_entities(ET.tostring(result), fragment)


if __name__ == '__main__': unittest.main()
