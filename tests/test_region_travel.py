import sys
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from region_travel import switch_table, travel_graph, station, prefab_geometry, preprocess
from campaign_entity_links import guid_value
from region_travel_dialogue import patch, merge_localization, HOST, DIALOGUE, PORT
from region_travel_ground import fit_cart
from region_travel import qmul, rotate
from region_travel_ui import import_showroom
import region_travel_driver as driver
from region_travel_companions import import_horse_scheduler, import_horse_scheduler_table
from region_travel_navigation import validate_mesh
import struct


class RegionTravelTests(unittest.TestCase):
    def test_compiled_horse_scheduler_closure_and_existing_records(self):
        mission=b'<Objects><Entity Name="playerHorseProxy" EntityGuid="835d5a78-de68-48f5"/><Entity Name="playerHorseDefaultSmartObject" EntityGuid="54932c15-1938-4949"/></Objects>'
        proxy=str(guid_value('835d5a78-de68-48f5'));default=str(guid_value('54932c15-1938-4949'))
        native=f'<database><Schedulers version="1"><C_SmartHub EntityGuid="{proxy}"><Links><S_ActivityLink TargetGuid="{default}" PositioningDelegate="0"><Parameters BehaviorName="use"/></S_ActivityLink></Links></C_SmartHub><C_SmartHub EntityGuid="{default}"/><C_SmartHub EntityGuid="999"/></Schedulers></database>'.encode()
        destination=b'<database><Schedulers version="1"><C_SmartHub EntityGuid="123"/></Schedulers></database>'
        blob,report=import_horse_scheduler_table(destination,native,mission)
        self.assertEqual({e.get('EntityGuid') for e in ET.fromstring(blob)[0]},{'123',proxy,default})
        self.assertEqual(report['compiled_scheduler_records'],2)
        with self.assertRaises(ValueError):import_horse_scheduler_table(blob,native,mission)
        broken=native.replace(('TargetGuid="'+default+'"').encode(),b'TargetGuid="999"')
        with self.assertRaises(ValueError):import_horse_scheduler_table(destination,broken,mission)

    def test_navigation_rejects_wrong_version_offsets_and_truncation(self):
        settings=bytes(56)
        tile=struct.pack('<4I',0x444e4156,14,12,34)
        mesh=settings+struct.pack('<I',1)+tile
        header=struct.pack('<5I',12,34,60,1,len(tile))
        self.assertEqual(validate_mesh(header,mesh,settings,14),1)
        for h,m,v in [(header,mesh,15),(header,mesh[:-1],14),
                      (struct.pack('<5I',12,34,61,1,len(tile)),mesh,14),
                      (header[:-1],mesh,14),(header,mesh+b'junk',14)]:
            with self.assertRaises(ValueError):validate_mesh(h,m,settings,v)

    def test_horse_scheduler_preserves_native_binding_without_new_horse(self):
        native=b'''<Objects><Entity Name="playerHorseProxy" EntityId="1" EntityGuid="proxy" EntityClass="TagPoint"><EntityLinks><Link Name="_use" TargetId="2" TargetGuid="default"/></EntityLinks></Entity><Entity Name="playerHorseDefaultSmartObject" EntityId="2" EntityGuid="default" EntityClass="SmartObjectHolder"><Properties guidSmartObjectType="native-type"/></Entity></Objects>'''
        dest=b'<Objects><Entity Name="existing" EntityId="1" EntityGuid="existing"/></Objects>'
        marker=ET.fromstring('<Entity Pos="10,20,999"/>')
        blob,report=import_horse_scheduler(dest,native,marker,lambda x,y:15)
        root=ET.fromstring(blob);proxy=root[1];target=root[2]
        self.assertEqual(proxy.find('EntityLinks/Link').get('TargetId'),target.get('EntityId'))
        self.assertEqual(proxy.find('EntityLinks/Link').get('TargetGuid'),target.get('EntityGuid'))
        self.assertEqual(proxy.get('Pos'),'10.0,20.0,15')
        self.assertEqual(target.find('Properties').get('guidSmartObjectType'),'native-type')
        self.assertFalse(report['creates_horse'])
        self.assertFalse(any(e.get('EntityClass')=='Horse' for e in root))
        with self.assertRaises(ValueError):import_horse_scheduler(blob,native,marker,lambda x,y:15)

    def test_cart_fits_all_wheels_as_one_rigid_object(self):
        contacts=[(-.75,-1.8,.5),(.75,-1.8,.5),(-.75,.5,.5),(.75,.5,.5)]
        ground=lambda x,y:12+.06*x-.04*y
        p,q,errors=fit_cart((100,200,80),.7,contacts,ground,qmul,rotate)
        self.assertLess(max(map(abs,errors)),1e-5)
        points=[tuple(a+b for a,b in zip(p,rotate(q,c))) for c in contacts]
        import math
        self.assertAlmostEqual(math.dist(points[0],points[3]),math.dist(contacts[0],contacts[3]))
        with self.assertRaises(ValueError):
            fit_cart((0,0,0),0,contacts,lambda x,y:2*x,qmul,rotate)

    def test_showroom_remaps_colliding_ids_and_parent_links(self):
        native=ET.Element('Objects')
        classes=['UIApseLinkNode','UIShopLinkNode','InventoryDummyPlayer','InventoryDummyHorse','InventoryDummyDog']
        for i,cls in enumerate(classes,1):
            e=ET.SubElement(native,'Entity',EntityId=str(i),EntityGuid='native'+str(i),EntityClass=cls,EditorLayer='showroom')
            if i>1:e.set('ParentId','1')
        ET.SubElement(ET.SubElement(native[0],'EntityLinks'),'Link',TargetId='3',TargetGuid='native3')
        dest=b'<Objects><Entity EntityId="3" EntityGuid="existing"/></Objects>'
        merged,report=import_showroom(dest,ET.tostring(native));root=ET.fromstring(merged)
        self.assertEqual(len({e.get('EntityId') for e in root}),6)
        self.assertEqual(root[1].find('EntityLinks/Link').get('TargetId'),root[3].get('EntityId'))
        self.assertEqual(root[2].get('ParentId'),root[1].get('EntityId'))
        self.assertEqual(report['native_entities'],5)
        native[0].find('EntityLinks/Link').set('TargetId','999')
        with self.assertRaises(ValueError):import_showroom(dest,ET.tostring(native))

    def test_return_driver_has_independent_soul_and_role(self):
        souls=b'<database><souls version="2"><soul soul_name="tsla_nomad" soul_id="original" brain_id="brain"/></souls></database>'
        storm=b'<?xml version="1.0"?><!DOCTYPE storm SYSTEM "storm.dtd"><storm><rules><!--native--><rule name="original"/></rules></storm>'
        dialogue=b'<Database><Skald><FaderDialog><Dialogue><SelectedSouls><SelectedSoul Role="HENRY"/><SelectedSoul Role="PREVOZNIK_TROSECKO" Soul="tsla_nomad"/></SelectedSouls></Dialogue></FaderDialog></Skald></Database>'
        role_table=b'<database><roles version="1"><role role_name="PREVOZNIK_TROSECKO" metarole_name="NPC" _role_string_name="native_label"/></roles></database>'
        files,_=driver.resources(souls,storm,dialogue,role_table)
        soul=ET.fromstring(files['Libs/Tables/rpg/soul__gluemappertravel.xml']).find('souls/soul')
        self.assertNotEqual(soul.get('soul_id'),'original')
        self.assertEqual(soul.get('brain_id'),'brain')
        roles=ET.fromstring(files['Libs/Storm/roles/world/levelSwitch.xml'])
        self.assertIsNotNone(roles.find("rules/rule[@name='original']"))
        d=ET.fromstring(files[driver.GRAPH]);actor=d.findall('.//SelectedSoul')[1]
        body=d.find('.//Dialogue')
        self.assertEqual(body.get('Initiator'),'Player')
        self.assertEqual(body.get('NonSpeakerRoles'),driver.ROLE)
        self.assertEqual(actor.get('Soul'),soul.get('soul_name'))
        self.assertEqual(actor.get('Role'),roles.find('.//addRole').get('name'))
        registered=ET.fromstring(files['Libs/Tables/rpg/role__gluemappertravel.xml']).find('roles/role')
        self.assertEqual(registered.get('role_name'),actor.get('Role'))
        self.assertEqual(registered.get('metarole_name'),'NPC')
        self.assertEqual(registered.get('_role_string_name'),'native_label')
        output=files['Libs/Storm/roles/world/levelSwitch.xml']
        self.assertTrue(output.startswith(storm[:storm.index(b'</rules>')]))
        self.assertTrue(output.endswith(storm[storm.index(b'</rules>'):]))
        with self.assertRaises(ValueError):driver.resources(souls,storm,dialogue,b'<database><roles/></database>')

    def test_localization_retains_all_original_rows_and_bytes(self):
        original=b'<Table>\r\n<Row><Cell>ui_prechod_z_seq1_Gr38</Cell><Cell>Buy transport</Cell><Cell>(Buy transport to the Kuttenberg region)</Cell></Row>\r\n<Row><Cell>other</Cell><Cell>A &amp; B</Cell><Cell>Unrelated dialogue</Cell></Row>\r\n</Table>'
        additions=b'<Table><Row><Cell>new</Cell><Cell>Travel</Cell><Cell>Travel to KDC1</Cell></Row></Table>'
        merged=merge_localization(original,additions)
        self.assertTrue(merged.startswith(original[:original.index(b'</Table>')]))
        before=ET.fromstring(original);after=ET.fromstring(merged)
        self.assertEqual([ET.tostring(r) for r in before],[ET.tostring(r) for r in list(after)[:len(before)]])
        self.assertEqual(len(after),len(before)+1)
        with self.assertRaises(ValueError):merge_localization(merged,additions)

    def test_driver_choice_ends_dialogue_before_switching(self):
        dialogue=b'''<Database><Skald><FaderDialog><Ports/><Dialogue><Decision><Sequences>
        <Sequence Name="original"><UiPrompt StringName="ui_prechod_z_seq1_Gr38"/></Sequence>
        <Sequence Name="exit" EndType="EndDialogue"/>
        </Sequences></Decision></Dialogue></FaderDialog></Skald></Database>'''
        host=b'<Database><Skald><Gameplay><Nodes><State Name="original"/></Nodes></Gameplay></Skald></Database>'
        files,strings=patch(dialogue,host)
        root=ET.fromstring(files[DIALOGUE])
        choices=root.findall('.//Sequences/Sequence')
        self.assertEqual([s.get('Name') for s in choices],['original',PORT,'exit'])
        self.assertEqual(choices[1].get('EndType'),'EndDialogue')
        self.assertIn("!Port('npc_videlo_crime')",choices[1].get('EntryCondition'))
        nodes=ET.fromstring(files[HOST]).find('./Skald/Gameplay/Nodes')
        prepare=nodes.find("Function[@Name='gluemapper_prepare_travel']")
        self.assertEqual(prepare.get('MethodName'),'wh::conceptmodule::PassLongTime')
        self.assertEqual(prepare.find('Edge').get('From'),'gluemapper_wait_for_dialogue.OnFinished')
        switch=nodes.find("Function[@Name='gluemapper_switch_to_kcd1']")
        self.assertEqual(switch.find('Edge').get('From'),'gluemapper_prepare_travel.OnExec')
        self.assertEqual(nodes.find('SceneFinishedWaiter/Edge').get('From'),'prechod_z_trosecka_na_kutnohorsko.'+PORT)
        self.assertEqual(ET.fromstring(strings).findall('Row/Cell')[2].text,'(Buy transport to the Rattay region)')
        with self.assertRaises(ValueError):patch(files[DIALOGUE],files[HOST])

    def test_routes_preserve_native_rows_and_do_not_use_new_game(self):
        source=b'<database><LevelSwitches version="1"><LevelSwitchData Name="trosecko" TargetLevelId="2"/></LevelSwitches></database>'
        rows=ET.fromstring(switch_table(source)).find('LevelSwitches')
        self.assertEqual([r.get('TargetLevelId') for r in rows],['1001','2'])
        self.assertEqual(len(rows),2)
        for r in rows:
            self.assertTrue(r.get('TargetLocationEntity').startswith('gmtravel_arrival_'))
            self.assertIsNone(r.get('Video'))
        graph=ET.fromstring(travel_graph('Travel','to_kcd1'))
        switch=graph.find("./Skald/Project/Nodes/Function[@Name='travel']")
        self.assertEqual(switch.get('MethodName'),'wh::game::SwitchLevel')
        self.assertEqual(switch.find('Edge').get('From'),'prepare_travel.OnExec')
        prepare=graph.find("./Skald/Project/Nodes/Function[@Name='prepare_travel']")
        self.assertIsNone(prepare.find('Edge'))
        self.assertIsNone(graph.find('.//InteractionTriggerNode'))
        self.assertIsNone(graph.find('.//GameStart'))

    def test_station_preserves_existing_entities_and_binds_native_guids(self):
        marker=ET.fromstring('<Entity Pos="100,200,50" Rotate="1,0,0,0"/>')
        prefab=b'<Prefab><Objects><Object Id="body" Name="body" EntityClass="GeomEntity" Geometry="body.cgf" Pos="0,0,1"/><Object Id="wheel" Parent="body" Name="wheel" EntityClass="GeomEntity" Geometry="wheel.cgf" Pos="1,2,3"/></Objects></Prefab>'
        source=b'<Objects><Entity Name="keep" EntityId="9000" EntityClass="NPC"><Properties value="unchanged"/></Entity></Objects>'
        result,graph,blob,links=station(source,marker,'kcd1',prefab)
        root=ET.fromstring(result)
        self.assertEqual(ET.tostring(root[0]),ET.tostring(ET.fromstring(source)[0]))
        self.assertEqual(len({e.get('EntityId') for e in root}),len(root))
        for e in list(root)[1:]:self.assertGreater(guid_value(e.get('EntityGuid')),0)
        self.assertEqual(len(links),0)
        trigger=root.find("Entity[@EntityClass='InteractionTrigger']")
        self.assertIsNone(trigger)
        dialogue=ET.fromstring(blob)
        self.assertIsNotNone(dialogue.find('.//return_driver'))
        self.assertEqual(dialogue.find('.//SceneFinishedWaiter/Edge').get('From'),'return_driver.travel')
        self.assertEqual(dialogue.find(".//Function[@Name='prepare_travel']/Edge").get('From'),'return_dialogue_finished.OnFinished')
        wheel=root.find("Entity[@Name='gmtravel_kcd1_wheel']")
        self.assertEqual(list(map(float,wheel.get('Pos').split(','))),[106,202,54])
        with self.assertRaises(ValueError):station(result,marker,'kcd1',prefab)

    def test_preserve_existing_concept_paths_and_player(self):
        original=b'<Root version="1"><SoulList><Souls><Soul><Player>1</Player></Soul></Souls></SoulList><ConceptManager><IncludeModGraphs>1</IncludeModGraphs><ConceptPaths><Path>original.xml</Path></ConceptPaths></ConceptManager></Root>'
        result=ET.fromstring(preprocess(original,'Quests/Travel.xml','trosecko'))
        self.assertEqual(result.findtext('ConceptManager/IncludeModGraphs'),'1')
        self.assertEqual([p.text for p in result.findall('ConceptManager/ConceptPaths/Path')],['original.xml','quests/travel.xml'])
        self.assertEqual(result.findtext('SoulList/Souls/Soul/Player'),'1')

    def test_reject_cyclic_prefab(self):
        with self.assertRaises(ValueError):
            prefab_geometry(b'<Prefab><Objects><Object Id="a" Parent="a" EntityClass="GeomEntity" Geometry="a.cgf"/></Objects></Prefab>')


if __name__=='__main__':unittest.main()
