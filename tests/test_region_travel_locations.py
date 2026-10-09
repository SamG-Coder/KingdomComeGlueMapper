import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from region_travel_locations import local_map, convert_mark
from region_travel_visit import attach, quest_graph, QUEST
from region_travel_entry import entry_graph


class RegionRegistrationTests(unittest.TestCase):
    def test_local_map_converts_world_to_image_and_focal_ellipse(self):
        row = dict(ui_local_map_id='id', location_id='town', ui_local_map_name='town',
            position_x='100', position_y='900', width='200', height='924',
            focus1_x='160', focus1_y='800', focus2_x='240', focus2_y='800',
            major_axis='100', enabled_by_default='True')
        result = local_map(row, 1024, 2048, 2)
        self.assertEqual(result['position_x'], '200')
        self.assertEqual(result['position_y'], '248')
        self.assertEqual(result['height'], '200')
        self.assertEqual(result['active_x'], '300')
        self.assertEqual(result['active_y'], '388')
        self.assertEqual(result['active_width'], '200')
        self.assertEqual(result['active_height'], '120')
        row['major_axis'] = '50'
        with self.assertRaises(ValueError): local_map(row, 1024, 2048, 2)

    def test_fast_travel_enum_conversion_retains_identity_and_discovery(self):
        original = ET.fromstring('<Mark><Id>source-id</Id><LocationId>source-location</LocationId>'
            '<MarkType>5</MarkType><LayerName>fast_travel_network{guid}</LayerName>'
            '<StaticData><State>2</State><Flags>35</Flags></StaticData></Mark>')
        result = convert_mark(original, {'type':dict(poi_type_id='type', mark_type='5')},
                              {'type':dict(mark_type='7')})
        self.assertEqual(result.findtext('MarkType'), '7')
        self.assertEqual(result.findtext('POITypeId'), 'type')
        self.assertEqual(result.findtext('Id'), 'source-id')
        self.assertEqual(result.findtext('StaticData/State'), '2')
        self.assertIsNone(result.find('LayerName'))
        self.assertEqual(original.findtext('MarkType'), '5')

    def test_quest_gets_level_ownership_without_moving_saved_states(self):
        graph = 'Quests/GlueTravel/GlueTravel_kcd1.xml'
        entry = 'Quests/GlueTravel/entry/kcd1_travel.xml'
        base = b'<Database><Skald><Project Name="GlueTravel_kcd1"><Definitions/><Nodes><kcd1_travel Name="kcd1_travel"/></Nodes></Project></Skald></Database>'
        result, strings = attach({graph:base,entry:entry_graph('kcd1_travel',None)},graph,'kcd1_travel','player','innkeeper-soul')
        legacy = ET.fromstring(result['Quests/GlueTravel/visit_rattay.xml']).find('Skald/Module')
        original = ET.fromstring(quest_graph()).find('Skald/Quest')
        self.assertEqual(legacy.get('Name'),original.get('Name'))
        self.assertEqual(legacy.get('HasteNamespace'),'true')
        self.assertEqual([ET.tostring(s) for s in legacy.findall('Nodes/State')],
                         [ET.tostring(s) for s in original.findall('Nodes/State')])
        self.assertIsNone(legacy.find('Objectives'))
        level=ET.fromstring(result[entry]).find('Skald/Level')
        self.assertIsNotNone(level.find('Nodes/'+QUEST))
        self.assertEqual(level.find('Definitions/Definition').get('File'),'../visit_rattay_journal.xml')
        regional=ET.fromstring(result['Quests/GlueTravel/visit_rattay_journal.xml']).find('Skald/Quest')
        self.assertIsNotNone(regional.find('Objectives/Objective'))
        self.assertEqual(regional.get('HasteNamespace'),'false')
        self.assertIsNone(regional.find('Nodes/State'))
        self.assertEqual(len(ET.fromstring(strings)),4)


if __name__ == '__main__': unittest.main()
