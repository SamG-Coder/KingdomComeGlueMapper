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
    def test_registered_external_entry_is_scanned_without_unrelated_helpers(self):
        m = model()
        def tree(objective):
            return dict(root=ast(E.fromstring('<Root><SetQuestObjective quest="&quot;$__object.name&quot;" '
                'objective="'+objective+'" function="StartObjective"/></Root>')))
        m['behavior_documents'] = {'libs/ai/shared/controller.xml':dict(trees={
            'registered':tree('first'), 'unrelated':tree('second')})}
        m['behavior_status'] = 'no_same_named_retail_program'
        entry = dict(kind='behavior_callback',file='shared/controller.xml',tree='registered',name='callback')
        m['controller_registration'] = dict(entry_points=[entry, dict(entry)])
        _, report = convert_state_operations([m], 'External')
        self.assertEqual(report['unresolved'], [])
        self.assertEqual(len(report['converted']), 1)
        self.assertEqual(report['wiring'][0]['quest_port'], 'start_first')
        self.assertEqual(report['converted'][0]['site']['document'], 'libs/ai/shared/controller.xml')
        m['controller_registration']['entry_points'] = [dict(entry,tree='absent')]
        _, report = convert_state_operations([m], 'External')
        self.assertEqual(report['converted'], [])
        self.assertEqual(report['unresolved'][0]['reason'], 'Missing registered controller entry tree')

    def test_object_owner_requires_registered_callback_and_preserves_source(self):
        import copy
        m = behavior(model(), '''<Root><SetQuestObjective quest="&quot;$__object.name&quot;"
          objective="first" function="StartObjective"/></Root>''')
        original = copy.deepcopy(m['behavior_documents'])
        _, report = convert_state_operations([m], 'Scoped')
        self.assertEqual(len(report['unresolved']), 1)
        self.assertTrue(quest_dependencies(m)[0]['dynamic'])
        m['controller_registration'] = dict(entry_points=[dict(kind='behavior_callback',
            file='quests/arbitrary_quest.xml', tree='onUpdate', name='public_callback')])
        _, report = convert_state_operations([m], 'Scoped')
        self.assertEqual(report['unresolved'], [])
        self.assertEqual(report['wiring'][0]['quest'], m['quest'])
        self.assertEqual(report['wiring'][0]['quest_port'], 'start_first')
        self.assertEqual(quest_dependencies(m), [])
        self.assertEqual(m['behavior_documents'], original)
        m['controller_registration']['entry_points'][0]['tree'] = 'other_tree'
        _, report = convert_state_operations([m], 'Scoped')
        self.assertEqual({r['reason'] for r in report['unresolved']}, {
            'Missing registered controller entry tree', 'Unregistered or dynamic source quest: $__object.name'})
        self.assertTrue(quest_dependencies(m)[0]['dynamic'])

    def test_objective_gate_retains_continuous_lifecycle_and_negation(self):
        m = model()
        for policy in ('Halt', 'KeepRunning'):
            bridge = QuestBridge([m], 'Other')
            node = ast(E.fromstring('''<QuestObjectiveGate questName="" objectiveName="first"
              objectiveState="Started" NegateTo="true" RunLogic="''' + policy + '''">
              <CallBehavior Target="$work" BehaviorName="use"/></QuestObjectiveGate>'''))
            result = bridge.lower(node, m['quest'])
            self.assertEqual(result['op'], 'GameContextBarrier')
            self.assertEqual(result['attributes']['RunLogic'], policy)
            self.assertEqual(result['attributes']['Negation'], 'true')
            self.assertEqual(result['children'], node['children'])
            _, wiring = bridge.emit()
            self.assertEqual(wiring[0]['quest_port'], 'first_Started')

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
