from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_compile import quest_dependencies, wire_bridge
from campaign_quest_bridge import QuestBridge, UnsupportedOperation, convert_state_operations
from campaign_quest_graph import emit_project
from quest_import import ast
from test_campaign_quest_graph import model


def behavior(m, body):
    m['behavior_documents'] = {'libs/ai/quests/' + m['quest'].lower() + '.xml': dict(trees={
        'onUpdate': dict(root=ast(E.fromstring(body)))})}
    return m


class QuestBridgeTests(unittest.TestCase):
    def test_actions_keep_guard_and_order_and_are_wired_to_real_quest_ports(self):
        m = behavior(model(), '''<Root><IfCondition condition="$ready"><Sequence>
<Wait duration="2s"/><SetQuestObjective quest="" objective="first" function="CompleteObjective"/>
<SetQuest questName="$this.name" objectiveName="second" function="StartObjective" paramInt="0"/>
</Sequence></IfCondition></Root>''')
        bridge_files, report = convert_state_operations([m], 'Anything')
        self.assertFalse(report['unresolved'])
        self.assertEqual(len(report['converted']), 2)
        first = report['converted'][0]
        self.assertEqual(first['site']['path'], 'Root/0/0/1')
        self.assertEqual(first['site']['ancestors'][1]['attributes']['condition'], '$ready')
        self.assertTrue(first['target']['op'].startswith('SendAIConceptSignal_'))
        native, _ = emit_project([m], 'Anything')
        wire_bridge(native, 'Anything', bridge_files, report)
        project = E.fromstring(native['Anything.xml'])
        edges = project.findall('.//arbitrary_quest/Edge')
        self.assertEqual({e.get('To') for e in edges}, {'complete_first', 'start_second'})
        self.assertFalse(project.findall('.//GameStart'))
        self.assertFalse(project.findall('.//HasteTrigger'))

    def test_condition_preserves_both_branches_and_creates_declared_context(self):
        m = model()
        bridge = QuestBridge([m], 'Other')
        source = ast(E.fromstring('''<QuestObjectiveCondition quest="" objective="first" function="IsObjectiveCompleted" failSubtMissing="false"><Then><CreateItem ItemGUID="original-item"/></Then><Else><Wait duration="1s"/></Else></QuestObjectiveCondition>'''))
        result = bridge.lower(source, m['quest'])
        self.assertEqual(result['children'], source['children'])
        self.assertIsNot(result['children'], source['children'])
        self.assertEqual(result['op'], 'GameContextCheck')
        files, wiring = bridge.emit()
        context = E.fromstring(files['Libs/Tables/ai/ScriptContext__other.xml']).find('.//ScriptContextDatabaseNode')
        self.assertEqual(context.get('Name'), result['attributes']['context'])
        self.assertEqual(wiring[0]['quest_port'], 'first_Completed')

    def test_unsupported_guards_never_turn_into_true(self):
        m = model(); bridge = QuestBridge([m], 'Other')
        for source in [
            '<QuestObjectiveCondition objective="first" function="IsObjectiveTrackedCompleted"/>',
            '<QuestCondition quest="$dynamic" function="IsQuestStarted"/>',
            '<QuestObjectiveGate objectiveName="first" objectiveState="AfterReset"/>',
            '<SetQuest questName="" function="ResetQuest"/>',
        ]:
            with self.assertRaises(UnsupportedOperation): bridge.lower(ast(E.fromstring(source)), m['quest'])
        self.assertFalse(bridge.actions)
        self.assertFalse(bridge.conditions)

    def test_dependency_scan_reports_dynamic_names_without_choosing_a_quest(self):
        m = behavior(model(), '''<Root><SetQuest questName="other_story" function="ActivateQuest"/>
<SetQuest questName="$selected" function="StartQuest"/><SetQuest questName="$this.name" function="StartQuest"/></Root>''')
        dependencies = quest_dependencies(m)
        self.assertEqual([(r['quest'], r['dynamic']) for r in dependencies], [('other_story', False), ('$selected', True)])
        _, report = convert_state_operations([m], 'Other')
        self.assertEqual(len(report['unresolved']), 2)
        self.assertFalse(report['executable'])

    def test_illegal_native_objective_name_is_encoded_consistently(self):
        m = model()
        row = m['tables']['quest_objective']['rows'][0]
        row['objective_name'] = '01_movement'
        m['graph']['nodes']['3']['payload'][0]['attributes']['Name'] = '01_movement'
        behavior(m, '<Root><SetQuestObjective objective="01_movement" function="CompleteObjective"/></Root>')
        files, _ = emit_project([m], 'Other')
        native, report = convert_state_operations([m], 'Other')
        wire_bridge(files, 'Other', native, report)
        self.assertTrue(report['wiring'][0]['quest_port'].startswith('complete_source_01_movement_'))


if __name__ == '__main__': unittest.main()
