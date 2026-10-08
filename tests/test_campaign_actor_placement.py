from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from quest_import import ast
from campaign_actor_placement import build, entity_guid


class PlacementTests(unittest.TestCase):
    def record(self):
        return dict(layer='authored', entity=ast(ET.fromstring(
            '<Entity Name="person" EntityClass="NPC" EntityId="42" '
            'EntityGuid="4D8E3D5634F7C351" Pos="1,2,3" Rotate="0,0,0,1">'
            '<EntityLinks><Link Name="Home" TargetId="88"/></EntityLinks></Entity>')),
            soul=ast(ET.fromstring('<Soul><SharedSoulGuid>shared</SharedSoulGuid>'
                '<Guid>instance</Guid><EntityGuid>5588471628078498641</EntityGuid>'
                '<Name>person</Name><StaticData><InventoryId>pockets</InventoryId></StaticData></Soul>')))

    def test_native_identity_and_transform_keep_source_dependencies(self):
        objects, souls, report = build(dict(npcs=[self.record()]), {'shared'})
        e = objects.find('Entity'); s = souls.find('./SoulList/Souls/Soul')
        self.assertEqual(e.get('EntityGuid'), '4d8e3d56-34f7-c351')
        self.assertEqual(s.findtext('EntityGuid'), e.get('EntityGuid'))
        self.assertEqual(s.get('version'), '8')
        self.assertEqual(s.findtext('Guid'), 'instance')
        self.assertEqual(e.get('Pos'), '1,2,3')
        self.assertEqual(e.get('Rotate'), '0,0,0,1')
        self.assertIsNone(e.find('EntityLinks'))
        self.assertEqual(report['dependencies'][0]['source_entity']['children'][0]['op'], 'EntityLinks')
        self.assertFalse(report['campaign_ready'])

    def test_unregistered_not_substituted(self):
        objects, _, report = build(dict(npcs=[self.record()]), set())
        self.assertEqual(len(objects), 0)
        self.assertEqual(report['omitted_unregistered'], ['person'])

    def test_duplicate_identity_and_mismatched_binding_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            build(dict(npcs=[self.record(), self.record()]), {'shared'})
        record = self.record()
        record['entity']['attributes']['EntityGuid'] = '1111111111111111'
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            build(dict(npcs=[record]), {'shared'})
        with self.assertRaises(ValueError): entity_guid('0')


if __name__ == '__main__': unittest.main()
