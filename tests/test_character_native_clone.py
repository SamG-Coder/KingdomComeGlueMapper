import copy
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_native_clone import clone_world, clone_resources, identity
from campaign_entity_links import native_guid, guid_value
from character_person import resolve_person


class NativeCloneTests(unittest.TestCase):
    def test_conditional_source_actor_resolves_without_importing_source_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp); level = source/'Data/Levels/rataje'; level.mkdir(parents=True)
            with zipfile.ZipFile(level/'level.pak', 'w') as z:
                z.writestr('objects_mission0.xml', '<Entities/>')
                z.writestr('layers/friend.xml', '<Layer><Entities><Entity Name="friend" EntityClass="NPC" EntityGuid="1234"/></Entities></Layer>')
                z.writestr('whdata_0', '<WH><SoulList><Souls><Soul><Name>friend</Name><SharedSoulGuid>source-id</SharedSoulGuid></Soul></Souls></SoulList></WH>')
            tables = MagicMock(); tables.row.return_value = {'soul_id': 'source-id'}; tables.cache = {}
            with patch('character_person.RetailSourceReader'), patch('character_person.SourceTables', return_value=tables), patch('character_person.capture_person_ai') as capture:
                result = resolve_person(source, 'friend', include_ai=False)
            capture.assert_not_called()
            self.assertEqual(result['actor'].get('EntityGuid'), '1234')
            self.assertIn('layers/friend.xml', result['provenance']['actor_sources'])

    def test_owned_home_and_scheduler_are_independent_and_shared_links_retained(self):
        a, b, c = map(native_guid, (100, 200, 300))
        files = {
            'objects_mission0.xml': f'''<Entities><Entity Name="driver" EntityId="1" EntityGuid="{a}" Pos="10,20,30">
              <EntityLinks><Link Name="_!home" TargetId="2" TargetGuid="{b}"/>
              <Link Name="owner" TargetId="2" TargetGuid="{b}"/>
              <Link Name="town" TargetId="3" TargetGuid="{c}"/></EntityLinks><Properties native="kept" Enabled="1"/></Entity>
              <Entity Name="home" EntityId="2" EntityGuid="{b}" Pos="10,20,30"/>
              <Entity Name="town" EntityId="3" EntityGuid="{c}" Pos="50,50,50"/></Entities>'''.encode(),
            'whdata_0': f'<WH><SoulList><Souls><Soul><Name>driver</Name><Guid>old</Guid><SharedSoulGuid>shared</SharedSoulGuid><EntityGuid>{a}</EntityGuid></Soul></Souls></SoulList></WH>'.encode(),
            'tables/ai/scheduler.xml': b'<database><Schedulers><Scheduler EntityGuid="100"><Links><Link TargetGuid="200"/></Links></Scheduler><Hub EntityGuid="200"/></Schedulers></database>',
            'waitinglinks.xml': f'<StaticLinksInfo><WaitingLinks><WaitingLink SourceId="{a}" TargetId="{b}"/></WaitingLinks></StaticLinksInfo>'.encode(),
        }
        before = dict(files)
        result, report = clone_world(files, 'driver', 'friend', (7, 18, 32))
        self.assertEqual(files, before)
        mission = ET.fromstring(result['objects_mission0.xml'])
        friend = mission.find("Entity[@Name='friend']")
        home = mission.find("Entity[@Name='friend_home']")
        self.assertEqual(friend.get('Pos'), '7.0,18.0,32.0')
        self.assertEqual(friend.find('Properties').get('native'), 'kept')
        self.assertEqual(friend.find('Properties').get('Enabled'), '1')
        self.assertEqual(friend.find("EntityLinks/Link[@Name='_!home']").get('TargetGuid'), home.get('EntityGuid'))
        self.assertEqual(friend.find("EntityLinks/Link[@Name='town']").get('TargetId'), '3')
        rows = ET.fromstring(result['tables/ai/scheduler.xml']).find('Schedulers')
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2].get('EntityGuid'), str(guid_value(friend.get('EntityGuid'))))
        self.assertEqual(rows[2].find('Links/Link').get('TargetGuid'), str(guid_value(home.get('EntityGuid'))))
        self.assertEqual(report['copied_records']['waitinglinks.xml'], 1)
        souls = ET.fromstring(result['whdata_0']).find('SoulList/Souls')
        self.assertEqual(souls[0].findtext('Guid'), 'old')
        self.assertEqual(souls[1].findtext('Guid'), identity('friend'))
        with self.assertRaises(ValueError): clone_world(result, 'driver', 'friend', (7,18,32))

    def test_behavior_fields_preserved_and_dialogue_bound_to_separate_role(self):
        files = {
            'Libs/Tables/rpg/soul__test.xml': b'<database><souls version="2"><soul soul_name="driver" soul_id="old" brain_id="native-brain" factionName="native-town" social_class_id="6" soul_vip_class_id="0" skald_character_name="native-voice"/></souls></database>',
            'Libs/Tables/rpg/role__test.xml': b'<database><roles version="1"><role role_name="DRIVER" native="yes"/></roles></database>',
            'Libs/Storm/storm.xml': b'<storm><tasks><task name="roles"><source path="original.xml"/></task><task name="names"/><task name="appearance"/><task name="equipment"/></tasks></storm>',
            'Quests/driver.xml': b'<Database><Skald><FaderDialog Name="driver"><Dialogue NonSpeakerRoles="DRIVER"><SelectedSouls><Actor Role="DRIVER" Soul="driver"/></SelectedSouls></Dialogue></FaderDialog></Skald></Database>',
            'Quests/host.xml': b'<Database><Skald><Project><Definitions><Definition File="driver.xml"/></Definitions><Nodes><driver Name="driver"/><SceneFinishedWaiter Name="done"><Edge From="driver.travel" To="Enqueue"/></SceneFinishedWaiter></Nodes></Project></Skald></Database>',
        }
        before = dict(files)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp); (source/'Localization').mkdir()
            with zipfile.ZipFile(source/'Localization/English_xml.pak', 'w') as z:
                z.writestr('text_ui_soul.xml', '<Table><Row><Cell>name</Cell><Cell>Matthew</Cell><Cell>Matthew</Cell></Row></Table>')
            result, names = clone_resources(files, source, dict(name='source-friend', instance=ET.fromstring('<Soul><StaticData><NameStringId>name</NameStringId></StaticData></Soul>')),
                dict(archetype='0',appearance={'head':'imported-head'},inventory='imported-clothes'), 'friend',
                template_name='driver',template_role='DRIVER',template_dialogue='Quests/driver.xml',target_graph='Quests/host.xml')
        self.assertEqual(before, files)
        soul = ET.fromstring(result['Libs/Tables/rpg/soul__friend.xml']).find('souls/soul')
        original = ET.fromstring(files['Libs/Tables/rpg/soul__test.xml']).find('souls/soul')
        for key in ('brain_id','factionName','social_class_id','soul_vip_class_id','skald_character_name'):
            self.assertEqual(soul.get(key), original.get(key))
        self.assertEqual(soul.get('soul_id'), identity('friend'))
        dialog = ET.fromstring(result['Quests/friend.xml']).find('Skald/FaderDialog/Dialogue')
        self.assertEqual(dialog.get('NonSpeakerRoles'), 'FRIEND')
        self.assertEqual(dialog.find('SelectedSouls/Actor').get('Soul'), 'friend')
        host = ET.fromstring(result['Quests/host.xml'])
        self.assertEqual([e.get('From') for e in host.iter('Edge')], ['driver.travel','friend.travel'])
        self.assertIn(b'original.xml', result['Libs/Storm/storm.xml'])
        self.assertEqual(result['Quests/driver.xml'], files['Quests/driver.xml'])
        self.assertIn(b'Matthew', names)


if __name__ == '__main__': unittest.main()
