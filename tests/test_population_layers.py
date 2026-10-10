from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_population_layers import register_layers, layer_name
from character_population_conditional import plan_layers, split_layers
from campaign_profile_bridge import ProfileBridge
from campaign_quest_bridge import QuestBridge, UnsupportedOperation
from campaign_behavior import convert_behaviors
from test_campaign_behavior import source, catalog


LAYER = 'some_people{d1c7cb66-eed7-45df-bb7a-f8800c4395c4}'


class PopulationLayersTests(unittest.TestCase):
    def inputs(self):
        files = {'leveldata.xml': b'<LevelData><Layers><Layer Name="existing" Id="8"/></Layers></LevelData>',
                 'objects_mission0.xml': b'<Objects><Entity Name="visual_only" EntityClass="GeomEntity"/><Entity Name="existing" EntityId="50" EntityGuid="00000000-0000-0050"/></Objects>',
                 'whdata_1': b'<Root><GameProfileManager><GameProfiles><GameProfile><Name>existing</Name><Id>9</Id></GameProfile></GameProfiles></GameProfileManager></Root>'}
        level = E.fromstring(f'<LevelData><Layers><Layer Name="{LAYER}" Id="3" Specs="-1"/></Layers></LevelData>')
        profiles = E.fromstring(f'''<Root><GameProfileManager><GameProfiles>
          <GameProfile><Name>source_people</Name><Id>41</Id><HasNavigation>0</HasNavigation><GameLayers><GameLayer>{LAYER}</GameLayer></GameLayers></GameProfile>
          <GameProfile><Name>incomplete</Name><Id>63</Id><GameLayers><GameLayer>{LAYER}</GameLayer><GameLayer>missing</GameLayer></GameLayers></GameProfile>
          </GameProfiles><GameProfileGroups><Groups><Group><Name>source_group</Name><Id>4</Id><Profiles><Profile>41</Profile></Profiles></Group></Groups></GameProfileGroups></GameProfileManager></Root>''')
        people = {LAYER: b'<Objects><Entity Name="person" EntityId="51" EntityGuid="00000000-0000-0051"/></Objects>'}
        return files, level, profiles, people

    def test_native_layers_reserve_ids_and_remap_group_members(self):
        output, report = register_layers(*self.inputs())
        self.assertEqual(report['profiles'], {'source_people': 'kcd1_source_people'})
        self.assertEqual(report['profile_ids'], {'41': '10'})
        metadata = E.fromstring(output['whdata_1'])
        self.assertEqual(metadata.findtext('.//Group/Profiles/Profile'), '10')
        layer = E.fromstring(output['layers/' + layer_name(LAYER) + '.xml'])
        self.assertEqual(layer[0].get('Layer'), layer_name(LAYER))
        self.assertEqual(E.fromstring(output['extractedlayerentityids.xml'])[0].get('ID'), '51')
        self.assertEqual(report['pending_profiles'][0]['missing_layers'], ['missing'])
        self.assertNotIn(b'person', output['objects_mission0.xml'])

    def test_reject_collision_with_existing_mission_entity(self):
        args = list(self.inputs())
        args[3][LAYER] = args[3][LAYER].replace(b'EntityId="51"', b'EntityId="50"')
        with self.assertRaisesRegex(ValueError, 'collision'): register_layers(*args)

    def test_actor_only_layer_is_atomic_and_mixed_or_compiled_layer_is_not_partial(self):
        path = 'layers/' + LAYER + '.xml'
        documents = {path: b'<Objects><Entity EntityClass="NPC" EntityGuid="0000000000000051"/></Objects>'}
        actor = dict(source_guid='0000000000000051', resident=False, container=path, status='conditional_converted')
        plan, pending = plan_layers(documents, [actor])
        self.assertEqual(plan[LAYER], ['0000000000000051'])
        self.assertEqual(pending, [])
        self.assertFalse(plan_layers(documents, [dict(actor, status='appearance_converted')])[0])
        self.assertFalse(plan_layers(documents, [actor], {LAYER.lower(): 1})[0])
        documents[path] = documents[path].replace(b'</Objects>', b'<Entity EntityClass="Bed"/></Objects>')
        plan, pending = plan_layers(documents, [actor])
        self.assertEqual(plan, {})
        self.assertEqual(pending[0]['world_adapters'], ['Bed'])

    def test_actor_and_private_helpers_stream_together_shared_home_does_not(self):
        files, level, profiles, _ = self.inputs()
        files['objects_mission0.xml'] = b'''<Objects>
          <Entity Name="person" EntityId="51" EntityGuid="00000051-0000-0000"/>
          <Entity Name="gm_person_idle_person" EntityId="52" EntityGuid="00000052-0000-0000"/>
          <Entity Name="gm_person_home_person" EntityId="53" EntityGuid="00000053-0000-0000"/>
          <Entity Name="shared_home_area" EntityId="54" EntityGuid="00000054-0000-0000"/>
          </Objects>'''
        person = dict(name='person', actor=E.fromstring('<Entity EntityGuid="0000000000000051"/>'))
        result, receipt = split_layers(files, {LAYER: ['0000000000000051']}, [person], level, profiles)
        mission = E.fromstring(result['objects_mission0.xml'])
        self.assertEqual([e.get('Name') for e in mission], ['shared_home_area'])
        layer = E.fromstring(result['layers/' + layer_name(LAYER) + '.xml'])
        self.assertEqual(len(layer), 3)
        self.assertEqual(receipt['conditional_actors'], 1)
        self.assertEqual(E.fromstring(result['extractedlayerentityids.xml']).tag, 'ReservedEntityIDsFromLayers')

    def test_navigation_profile_requires_real_navigation_conversion(self):
        args = self.inputs()
        args[2].find('.//HasNavigation').text = '1'
        output, report = register_layers(*args)
        self.assertEqual(report['profiles'], {})
        self.assertEqual(len(E.fromstring(output['whdata_1']).findall('.//GameProfile')), 1)

    def test_profile_switch_uses_persistent_native_layer_ownership(self):
        bridge = ProfileBridge('Imported', {'old':'kcd1_old', 'new':'kcd1_new'})
        node = bridge.lower(dict(attributes={'profileEnable':'"new"', 'profileDisable':'"old"'}, children=[]))
        self.assertEqual(len(node), 2)
        files = bridge.attach(QuestBridge([], 'Imported').emit()[0])
        root = E.fromstring(files['Quests/Imported_quest_bridge.xml'])
        self.assertEqual({a.get('AssetProfiles') for a in root.iter('ProfileAsset')}, {'kcd1_old','kcd1_new'})
        self.assertEqual(len(root.findall('.//Layer')), 2)
        self.assertEqual(len(root.findall('.//State')), 2)
        self.assertEqual(len(root.findall('.//Edge[@To="SetFalse"]')), 1)
        self.assertEqual(len(root.findall('.//Edge[@To="SetTrue"]')), 1)
        self.assertFalse(root.findall('.//ExecuteLua'))

    def test_failed_profile_switch_does_not_emit_half_switch(self):
        bridge = ProfileBridge('Imported', {'old':'kcd1_old'})
        with self.assertRaises(UnsupportedOperation):
            bridge.lower(dict(attributes={'profileEnable':'"old"', 'profileDisable':'"missing"'}, children=[]))
        self.assertEqual(bridge.actions, {})

    def test_profile_conversion_stays_inside_original_behavior_branch(self):
        model = source('<Root><Behavior><IfCondition failOnCondition="&quot;false&quot;" condition="&quot;$hasStarted&quot;"><EnableProfile profileEnable="&quot;source_people&quot;" profileDisable="&quot;&quot;"/></IfCondition></Behavior></Root>')
        files, bridge_files, report = convert_behaviors([model], 'Imported', catalog(),
                                                       registered={'profiles': {'source_people':'kcd1_source_people'}})
        self.assertEqual(report['whole_trees_emitted'], 1)
        tree = E.fromstring(next(iter(files.values())))
        self.assertEqual(len(tree.find('.//IfCondition/Sequence')), 1)
        self.assertEqual(len(E.fromstring(bridge_files['Quests/Imported_quest_bridge.xml']).findall('.//Layer')), 1)

    def test_profile_loaded_gate_uses_the_same_registered_native_profile(self):
        model = source('<Root><Behavior><ProfileLoadedGate LayerName="&quot;source_people&quot;" NegateTo="&quot;false&quot;" RunLogic="&quot;KeepRunning&quot;"><Success/></ProfileLoadedGate></Behavior></Root>')
        files, _, report = convert_behaviors([model], 'Imported', catalog(),
                                             registered={'profiles': {'source_people':'kcd1_source_people'}})
        self.assertEqual(report['whole_trees_emitted'], 1)
        gate = E.fromstring(next(iter(files.values()))).find('.//ProfileLoadedGate')
        self.assertEqual(gate.get('LayerName'), "'kcd1_source_people'")
        self.assertEqual(gate.get('RunLogic'), 'KeepRunning')
        files, _, report = convert_behaviors([model], 'Imported', catalog())
        self.assertEqual(report['whole_trees_emitted'], 0)
        self.assertEqual(files, {})


if __name__ == '__main__': unittest.main()
