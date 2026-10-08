import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_bindings import build_bindings, emit_assets, native_links, parse_link_label, parse_link_labels, registration_errors
from campaign_entity_links import native_guid
from quest_import import ast


def fixture(quest='another_story', position='11,22,33'):
    documents = {
        'quest': E.fromstring(f'<Entity Name="{quest}" EntityClass="QuestObject" EntityGuid="1111000022223333"/>'),
        'actor': E.fromstring('<Entity Name="any_actor" EntityClass="NPC_Female" EntityGuid="123456789abcdef0"/>'),
        'area': E.fromstring('<Entity Name="any_area" EntityClass="TriggerArea" EntityGuid="432187659abcdef0"/>'),
        'spot': E.fromstring(f'<Entity Name="any_point" EntityClass="TagPoint" EntityGuid="5555666677778888" Pos="{position}" Rotate="1,0,0,0" Layer="story_layer"/>'),
    }
    entities = {k: dict(source='layer/' + k, attributes=dict(v.attrib), entity=ast(v)) for k, v in documents.items()}
    links = [dict({'from': 'quest', 'to': target}, source_link={'Name': label}) for target, label in [
        ('actor', "QuestActor[('shopkeeper')]"), ('area', "QuestPlace['doorway']"),
        ('spot', "CutsceneSpot[npc('shopkeeper'),alias('first_meeting')]")]]
    model = dict(quest=quest, quest_id='7', tables={
        'quest': dict(rows=[dict(quest_name=quest, quest_id='7', smart_object=quest)]),
        'quest_npc': dict(rows=[dict(quest_asset_id='8', quest_id='7', soul_id='source-person', soul_faction_id='', soul_class_id='')]),
        'quest_item': dict(rows=[dict(quest_asset_id='9', quest_id='7', item_id='source-item', min_health='0')]),
        'quest_place': dict(rows=[dict(quest_asset_id='10', quest_id='7', entity='any_area', map='', show_as_area='True')]),
        'quest_tracked_asset': dict(rows=[dict(objective_id='3', quest_asset_id='9', map_asset_id='10', count='2')]),
    }, behavior_documents={'libs/ai/quests/' + quest + '.xml': dict(trees={'onUpdate': dict(root=ast(E.fromstring(
        '<Root><AreaPresence Area="t_places[&apos;doorway&apos;]"/></Root>')))})})
    souls = E.fromstring(f'<Root><SoulList><Souls><Soul><EntityGuid>{int("123456789abcdef0",16)}</EntityGuid><SharedSoulGuid>source-person</SharedSoulGuid><Name>any_actor</Name></Soul></Souls></SoulList></Root>')
    world = dict(entities=entities, links=links, unresolved=[])
    return model, world, souls


class CampaignBindingTests(unittest.TestCase):
    def test_multiple_tags_keep_separate_alias_and_seat(self):
        result = parse_link_labels("QuestPlace['arbitrary'],Seat, CutsceneSpot[npc('someone'), alias('greeting')]")
        self.assertEqual([r['tag'] for r in result], ['QuestPlace','Seat','CutsceneSpot'])
        self.assertEqual(result[0]['positional'], ['arbitrary'])
        self.assertEqual(result[2]['named'], dict(npc='someone', alias='greeting'))

    def test_original_cutscene_links_and_transforms_are_data_not_selected_spawns(self):
        for name, position in [('another_story', '11,22,33'), ('different_story', '70,80,90')]:
            m, world, souls = fixture(name, position)
            before = copy.deepcopy(world)
            plan = build_bindings(m, world, souls)
            self.assertFalse(plan['unresolved'])
            self.assertFalse(plan['executable'])
            spot = next(b for b in plan['bindings'] if b.get('entity') == 'spot')
            self.assertEqual(spot['transform']['Pos'], position)
            self.assertEqual(spot['layer'], 'story_layer')
            self.assertEqual(plan['cutscene_spots'][0]['parameters'], dict(npc='shopkeeper', alias='first_meeting'))
            self.assertEqual(len(plan['behavior_references'][0]['assets']), 1)
            self.assertEqual(world, before)

    def test_resolved_source_is_not_a_registered_destination(self):
        plan = build_bindings(*fixture())
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            emit_assets(plan, {})
        registered = dict(entities=[native_guid(int(v,16)) for v in ['123456789abcdef0','432187659abcdef0','5555666677778888']],
                          souls=['source-person'], items=['source-item'])
        self.assertFalse(registration_errors(plan, registered))
        emitted = E.fromstring(emit_assets(plan, registered))
        self.assertEqual(len(emitted.findall('SoulAsset')), 2)
        self.assertEqual(len(emitted.findall('TriggerAreaAsset')), 2)
        self.assertEqual(emitted.find('ItemAsset').get('ItemClassGuids'), 'source-item')
        static = native_links(plan, native_guid(123))
        self.assertEqual(len(static), 3)
        self.assertTrue(all(row[0] == native_guid(123) for row in static))

    def test_duplicate_alias_targets_are_preserved_and_block_implicit_selection(self):
        m, world, souls = fixture()
        world['links'].append(dict({'from': 'quest', 'to': 'spot'}, source_link={'Name': "QuestPlace['doorway']"}))
        plan = build_bindings(m, world, souls)
        self.assertEqual(len(plan['references']["t_places['doorway']"]), 2)
        self.assertTrue(any(r['kind'] == 'multiple_alias_targets' for r in plan['unresolved']))

    def test_dynamic_or_executable_link_syntax_is_rejected(self):
        for value in ["QuestPlace[__import__('os').system('x')]", "CutsceneSpot[alias('a'),alias('b')]", 'QuestPlace[$dynamic]']:
            with self.assertRaises(ValueError): parse_link_label(value)
        self.assertEqual(parse_link_label("CutsceneSpot[npc('someone'),idle(true)]")['named']['idle'], True)

    def test_missing_behavior_reference_and_item_filters_are_not_discarded(self):
        m, world, souls = fixture()
        m['tables']['quest_item']['rows'][0]['min_health'] = '0.8'
        world['links'] = [l for l in world['links'] if l['to'] != 'area']
        plan = build_bindings(m, world, souls)
        kinds = {r['kind'] for r in plan['unresolved']}
        self.assertTrue({'behavior_reference', 'asset_filter_adapter', 'tracked_asset_resolution'} <= kinds)


if __name__ == '__main__': unittest.main()
