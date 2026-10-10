import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from region_travel_entry import entry_graph
from region_travel_visit import attach, QUEST
from region_travel_theresa import SPEC, dialogue, ROLE


class TheresaVisitTests(unittest.TestCase):
    def base(self):
        self.path = 'Quests/GlueTravel/GlueTravel_kcd1.xml'
        self.entry = 'Quests/GlueTravel/entry/kcd1_travel.xml'
        graph = b'<Database><Skald><Project Name="GlueTravel_kcd1"><Definitions/><Nodes><kcd1_travel Name="kcd1_travel"/></Nodes></Project></Skald></Database>'
        return attach({self.path: graph, self.entry: entry_graph('kcd1_travel', None)},
                      self.path, 'kcd1_travel', 'player', 'innkeeper')[0]

    def test_two_independent_visits_share_arrival_without_corrupting_saved_rattay_state(self):
        before = self.base()
        after, strings = attach(before, self.path, 'kcd1_travel', 'player', 'theresa', spec=SPEC)
        for name in ('visit_rattay.xml', 'visit_rattay_journal.xml'):
            path = 'Quests/GlueTravel/' + name
            self.assertEqual(before[path], after[path])
        project = E.fromstring(after[self.path]).find('Skald/Project')
        self.assertEqual(len(project.findall('Types')), 1)
        self.assertEqual(len(project.findall('ObjectiveValueTypes/ObjectiveValueType')), 3)
        self.assertEqual(len(project.findall("Assets/SoulAsset[@Name='travel_player']")), 1)
        self.assertEqual(project.find("Assets/SoulAsset[@Name='rattay_innkeeper']").get('SharedSoulGuids'), 'innkeeper')
        self.assertEqual(project.find("Assets/SoulAsset[@Name='theresa']").get('SharedSoulGuids'), 'theresa')
        host = E.fromstring(after[self.entry]).find('Skald/Level')
        ports = [p.get('Name') for p in host.findall('Ports/Port')]
        self.assertEqual(len(ports), len(set(ports)))
        self.assertEqual(ports.count('arrived'), 1)
        self.assertIsNotNone(project.find('Nodes/' + QUEST))
        journal = E.fromstring(after['Quests/GlueTravel/visit_theresa_journal.xml']).find('Skald/Quest')
        self.assertEqual(journal.get('Type'), 'Side')
        self.assertEqual(journal.find(".//EnumLog[@Name='Active']").get('Marker'), 'theresa')
        self.assertIsNotNone(host.find('Nodes/' + SPEC['quest']))

    def test_only_first_arrival_starts_and_actual_conversation_completes(self):
        graphs, _ = attach(self.base(), self.path, 'kcd1_travel', 'player', 'theresa', spec=SPEC)
        root = E.fromstring(graphs['Quests/GlueTravel/visit_theresa.xml'])
        gate = root.find(".//If[@Name='first_arrival']")
        self.assertEqual(gate.find("Edge[@To='Condition']").get('From'), 'progress.None')
        gate = root.find(".//If[@Name='spoke_to_theresa']")
        self.assertEqual(gate.find("Edge[@To='Condition']").get('From'), 'progress.Active')
        self.assertEqual(gate.find("Edge[@To='Exec']").get('From'), 'talked')
        dialog = E.fromstring(dialogue({'name': 'source-theresa'}))
        self.assertEqual(dialog.find('.//Dialogue').get('NonSpeakerRoles'), ROLE)
        self.assertIsNotNone(dialog.find(".//Sequence[@Name='visit']/Triggers/Port[@Name='dialog_started']"))
        self.assertEqual(dialog.find(".//Sequence[@Name='visit']").get('EndType'), 'Decision')
        self.assertEqual(list(root.iter('AreaTrigger')), [])

    def test_duplicate_and_unsafe_registration_fails_without_mutating_input(self):
        graphs, _ = attach(self.base(), self.path, 'kcd1_travel', 'player', 'theresa', spec=SPEC)
        copy = dict(graphs)
        with self.assertRaisesRegex(ValueError, 'already registered'):
            attach(graphs, self.path, 'kcd1_travel', 'player', 'theresa', spec=SPEC)
        self.assertEqual(copy, graphs)
        with self.assertRaisesRegex(ValueError, 'Invalid visit quest identifier'):
            attach(graphs, self.path, 'kcd1_travel', 'player', 'theresa', spec=dict(SPEC, file='../escape'))


if __name__ == '__main__': unittest.main()
