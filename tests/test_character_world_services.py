import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_world_services import register_services
from character_world_dependencies import WorldDependencies
from region_travel import guid
from campaign_entity_links import native_guid


class WorldServicesTests(unittest.TestCase):
    def fixture(self):
        native = E.fromstring('''<Objects>
          <Entity EntityId="1" Name="sa_land"><EntityLinks>
            <Link Name="mrkev" TargetId="2"/><Link Name="redkev" TargetId="3"/>
            <Link Name="hangoverSpotsHub" TargetId="4"/><Link Name="playerLinkRerouter" TargetId="5"/>
            <Link Name="flutistController" TargetId="6"/><Link Name="npcEmergencyPoint" TargetId="7"/>
            <Link Name="punishment_fallbackArea" TargetId="99"/>
          </EntityLinks></Entity>
          <Entity EntityId="2"><Properties guidSmartObjectType="human"/><EntityLinks>
            <Link Name="horseParkingSpot" TargetId="8"/><Link Name="extraGuards_proxy" TargetId="99"/>
          </EntityLinks></Entity>
          <Entity EntityId="3"><Properties guidSmartObjectType="animal"/></Entity>
          <Entity EntityId="4"><EntityLinks><Link Name="hangoverSpot" TargetId="99"/></EntityLinks></Entity>
          <Entity EntityId="5" EntityClass="TagPoint"><Properties bSaved_by_game="0"/></Entity>
          <Entity EntityId="6" EntityClass="SmartObjectHolder"><Properties guidSmartObjectType="flutist"/></Entity>
          <Entity EntityId="7" EntityClass="TagPoint" Pos="999,999,999"><Properties bSaved_by_game="0"/></Entity>
          <Entity EntityId="8" EntityClass="SmartObjectHolder" Pos="999,999,999">
            <Properties guidSmartObjectType="horseParking" soclass_SmartObjectHelpers="animal_horseDrink"/>
          </Entity>
        </Objects>''')
        source = E.fromstring('''<Objects>
          <Entity EntityId="1" Name="SA_Land"><EntityLinks>
            <Link Name="" TargetId="2"/><Link Name="hangoverSpotsHub" TargetId="3"/>
            <Link Name="rainshelterMainPoint" TargetId="5"/>
          </EntityLinks></Entity>
          <Entity Name="exit" EntityId="2" EntityGuid="22" EntityClass="TagPoint" Pos="10,20,30"><Properties sWH_AI_EntityCategory="levelExit"/></Entity>
          <Entity Name="hangover" EntityId="3" EntityGuid="33" EntityClass="TagPoint" Pos="11,21,31">
            <EntityLinks><Link Name="hangoverSpot" TargetId="4"/></EntityLinks>
          </Entity>
          <Entity Name="spot" EntityId="4" EntityGuid="44" EntityClass="TagPoint" Pos="12,22,32"/>
          <Entity Name="town" EntityId="5" EntityGuid="55" EntityClass="TagPoint" Pos="13,23,33"/>
          <Entity EntityId="6"><EntityLinks><Link Name="horseParkingSpot" TargetId="7"/><Link Name="horseParkingSpot" TargetId="7"/></EntityLinks></Entity>
          <Entity Name="parking" EntityId="7" EntityGuid="77" EntityClass="TagPoint" Pos="14,24,34" Rotate="1,0,0,0"/>
          <Entity EntityId="8" Layer="quest"><EntityLinks><Link Name="horseParkingSpot" TargetId="9"/></EntityLinks></Entity>
          <Entity Name="camp" EntityId="9" EntityGuid="99" EntityClass="TagPoint" Pos="15,25,35"/>
        </Objects>''')
        mission = E.fromstring('''<Objects><Entity Name="sa_land" EntityId="1" Pos="0,0,-50"><EntityLinks>
          <Link Name="mrkev" TargetId="2"/><Link Name="redkev" TargetId="3"/></EntityLinks></Entity>
          <Entity Name="human" EntityId="2"><Properties guidSmartObjectType="human"/></Entity>
          <Entity Name="animal" EntityId="3"><Properties guidSmartObjectType="animal"/></Entity>
          <Entity Name="scenery_without_id" Geometry="unchanged"/></Objects>''')
        for i, e in enumerate(mission, 1): e.set('EntityGuid', native_guid(i))
        files = {'objects_mission0.xml': E.tostring(mission),
                 'smartobjecthelpersetanimations.xml': b'<SmartObjectAnimationCollections><Collection HelperSetName="existing"/></SmartObjectAnimationCollections>'}
        helpers = b'<SmartObjectAnimationCollections><Collection HelperSetName="animal_horseDrink"><Animation/></Collection></SmartObjectAnimationCollections>'
        return files, list(source), list(native), helpers

    def test_native_links_have_real_source_positioned_entities_and_helper_dependencies(self):
        files, source, native, helpers = self.fixture()
        original = copy.deepcopy(files)
        result, report = register_services(files, source, native, guid, helpers)
        self.assertEqual(files, original)
        mission = E.fromstring(result['objects_mission0.xml'])
        by_guid = {e.get('EntityGuid'): e for e in mission}
        waiting = E.fromstring(result['waitinglinks.xml'])
        for edge in waiting.findall('WaitingLinks/WaitingLink'):
            self.assertIn(edge.get('SourceId'), by_guid)
            self.assertIn(edge.get('TargetId'), by_guid)
        self.assertEqual(report['counts']['horseParkingSpot'], 1)
        parking = next(r for r in report['registered'] if r['label'] == 'horseParkingSpot')
        self.assertEqual(parking['position'], '14,24,34')
        self.assertNotIn(b'999,999,999', result['objects_mission0.xml'])
        self.assertNotIn(b'15,25,35', result['objects_mission0.xml'])
        self.assertIn(b'HelperSetName="existing"', result['smartobjecthelpersetanimations.xml'])
        self.assertIn(b'animal_horseDrink', result['smartobjecthelpersetanimations.xml'])
        self.assertFalse(report['all_native_services_registered'])

    def test_new_native_services_and_missing_helpers_cannot_be_silently_dropped(self):
        files, source, native, helpers = self.fixture()
        E.SubElement(native[0].find('EntityLinks'), 'Link', Name='newService', TargetId='2')
        with self.assertRaisesRegex(ValueError, 'Unmapped native world services'):
            register_services(files, source, native, guid, helpers)
        files, source, native, helpers = self.fixture()
        with self.assertRaisesRegex(ValueError, 'Missing native horse-parking helper'):
            register_services(files, source, native, guid, b'<SmartObjectAnimationCollections/>')

    def test_cycle_safe_creation_binds_only_created_endpoints(self):
        graph = WorldDependencies()
        a = graph.add('entity', 'a', {'name': 'a'})
        b = graph.add('entity', 'b', {'name': 'b'})
        c = graph.add('missing_type', 'c', {})
        graph.link(a, b, 'home');graph.link(b, a, 'owner');graph.link(a, b, 'home');graph.link(a, c, 'guard')
        graph.roots = [b]
        class Adapter:
            def __init__(self): self.created, self.bound = [], []
            def create(self, identity, source):
                self.created.append(identity);return 'native/' + identity
            def bind(self, a, b, label):
                self.bound.append((a, b, label))
        adapter = Adapter()
        result = graph.convert({'entity': adapter})
        self.assertEqual(adapter.created, ['a', 'b'])
        self.assertEqual(len(adapter.bound), 2)
        self.assertEqual(len(result['blocked_edges']), 1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['root_status'][b], 'incomplete')


if __name__ == '__main__': unittest.main()
