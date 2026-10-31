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
