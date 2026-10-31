from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_campaign_behavior import source, catalog
from campaign_behavior import convert_behaviors
from campaign_compile import wire_bridge
from campaign_quest_graph import emit_project
from quest_lab import Unsupported
from quest_lab.behavior import Behavior
from quest_lab.graph import Graph
from quest_lab.source import Source


class SignalTests(unittest.TestCase):
    def test_compiled_parallel_controller_observes_other_branch_objective_change(self):
        model = source('''<Root><Behavior><Parallel successMode="All" failureMode="Any">
          <Sequence><Wait duration="1s" timeType="GameTime" variation="" doFail="false"/>
          <SetQuestObjective objective="first" function="CompleteObjective"/></Sequence>
          <Sequence><Wait duration="2s" timeType="GameTime" variation="" doFail="false"/>
          <QuestObjectiveCondition objective="first" function="IsObjectiveCompleted" failSubtMissing="true">
          <Then><SetQuestObjective objective="second" function="StartObjective"/></Then><Else><Fail/></Else>
          </QuestObjectiveCondition></Sequence></Parallel></Behavior></Root>''')
        behaviors, bridges, report = convert_behaviors([model], 'Parallel', catalog())
        self.assertEqual(report['whole_trees_emitted'], 1)
        files, _ = emit_project([model], 'Campaign')
        wire_bridge(files, 'Campaign', bridges, report)
        files.update({p.removeprefix('Quests/'):b for p,b in bridges.items() if p.startswith('Quests/')})
        graph = Graph(Source.blobs(files), 'Campaign')
        behavior = Behavior('controller', next(iter(behaviors.values())), graph)
        self.assertEqual(behavior.unsupported, [])
        task = behavior.start('onUpdate')
        graph.advance_to(behavior.next_deadline()); behavior.pump()
        self.assertTrue(graph.value('q_test.first_Completed'))
        self.assertFalse(graph.value('q_test.second_Started'))
        self.assertEqual(behavior.tasks[task]['status'], 'parallel')
        graph.advance_to(behavior.next_deadline()); behavior.pump()
        self.assertTrue(graph.value('q_test.second_Started'))
        self.assertEqual(behavior.tasks[task]['status'], 'success')

    def test_complete_fail_if_missing_condition_executes_both_authored_paths(self):
        model = source('''<Root><Behavior><QuestObjectiveCondition quest="" objective="first"
          function="IsObjectiveCompleted" failSubtMissing="true">
          <Else><Fail/></Else><Then><SetQuestObjective quest="" objective="second"
          function="StartObjective"/></Then></QuestObjectiveCondition></Behavior></Root>''')
        behaviors, bridges, report = convert_behaviors([model], 'Branches', catalog())
        files, _ = emit_project([model], 'Campaign')
        wire_bridge(files, 'Campaign', bridges, report)
        files.update({p.removeprefix('Quests/'):b for p,b in bridges.items() if p.startswith('Quests/')})
        graph = Graph(Source.blobs(files), 'Campaign')
        behavior = Behavior('controller', next(iter(behaviors.values())), graph)
        self.assertEqual(behavior.unsupported, [])
        self.assertEqual(behavior.tasks[behavior.start('onUpdate')]['status'], 'fail')
        self.assertFalse(graph.value('q_test.second_Started'))
        graph.fire('q_test.complete_first')
        self.assertEqual(behavior.tasks[behavior.start('onUpdate')]['status'], 'success')
        self.assertTrue(graph.value('q_test.second_Started'))

    def test_controller_conditions_read_live_quest_state_after_restore(self):
        model = source('''<Root><Behavior><QuestObjectiveCondition quest="" objective="first"
          function="IsObjectiveCompleted" failSubtMissing="false">
          <Then><SetQuestObjective quest="" objective="second" function="StartObjective"/></Then>
          <Else><SetQuestObjective quest="" objective="first" function="CompleteObjective"/></Else>
          </QuestObjectiveCondition></Behavior></Root>''')
        behaviors, bridges, report = convert_behaviors([model], 'Conditions', catalog())
        files, _ = emit_project([model], 'Campaign')
        wire_bridge(files, 'Campaign', bridges, report)
        files.update({p.removeprefix('Quests/'):b for p,b in bridges.items() if p.startswith('Quests/')})
        graph = Graph(Source.blobs(files), 'Campaign')
        self.assertEqual(graph.unsupported, [])
        behavior = Behavior('controller', next(iter(behaviors.values())), graph)
        self.assertEqual(behavior.unsupported, [])
        before = graph.snapshot()
        behavior.start('onUpdate')
        self.assertTrue(graph.value('q_test.first_Completed'))
        self.assertFalse(graph.value('q_test.second_Started'))
        behavior.start('onUpdate')
        self.assertTrue(graph.value('q_test.second_Started'))
        graph.restore(before)
        behavior.start('onUpdate')
        self.assertFalse(graph.value('q_test.second_Started'))
        self.assertEqual([r['result'] for r in graph.trace if r['kind']=='game_context_check'], [False,True,False])

    def test_unknown_or_multiple_context_writers_are_not_assumed_false(self):
        blob=b'''<Database><Skald><Project Name="Test"><Nodes>
          <SetGameContext Name="a"><Constant Name="Context" Value="duplicate"/>
          <Constant Name="IsActive" Value="false"/></SetGameContext>
          <SetGameContext Name="b"><Constant Name="Context" Value="duplicate"/>
          <Constant Name="IsActive" Value="true"/></SetGameContext>
          </Nodes></Project></Skald></Database>'''
        graph=Graph(Source.blobs({'test.xml':blob}), 'Test')
        self.assertEqual(len(graph.unsupported), 2)
        for context in ('missing','duplicate'):
            with self.assertRaises(Unsupported): graph.game_context(context)

    def test_converted_controller_drives_quest_through_native_bridge(self):
        model = source('''<Root><Behavior><Sequence>
          <SetQuestObjective quest="" objective="first" function="StartObjective"/>
          <SetQuestObjective quest="" objective="first" function="CompleteObjective"/>
          </Sequence></Behavior></Root>''')
        behaviors, bridges, report = convert_behaviors([model], 'Signals', catalog())
        files, _ = emit_project([model], 'Campaign')
        wire_bridge(files, 'Campaign', bridges, report)
        files.update({p.removeprefix('Quests/'):b for p,b in bridges.items() if p.startswith('Quests/')})
        graph = Graph(Source.blobs(files), 'Campaign')
        self.assertEqual(graph.unsupported, [])
        behavior = Behavior('controller', next(iter(behaviors.values())), graph)
        self.assertEqual(behavior.unsupported, [])
        saved = graph.snapshot()
        task = behavior.start('onUpdate')
        self.assertEqual(behavior.tasks[task]['status'], 'success')
        self.assertTrue(graph.value('q_test.first_Completed'))
        self.assertEqual(len([r for r in graph.trace if r['kind']=='ai_concept_signal']), 2)
        graph.restore(saved)
        self.assertFalse(graph.value('q_test.first_Completed'))
        behavior.start('onUpdate')
        self.assertTrue(graph.value('q_test.first_Completed'))

    def test_inactive_and_unknown_listeners_do_not_emit(self):
        blob=b'''<Database><Skald><Project Name="Test"><Nodes>
          <AIConceptSignalTrigger Name="signal" NotificationName="example">
          <Constant Name="IsActive" Value="false"/></AIConceptSignalTrigger>
          </Nodes></Project></Skald></Database>'''
        graph=Graph(Source.blobs({'test.xml':blob}), 'Test')
        self.assertEqual(graph.unsupported, [])
        graph.signal('example')
        self.assertEqual(graph.events['signal.OnNotification'], 0)
        with self.assertRaises(Unsupported): graph.signal('missing')
