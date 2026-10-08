import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_linker import discover_entry, link_entities, operation_records
from quest_import import parse_behavior


class CampaignLinkerTests(unittest.TestCase):
    def test_entry_discovery_is_independent_of_campaign_names(self):
        for name in ('different_story', 'second_campaign'):
            data = f'''<Graph><Nodes><Node Id="3" Class="System:ExecuteScript"><Inputs Script="local ent=System.GetEntityByName('{name}'); XGenAIModule.SendMessageToEntity(ent.this.id,'story:begin','')"/></Node></Nodes></Graph>'''.encode()
            result = discover_entry(data)
            self.assertEqual(result['dispatch']['entity_name'], name)
            self.assertEqual(result['dispatch']['message'], 'story:begin')
        with self.assertRaises(ValueError): discover_entry(b'<Graph><Nodes/></Graph>')

    def test_recursive_links_cross_layers_preserve_cycles_and_trigger_shapes(self):
        documents = {'objects_mission0.xml': b'''<Objects><Entity Name="controller" EntityId="1" EntityGuid="A" EntityClass="QuestObject"><EntityLinks><Link Name="next" TargetId="2" TargetGuid="B"/></EntityLinks></Entity></Objects>''',
                     'layers/other.xml': b'''<Objects><Entity Name="trigger" EntityId="2" EntityGuid="B" EntityClass="AreaTrigger"><Points><Point Pos="1,2,3"/></Points><EntityLinks><Link Name="owner" TargetId="1" TargetGuid="A"/></EntityLinks></Entity></Objects>'''}
        result = link_entities(documents, 'controller')
        self.assertEqual(len(result['entities']), 2)
        self.assertEqual(len(result['links']), 2)
        self.assertFalse(result['unresolved'])
        trigger = result['entities']['layers/other.xml#0']['entity']
        self.assertEqual(trigger['children'][0]['children'][0]['attributes']['Pos'], '1,2,3')

    def test_ambiguous_layers_and_disagreeing_ids_are_not_silently_linked(self):
        docs = {'a': b'''<Objects><Entity Name="start" EntityId="1"><EntityLinks><Link TargetId="2" TargetGuid="0"/></EntityLinks></Entity><Entity Name="one" EntityId="2" EntityGuid="B"/></Objects>''',
                'b': b'<Objects><Entity Name="variant" EntityId="2" EntityGuid="C"/></Objects>'}
        result = link_entities(docs, 'start')
        self.assertEqual(len(result['unresolved']), 1)
        self.assertEqual(len(result['entities']), 1)
        docs['a'] = docs['a'].replace(b'TargetGuid="0"', b'TargetGuid="C"').replace(b'TargetId="2"', b'TargetId="9"')
        self.assertEqual(link_entities(docs, 'start')['unresolved'][0]['reason'], 'GUID and entity ID disagree')

    def test_equipment_grant_keeps_condition_and_sequence_position(self):
        trees = parse_behavior(b'''<BehaviorTrees><BehaviorTree name="entry"><Root><IfCondition condition="newGame"><Sequence><CreateItem ItemGUID="source-item" Amount="2" Target="__player"/><EquipItem Item="created"/></Sequence></IfCondition></Root></BehaviorTree></BehaviorTrees>''', 'test')
        records = operation_records(trees)
        self.assertEqual([r['family'] for r in records], ['inventory', 'equipment'])
        self.assertEqual(records[0]['ancestry'][1]['attributes']['condition'], 'newGame')
        self.assertEqual(records[0]['arguments']['Amount'], '2')
        self.assertNotEqual(records[0]['path'], records[1]['path'])
        self.assertTrue(all(r['adapter_status'] == 'unimplemented' for r in records))


if __name__ == '__main__': unittest.main()
