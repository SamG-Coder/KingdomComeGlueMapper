import sys
from pathlib import Path
import unittest
import tempfile
import zipfile
from unittest.mock import patch
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_person_ai import (capture_person_ai, native_brain_and_class,
                                stat_operations, apply_person_properties, register_factions, FACTION_PATH,
                                register_guard_selector)
from character_person import effective_soul
from character_world_ai import register_interrupt_services
from character_person_world import register_person_world, source_home_geometry
from campaign_trigger_areas import write_areas, read_areas
from campaign_entity_links import native_guid, guid_value


class PersonAITests(unittest.TestCase):
    def test_instance_only_souls_have_stable_distinct_identity_and_preserve_overrides(self):
        instance = E.fromstring('<Soul><Guid>one</Guid><Name>guard</Name><StaticData><FactionId>46</FactionId>'
          '<InitialSoulStats><Strength>15</Strength></InitialSoulStats><InitialAIData><BrainId>brain</BrainId>'
          '<Activities><Activity><Time>24:14</Time><Activity>sleep</Activity></Activity></Activities>'
          '</InitialAIData></StaticData></Soul>')
        first = effective_soul(None, instance)
        self.assertEqual(first, effective_soul(None, instance))
        self.assertEqual(first['time_0'], '24:14')
        self.assertEqual(first['str'], '15')
        instance.find('Guid').text = 'two'
        self.assertNotEqual(first['soul_id'], effective_soul(None, instance)['soul_id'])
        definition = {'soul_id':'shared','faction':'other','str':'1'}
        self.assertEqual(effective_soul(definition, instance)['faction'], '46')
        self.assertEqual(definition['faction'], 'other')

    def test_guard_selector_adds_region_without_bypassing_crime_class_gate(self):
        blob = b'<storm><customSelectors><customSelector name="isGuard" mode="and"><or>'
        blob += b'<hasSocialClass name="guard"/></or><or><hasFaction name="trosecko_settlements"/>'
        blob += b'</or></customSelector><customSelector name="unrelated"/></customSelectors></storm>'
        result = register_guard_selector(blob)
        self.assertEqual(register_guard_selector(result), result)
        tree = E.fromstring(result)
        selector = tree.find(".//customSelector[@name='isGuard']")
        self.assertEqual(selector.get('mode'), 'and')
        self.assertEqual(len(selector.findall('.//hasSocialClass')), 1)
        self.assertEqual(len(selector.findall('.//hasFaction')), 2)
        self.assertIsNotNone(tree.find(".//customSelector[@name='unrelated']"))

    def test_core_stats_preserve_values_and_do_not_guess_incompatible_combat_scale(self):
        ops = stat_operations(dict(str='8', hearing='4', combat_level='20', inventory_id='legacy'))
        self.assertEqual(ops, [('setAttribute', {'stat': 'strength', 'value': '8'}),
                               ('setAttribute', {'stat': 'hearing', 'value': '4'})])
        for value in ('nan', 'inf', '-1'):
            with self.assertRaises(ValueError): stat_operations({'vision': value})

    def test_source_perception_flags_do_not_copy_old_model_or_idle_quest_gate(self):
        source = E.fromstring('<Entity><Properties bWH_PerceptorObject="1" fileModel="old.cdf">'
                              '<Script bIdleUntilFirstPatch="1"/></Properties></Entity>')
        for cls in ('NPC', 'NPC_Female'):
            target = E.Element('Entity', EntityClass=cls)
            apply_person_properties(target, source)
            self.assertEqual(target.find('Properties').attrib, {'bWH_PerceptorObject': '1'})
            self.assertIsNone(target.find('Properties/Script'))

    def test_brain_and_social_class_resolve_by_semantics_not_old_integer_ids(self):
        ai = {'source_brain': {'brain_name': 'npc_daycycle'},
              'source_social_class': {'social_class_name': 'villager'}}
        brains = E.fromstring('<x><brain brain_name="npc_basic" brain_id="new"/></x>')
        classes = E.fromstring('<x><social_class social_class_name="villager" social_class_id="99"/></x>')
        self.assertEqual(native_brain_and_class(ai, brains, classes), ('new', '99'))
        ai['source_brain']['brain_name'] = 'quest_brain'
        with self.assertRaisesRegex(ValueError, 'No native person brain adapter'):
            native_brain_and_class(ai, brains, classes)

    def test_shared_faction_location_merges_and_preserves_native_civilians(self):
        native = E.fromstring('<database><FactionTree version="1"><Faction Name="civilians"><Children>'
                              '<Faction Name="trosecko"/><Faction Name="kutnohorsko"/></Children>'
                              '</Faction></FactionTree></database>')
        files = {}
        for index in ('10', '20', '10'):
            leaf = dict(faction_id=index, faction_name='family', player_reputation='1', location_id='town')
            self.assertEqual(register_factions(files, {'source_factions': [leaf],
                'source_superfaction': {'superfaction_name': 'Civilians'}}, native),
                             'gluemapper_kcd1_faction_' + index)
        tree = E.fromstring(files[FACTION_PATH])
        self.assertEqual(len(list(tree.iter('Faction'))), 7)
        self.assertEqual(len(list(tree.iter('Relation'))), 2)
        for name in ('trosecko', 'kutnohorsko'):
            self.assertIsNotNone(tree.find(".//Faction[@Name='" + name + "']"))
        self.assertEqual(tree.find(".//Faction[@Name='gluemapper_kcd1']").get('LevelId'), '1001')
        self.assertEqual(len(list(native.iter('Faction'))), 3)

    def test_guard_authority_is_not_lost_when_class_name_exists_in_both_games(self):
        ai = {'source_brain': {'brain_name': 'npc_daycycle'},
              'source_social_class': {'social_class_name': 'soldier', 'soul_crime_role_id': '2'}}
        brains = E.fromstring('<x><brain brain_name="npc_basic" brain_id="new"/></x>')
        classes = E.fromstring('<x><social_class social_class_name="soldier" social_class_id="33"/>'
                              '<social_class social_class_name="soldier_crimeAuthority" social_class_id="108"/></x>')
        self.assertEqual(native_brain_and_class(ai, brains, classes), ('new', '108'))

    def test_superfaction_is_a_different_table_even_when_a_faction_shares_its_id(self):
        rows = {'ai/brain': {'brain_name': 'npc_daycycle'},
                'rpg/social_class': {'social_class_name': 'soldier'},
                'rpg/faction': {'faction_id': '46', 'superfaction_id': '6'},
                'rpg/superfaction': {'superfaction_id': '6', 'superfaction_name': 'Soldiers'}}
        class Tables:
            def row(self, table, field, value):
                if table == 'rpg/faction': self_f.assertEqual(value, '46')
                return rows[table]
        self_f = self
        ai = capture_person_ai(Tables(), dict(brain_id='brain', social_class_id='33', faction='46'),
                               E.Element('Entity'), E.Element('Soul'), [])
        self.assertEqual(ai['source_factions'], [rows['rpg/faction']])
        self.assertEqual(ai['source_superfaction']['superfaction_name'], 'Soldiers')

    def test_world_interrupt_links_resolve_and_never_copy_native_parking_coordinates(self):
        land_src = E.fromstring('<Entity><EntityLinks><Link Name="mrkev" TargetId="1"/>'
                                '<Link Name="redkev" TargetId="2"/></EntityLinks></Entity>')
        source = [E.fromstring('<Entity Name="human" EntityId="1"><Properties guidSmartObjectType="human-type"/>'
                   '<EntityLinks><Link Name="horseParkingSpot" TargetId="999"/></EntityLinks></Entity>'),
                  E.fromstring('<Entity Name="animal" EntityId="2"><Properties guidSmartObjectType="animal-type"/></Entity>')]
        mission = E.Element('Objects');land = E.SubElement(mission, 'Entity', EntityGuid='land', Pos='0,0,-50')
        def place(name, cls):
            return E.SubElement(mission, 'Entity', Name=name, EntityClass=cls,
                                EntityId=str(len(mission)), EntityGuid=name)
        waiting = []
        report = register_interrupt_services(source, land_src, land, place, waiting)
        self.assertEqual({x[2] for x in waiting}, {'mrkev', 'redkev'})
        self.assertEqual(len(report['spatial_dependencies_pending']), 1)
        self.assertNotIn(b'999', E.tostring(mission))
        self.assertTrue(all(e.get('Pos') == '0,0,-50' for e in mission))
        self.assertEqual([e.get('EntityClass') for e in list(mission)[1:]], ['SmartObjectHolder'] * 2)

    def test_home_xml_geometry_is_transformed_once_and_rejects_tilted_volumes(self):
        home = E.fromstring('<Entity EntityGuid="10" Pos="10,20,30" Scale="2,2,1">'
                            '<Area Height="8"><Points><Point Pos="0,0,0"/>'
                            '<Point Pos="2,0,0"/><Point Pos="0,2,0"/></Points></Area></Entity>')
        result = source_home_geometry(home)
        self.assertEqual(result.guid, 16)
        self.assertEqual(result.points, ((10,20,30),(14,20,30),(10,24,30)))
        home.set('Rotate', '0.70710678,0.70710678,0,0')
        with self.assertRaisesRegex(ValueError, 'Tilted'):
            source_home_geometry(home)

    def test_home_registration_keeps_existing_shop_activity_and_idle_is_interruptible(self):
        land, first, second = [native_guid(i) for i in (1,2,3)]
        files = {'objects_mission0.xml': f'<Objects><Entity Name="sa_land" EntityId="1" EntityGuid="{land}"/>'
                 f'<Entity Name="one" EntityId="2" EntityGuid="{first}" Pos="100,200,30"/><Entity Name="two" EntityId="3" EntityGuid="{second}"/></Objects>'.encode(),
                 'tables/ai/scheduler.xml': b'<database><Schedulers version="1"><C_SmartHub EntityGuid="2"/>'
                    b'<C_SmartHub EntityGuid="3"><Links><S_ActivityLink TargetGuid="4"><Parameters BehaviorName="use"/></S_ActivityLink>'
                    b'</Links></C_SmartHub><C_SmartHub EntityGuid="4"/></Schedulers></database>',
                 'triggerareas.fubar': write_areas([],123)}
        home = '<Entity Name="source_house" EntityGuid="10" Pos="10,20,30"><Area Height="8"><Points>'
        home += '<Point Pos="0,0,0"/><Point Pos="2,0,0"/><Point Pos="0,2,0"/></Points></Area></Entity>'
        people = [dict(name=n, source_level='world', ai=dict(links=[dict(label='Home,owner',source_xml=home)],schedule=[]))
                  for n in ('one','two')]
        people[0]['ai']['links'].append(dict(label="Home,Work[('Herbalist')]", source_xml=home))
        activity = E.fromstring('<S_ActivityLink PositioningDelegate="0"><Parameters BehaviorName="schedulerWait" '
                               'Priority="0" BehaviorOverride="false"/></S_ActivityLink>')
        terminal = E.Element('C_SmartHub')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'Data/Levels/world';path.mkdir(parents=True)
            with zipfile.ZipFile(path/'level.pak','w') as z:z.writestr('triggerareas.fubar',write_areas([],123))
            with patch('character_person_world.native_wait_activity',return_value=(activity,terminal)):
                result, report = register_person_world(files, people, directory, None)
        root=E.fromstring(result['tables/ai/scheduler.xml'])
        self.assertEqual(root.find(".//C_SmartHub[@EntityGuid='3']/Links/S_ActivityLink/Parameters").get('BehaviorName'),'use')
        p=root.find(".//C_SmartHub[@EntityGuid='2']/Links/S_ActivityLink/Parameters")
        self.assertEqual(p.get('BehaviorOverride'),'false')
        mission=E.fromstring(result['objects_mission0.xml'])
        anchor=mission.find("Entity[@Name='gm_person_idle_one']")
        self.assertEqual(anchor.get('Pos'),'100,200,30')
        self.assertEqual(anchor.get('EntityClass'),'TagPoint')
        self.assertEqual(anchor.find('EntityLinks/Link').get('TargetGuid'),land)
        self.assertEqual(anchor.find('EntityLinks/Link').get('Name'),'^delegate')
        link=root.find(".//C_SmartHub[@EntityGuid='2']/Links/S_ActivityLink")
        self.assertEqual(link.get('PositioningDelegate'),str(guid_value(anchor.get('EntityGuid'))))
        self.assertEqual(link.get('TargetGuid'),'1')
        self.assertEqual(mission.find("Entity[@Name='one']/EntityLinks/Link[@Name='_,schedulerWait']").get('TargetGuid'),anchor.get('EntityGuid'))
        self.assertEqual(len(read_areas(result['triggerareas.fubar'])['areas']),1)
        self.assertEqual(len(E.fromstring(result['objects_mission0.xml']).findall("Entity[@EntityClass='TriggerArea']")),1)
        self.assertEqual(report[1]['baseline'],'existing authored activity preserved')
        self.assertNotIn(b'schedulerWait',files['tables/ai/scheduler.xml'])


if __name__ == '__main__': unittest.main()
