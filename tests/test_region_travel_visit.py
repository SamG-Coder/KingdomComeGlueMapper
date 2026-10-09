import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from region_travel_visit import attach, QUEST
from region_travel_entry import entry_graph
from region_travel_merchant import dialogue, ROLE


class VisitDialogueTests(unittest.TestCase):
    def build(self):
        path = 'Quests/GlueTravel/GlueTravel_kcd1.xml'
        base = b'<Database><Skald><Project Name="GlueTravel_kcd1"><Definitions/><Nodes><kcd1_travel Name="kcd1_travel"/></Nodes></Project></Skald></Database>'
        result, _ = attach({path: base, 'Quests/GlueTravel/entry/kcd1_travel.xml': entry_graph('kcd1_travel', None)},
                           path, 'kcd1_travel', 'player', 'source-innkeeper')
        return {k: ET.fromstring(v) for k, v in result.items()}

    def test_only_an_active_quest_completes_from_real_dialogue(self):
        graphs = self.build()
        parent = graphs['Quests/GlueTravel/GlueTravel_kcd1.xml']
        node = parent.find('Skald/Project/Nodes/' + QUEST)
        self.assertEqual([(e.get('From'), e.get('To')) for e in node],
                         [('kcd1_travel.arrived', 'arrive'), ('rattay_innkeeper.dialog_started', 'talked')])
        state = graphs['Quests/GlueTravel/visit_rattay.xml']
        self.assertEqual([e.get('From') for e in state.iter('Edge') if e.get('To') == 'SetDone'],
                         ['spoke_to_innkeeper.True', 'progress.OnDone'])
        gate = state.find(".//If[@Name='spoke_to_innkeeper']")
        self.assertEqual([(e.get('From'), e.get('To')) for e in gate],
                         [('talked', 'Exec'), ('progress.Active', 'Condition')])
        for graph in graphs.values():
            self.assertEqual(list(graph.iter('AreaTrigger')), [])
            self.assertFalse(any('OnEnter' in e.get('From', '') for e in graph.iter('Edge')))

    def test_marker_resolves_the_imported_person_in_regional_quest(self):
        graphs = self.build()
        quest = graphs['Quests/GlueTravel/visit_rattay_journal.xml']
        alias = quest.find(".//EnumLog[@Name='Active']").get('Marker')
        asset = graphs['Quests/GlueTravel/GlueTravel_kcd1.xml'].find(f".//SoulAsset[@Name='{alias}']")
        self.assertEqual(asset.get('SharedSoulGuids'), 'source-innkeeper')

    def test_talk_menu_binds_player_and_merchant_before_native_shop_action(self):
        root = ET.fromstring(dialogue('source-innkeeper'))
        body = root.find('Skald/FaderDialog/Dialogue')
        self.assertEqual(body.get('NonSpeakerRoles'), ROLE)
        self.assertEqual(body.find('Decision').get('Priority'), 'General')
        self.assertEqual(body.find(f"SelectedSouls/SelectedSoul[@Role='{ROLE}']").get('Soul'), 'source-innkeeper')
        trade = body.find(".//Sequence[@Name='trade']")
        self.assertEqual(trade.get('Type'), 'OpenShop')
        self.assertEqual(trade.get('EndType'), 'EndDialogue')
        self.assertEqual(trade.find('Elements/Response').get('Role'), 'HENRY')
        self.assertIsNone(trade.find('Decision'))
        self.assertIn('trade_selected', trade.get('ExitScript'))
        # A SelectedSoul alone cannot make Henry a runtime participant.
        self.assertEqual({r.get('Role') for r in body.iter('Response')}, {'HENRY'})

    def test_opening_talk_emits_completion_before_any_shop_choice(self):
        root = ET.fromstring(dialogue('source-innkeeper'))
        dialog = root.find('Skald/FaderDialog')
        self.assertEqual(dialog.find("Ports/Port[@Name='dialog_started']").get('Direction'), 'Out')
        entry = dialog.find('Dialogue/Decision')
        self.assertEqual(entry.get('Autoselect'), 'true')
        seqs = entry.findall('Sequences/Sequence')
        self.assertEqual(len(seqs), 1)
        self.assertIsNone(seqs[0].get('EntryCondition'))  # Also works on a second Talk.
        self.assertEqual(seqs[0].get('EndType'), 'Decision')
        self.assertEqual(seqs[0].find('Triggers/Port').get('Name'), 'dialog_started')
        self.assertIsNotNone(seqs[0].find('Decision/Sequences/Sequence[@Type="OpenShop"]'))
        self.assertIsNone(seqs[0].find('Decision/Sequences/Sequence/Triggers'))


if __name__ == '__main__': unittest.main()
