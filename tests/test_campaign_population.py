from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from quest_import import ast
from campaign_population import resolve_actor


class PopulationTests(unittest.TestCase):
    def test_uses_authored_instance_body_and_preserves_runtime_dependencies(self):
        soul = ast(ET.fromstring('<Soul><Guid>instance</Guid><StaticData><InventoryId>inventory</InventoryId><VoiceId>voice</VoiceId><CharacterBodyDescription><BodyId>authored-body</BodyId></CharacterBodyDescription><InitialClothingDescription><PresetId>outfit</PresetId></InitialClothingDescription><InitialAIData><BrainId>brain</BrainId></InitialAIData></StaticData></Soul>'))
        entity = ast(ET.fromstring('<Entity Name="actor" Pos="1,2,3"><EntityLinks><Link Name="Home" TargetId="8"/></EntityLinks></Entity>'))
        tables = {'character_body': [{'character_body_id':'authored-body'}],
                  'item/armor2clothing_preset': [], 'item/clothing': [], 'item/armor': []}
        result = resolve_actor(dict(soul=soul, entity=entity, layer='layer'), tables.__getitem__)
        self.assertEqual(result['parts'][0]['character_body_id'], 'authored-body')
        self.assertEqual(result['inventory_id'], 'inventory')
        self.assertEqual(result['voice_id'], 'voice')
        self.assertEqual(result['entity_links']['children'][0]['attributes']['TargetId'], '8')
        self.assertFalse(result['converted'])


if __name__ == '__main__': unittest.main()
