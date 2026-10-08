from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_world_profile import resolve_profile


class WorldProfileTests(unittest.TestCase):
    def test_case_insensitive_layers_and_both_human_classes_join_decimal_souls(self):
        profile = E.fromstring('<Root><GameProfile><Name>opening</Name><GameLayers><GameLayer>Actors{AB}</GameLayer></GameLayers></GameProfile></Root>')
        souls = E.fromstring('<Root><SoulList><Souls><Soul><EntityGuid>255</EntityGuid><Name>actor</Name></Soul></Souls></SoulList></Root>')
        for cls in ('NPC', 'NPC_Female'):
            data = f'<Objects><Entity Name="actor" EntityClass="{cls}" EntityGuid="FF"/></Objects>'.encode()
            result = resolve_profile(profile, souls, {'layers/actors{ab}.xml': data}, 'opening')
            self.assertEqual(len(result['npcs']), 1)
            self.assertIsNotNone(result['npcs'][0]['soul'])
            self.assertEqual(result['unresolved'], [])
        self.assertEqual(resolve_profile(profile, souls, {}, 'opening')['unresolved'][0]['kind'], 'layer')
        with self.assertRaises(ValueError): resolve_profile(profile, souls, {}, 'unknown')


if __name__ == '__main__': unittest.main()
