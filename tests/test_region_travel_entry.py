from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from region_travel import guid, travel_graph
from region_travel_entry import register_entry, STREAMING_SOURCE
from campaign_entity_links import guid_value
from campaign_trigger_areas import read_areas, write_areas


def archive(files):
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temporary:
        path = Path(temporary.name)
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return zipfile.ZipFile(path)


def close_archive(archive):
    path = Path(archive.filename)
    archive.close()
    path.unlink()


class EntryTests(unittest.TestCase):
    def fixture(self):
        proxy, default = '00000001-0000-0001', '00000002-0000-0001'
        native = archive({
            'mission_mission0.xml': b'<Mission><Environment><EnvState UseLayersActivation="1"/></Environment></Mission>',
            'objects_mission0.xml': f'''<Objects>
            <Entity Name="playerProxy" EntityId="1" EntityGuid="{proxy}" EntityClass="TagPoint"><EntityLinks>
              <Link TargetId="2" Name="_,playerWait"/><Link TargetId="2" Name="_,60,!instant,animationAction"/>
              <Link TargetId="999" Name="_,quest_action"/>
            </EntityLinks></Entity>
            <Entity Name="so_player_scheduler" EntityId="2" EntityGuid="{default}" EntityClass="SmartObjectHolder"><Properties guidSmartObjectType="native-player"/></Entity>
            <Entity Name="sa_land" EntityId="3" EntityGuid="00000003-0000-0001" EntityClass="SmartAreaShape"><Properties guidSmartAreaTemplate="native-land"/><EntityLinks><Link TargetId="999" Name="punishment"/><Link TargetId="4" Name="mrkev"/><Link TargetId="5" Name="redkev"/></EntityLinks></Entity>
            <Entity Name="human_interrupt" EntityId="4" EntityGuid="00000004-0000-0001" EntityClass="GeomEntity"><Properties guidSmartObjectType="human"/></Entity>
            <Entity Name="animal_interrupt" EntityId="5" EntityGuid="00000005-0000-0001" EntityClass="SmartObjectHolder"><Properties guidSmartObjectType="animal"/></Entity>
            </Objects>''',
            'tables/ai/scheduler.xml': f'''<database><Schedulers version="1">
              <C_SmartHub EntityGuid="{guid_value(proxy)}"><Links>
                <S_ActivityLink TargetGuid="{guid_value(default)}"><Parameters BehaviorName="playerWait"/></S_ActivityLink>
                <S_ActivityLink TargetGuid="{guid_value(default)}"><Parameters BehaviorName="animationAction"/></S_ActivityLink>
                <S_ActivityLink TargetGuid="999"><Parameters BehaviorName="quest_action"/></S_ActivityLink>
              </Links></C_SmartHub><C_SmartHub EntityGuid="{guid_value(default)}"/>
            </Schedulers></database>''',
            'triggerareas.fubar': write_areas([], 12345)})
        scripts = archive({STREAMING_SOURCE: b'<Database><Skald><Module Name="streamprofileshandling"><Nodes><ProfileStateTrigger/></Nodes></Module></Skald></Database>'})
        files = {
            'mission_mission0.xml': b'<Mission><Environment><EnvState UseLayersActivation="0" SunLinkedToTOD="1"/></Environment></Mission>',
            'objects_mission0.xml': b'''<Objects>
              <Entity Name="keep" EntityId="1" EntityGuid="00000001-0000-0002" EntityClass="GeomEntity"><Properties unchanged="1"/></Entity>
              <Entity Name="gmtravel_arrival_kcd1" EntityId="2" EntityGuid="00000002-0000-0002" EntityClass="TagPoint" Pos="10,20,30"/>
              <Entity Name="gmtravel_kcd1_cart" EntityId="3" EntityGuid="00000003-0000-0002" EntityClass="GeomEntity"/>
              <Entity Name="gmtravel_return_driver" EntityId="4" EntityGuid="00000004-0000-0002" EntityClass="NPC"/>
              <Entity Name="gmtravel_interact_kcd1" EntityId="5" EntityGuid="00000005-0000-0002" EntityClass="InteractionTrigger"/>
            </Objects>''',
            'leveldata.xml': b'<LevelData><LevelInfo HeightmapMaxHeight="270"/><Layers><Layer Name="existing" Id="9" Specs="-1"/></Layers></LevelData>',
            'levelinfo.xml': b'<LevelInfo><TerrainInfo HeightmapSize="4096" UnitSize="1"/></LevelInfo>',
            'tables/ai/scheduler.xml': b'<database><Schedulers version="1"><C_SmartHub EntityGuid="99"/></Schedulers></database>',
            'whdata_0': b'<Root><ConceptManager><ConceptModules/></ConceptManager><AI><SmartAreaManager version="2"><SmartAreas/></SmartAreaManager></AI><SoulList><Souls><Soul><Name>Dude</Name><Player>1</Player></Soul></Souls></SoulList></Root>',
            'whdata_1': b'<Root version="1"><LedgeManager keep="yes"/><GameProfileManager><GameProfiles><GameProfile><Name>old</Name><Id>3</Id></GameProfile></GameProfiles></GameProfileManager></Root>',
        }
        self.addCleanup(close_archive, native)
        self.addCleanup(close_archive, scripts)
        return files, native, scripts

    def package(self, files, native, scripts, *, station_streaming=True):
        return register_entry(files, travel_graph('Travel', 'to_trosky'),
            'Quests/Travel.xml', native, scripts, 'destination', 42, guid, station_streaming=station_streaming)

    def test_resident_return_route_does_not_wait_for_profile_streaming(self):
        files, native, scripts = self.fixture()
        result, graphs, report = self.package(files, native, scripts, station_streaming=False)
        mission = ET.fromstring(result['objects_mission0.xml'])
        for name in ('gmtravel_return_driver', 'gmtravel_interact_kcd1', 'gmtravel_kcd1_cart'):
            entity = mission.find(f"Entity[@Name='{name}']")
            self.assertIsNotNone(entity)
            self.assertIsNone(entity.get('Layer'))
        self.assertFalse(any(k.startswith('layers/') for k in result))
        self.assertIsNone(report['profile'])
        self.assertFalse(report['station_streaming'])
        project = ET.fromstring(graphs['Quests/Travel.xml'])
        self.assertIsNone(project.find('.//InteractionTriggerNode'))
        self.assertIsNone(ET.fromstring(graphs['Quests/entry/destination.xml']).find('.//ProfileAsset'))

    def test_preserves_player_and_scenery_without_duplicating_companions(self):
        files, native, scripts = self.fixture()
        result, graphs, report = self.package(files, native, scripts)
        root = ET.fromstring(result['objects_mission0.xml'])
        self.assertEqual(root.find("Entity[@Name='keep']/Properties").get('unchanged'), '1')
        self.assertEqual(ET.fromstring(result['whdata_0']).findtext('.//Soul/Name'), 'Dude')
        self.assertEqual(ET.fromstring(result['whdata_1']).find('LedgeManager').get('keep'), 'yes')
        self.assertFalse(report['creates_companion'])
        self.assertFalse(report['changes_ownership'])
        self.assertFalse(any(e.get('EntityClass') in ('Horse', 'Dog') for e in root))
        self.assertEqual(root.find("Entity[@EntityClass='LevelHolder']/Properties").get('iLevelId'), '42')
        self.assertEqual(ET.fromstring(result['whdata_0']).findtext('.//ConceptModules/Module'), 'destination')
        with self.assertRaises(ValueError):
            self.package(result, native, scripts)

    def test_streamed_entities_have_unique_reserved_ids_and_registered_profile(self):
        files, native, scripts = self.fixture()
        result, _, _ = self.package(files, native, scripts)
        placed = ET.fromstring(result['objects_mission0.xml']).findall('Entity')
        layer_name = next(k for k in result if k.startswith('layers/'))
        streamed = ET.fromstring(result[layer_name]).findall('Entity')
        self.assertEqual(len({e.get('EntityId') for e in placed+streamed}), len(placed+streamed))
        self.assertEqual({e.get('ID') for e in ET.fromstring(result['extractedlayerentityids.xml'])},
                         {e.get('EntityId') for e in streamed})
        record = ET.fromstring(result['whdata_1']).findall('.//GameProfile')[-1]
        self.assertEqual(record.findtext('Id'), '4')
        self.assertEqual(record.findtext('GameLayers/GameLayer').lower(), layer_name[7:-4])
        layers = ET.fromstring(result['leveldata.xml']).find('Layers')
        self.assertEqual([x.get('Id') for x in layers], ['9', '10'])
        environment = ET.fromstring(result['mission_mission0.xml']).find('Environment/EnvState')
        self.assertEqual(environment.get('UseLayersActivation'), '1')
        self.assertEqual(environment.get('SunLinkedToTOD'), '1')

    def test_ai_geometry_and_compiled_scheduler_use_destination_identities(self):
        files, native, scripts = self.fixture()
        result, _, _ = self.package(files, native, scripts)
        mission = ET.fromstring(result['objects_mission0.xml'])
        land = mission.find("Entity[@Name='sa_land']")
        area = read_areas(result['triggerareas.fubar'])['areas'][0]
        self.assertEqual(area.guid, guid_value(land.get('EntityGuid')))
        self.assertEqual(area.points[-2], (4096.,4096.,-50.))
        self.assertEqual(ET.fromstring(result['whdata_0']).findtext('.//SmartArea/Guid'), str(area.guid))
        ids = {str(guid_value(e.get('EntityGuid'))) for e in mission}
        scheduler = ET.fromstring(result['tables/ai/scheduler.xml'])
        self.assertIsNotNone(scheduler.find(".//C_SmartHub[@EntityGuid='99']"))
        links = scheduler.findall('.//S_ActivityLink')
        self.assertEqual({e.find('Parameters').get('BehaviorName') for e in links}, {'playerWait','animationAction'})
        self.assertTrue(all(e.get('TargetGuid') in ids for e in links))
        self.assertNotIn(b'quest_action', result['objects_mission0.xml'])

    def test_both_entry_paths_wait_for_native_loaded_acknowledgement(self):
        files, native, scripts = self.fixture()
        _, graphs, _ = self.package(files, native, scripts)
        root = ET.fromstring(graphs['Quests/entry/destination.xml'])
        level = root.find('Skald/Level')
        self.assertEqual(level.get('HibernateMode'), 'Auto')
        self.assertEqual({e.get('From') for e in level.findall('Nodes/TriggerSequence/Edge')}, {'OnWake','OnLevelSwitched'})
        ready = level.find("Nodes/State[@Name='entry_ready']")
        self.assertEqual(ready.find("Edge[@To='SetTrue']").get('From'), 'stream_station.onloaded')
        project = ET.fromstring(graphs['Quests/Travel.xml'])
        self.assertIsNone(project.find('.//InteractionTriggerNode'))
        self.assertEqual(graphs['Quests/entry/streamprofileshandling.xml'], scripts.read(STREAMING_SOURCE))
        self.assertIsNone(root.find('.//Timer'))


if __name__ == '__main__':
    unittest.main()
