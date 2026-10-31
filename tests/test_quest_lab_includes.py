from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_campaign_behavior import source, catalog
from quest_import import ast
from campaign_behavior import convert_behaviors
from campaign_compile import wire_bridge
from campaign_quest_graph import emit_project
from quest_lab import Unsupported, BudgetExceeded
from quest_lab.behavior import Behavior
from quest_lab.graph import Graph
from quest_lab.source import Source


def graph(files=None, budget=10000):
    blobs = {'graph.xml': b'<Database><Skald><Project Name="Test"><Nodes/></Project></Skald></Database>'}
    blobs.update(files or {})
    return Graph(Source.blobs(blobs), 'Test', budget=budget)


def caller(file='shared.xml', name='helper'):
    return ('<BehaviorTree name="main"><Root><Behavior><IncludeTree File="'+file+
            '" Name="'+name+'"/></Behavior></Root></BehaviorTree>').encode()


class IncludeTests(unittest.TestCase):
    def test_converted_nested_helper_wait_and_objective_action_survive_restore(self):
        model = source('<Root><Behavior><IncludeTree File="shared.xml" Name="helper"/></Behavior></Root>', {
            'libs/ai/shared.xml': dict(trees={'helper': dict(variables=[], root=ast(E.fromstring(
                '<Root><Behavior><IncludeTree File="finish.xml" Name="finish"/></Behavior></Root>')))}),
            'libs/ai/finish.xml': dict(trees={'finish': dict(variables=[], root=ast(E.fromstring(
                '<Root><Behavior><Sequence><Wait duration="1s" timeType="GameTime" variation="" '
                'doFail="false" skipInLOD="false"/><SetQuestObjective objective="first" '
                'function="CompleteObjective"/></Sequence></Behavior></Root>')))})})
        behaviors, bridges, report = convert_behaviors([model], 'Includes', catalog())
        self.assertEqual(report['whole_trees_emitted'], 3)
        files, _ = emit_project([model], 'Campaign')
        wire_bridge(files, 'Campaign', bridges, report)
        files.update({p.removeprefix('Quests/'): b for p,b in bridges.items() if p.startswith('Quests/')})
        files.update(behaviors)
        g = Graph(Source.blobs(files), 'Campaign')
        b = Behavior('controller', behaviors['AI/includes/q_test/quests/q_test.xml'], g)
        self.assertEqual(b.unsupported, [])
        task = b.start('onUpdate')
        self.assertEqual(b.tasks[task]['status'], 'waiting')
        self.assertFalse(g.value('q_test.first_Completed'))
        saved_g, saved_b = g.snapshot(), b.snapshot()
        for replay in range(2):
            if replay: g.restore(saved_g); b.restore(saved_b)
            g.advance_to(b.next_deadline()); b.pump()
            self.assertEqual(b.tasks[task]['status'], 'success')
            self.assertTrue(g.value('q_test.first_Completed'))

    def test_failure_returns_to_callers_and_unused_trees_are_not_executed(self):
        helper = b'<BehaviorTrees><BehaviorTree name="helper"><Root><Behavior><Fail/></Behavior></Root></BehaviorTree><BehaviorTree name="unused"><Root><Unknown/></Root></BehaviorTree></BehaviorTrees>'
        b = Behavior('x', caller(), graph({'Libs/AI/shared.xml': helper}))
        self.assertFalse(b.unsupported)
        self.assertEqual(b.tasks[b.start('main')]['status'], 'fail')

    def test_missing_dynamic_and_local_scope_includes_stay_explicit(self):
        local = b'<BehaviorTree name="helper"><Variables><Variable name="x"/></Variables><Root><Behavior><Success/></Behavior></Root></BehaviorTree>'
        for blob, files in [(caller(), {}), (caller(name='$selected'), {}),
                             (caller(), {'AI/shared.xml':local})]:
            b = Behavior('x', blob, graph(files))
            self.assertTrue(b.unsupported)
            with self.assertRaises(Unsupported): b.start('main')

    def test_ambiguous_files_and_child_overrides_are_not_silently_selected(self):
        helper = b'<BehaviorTree name="helper"><Root><Behavior><Success/></Behavior></Root></BehaviorTree>'
        b = Behavior('x', caller(), graph({'AI/shared.xml':helper, 'Libs/AI/shared.xml':helper}))
        self.assertTrue(b.unsupported)
        with self.assertRaisesRegex(Unsupported, 'ambiguous'): b.start('main')
        overridden = caller().replace(b'Name="helper"/>', b'Name="helper"><Success/></IncludeTree>')
        b = Behavior('x', overridden, graph({'AI/shared.xml':helper}))
        with self.assertRaisesRegex(Unsupported, 'overrides'): b.start('main')

    def test_recursive_calls_use_budget_and_helper_changes_invalidate_snapshot(self):
        recursive = caller(name='main')
        b = Behavior('x', recursive, graph({'AI/shared.xml':recursive}, budget=40))
        self.assertFalse(b.unsupported)
        with self.assertRaises(BudgetExceeded): b.start('main')
        first = b'<BehaviorTree name="helper"><Root><Behavior><Success/></Behavior></Root></BehaviorTree>'
        second = first.replace(b'Success', b'Fail')
        before = Behavior('x', caller(), graph({'AI/shared.xml':first})).snapshot()
        after = Behavior('x', caller(), graph({'AI/shared.xml':second}))
        with self.assertRaisesRegex(ValueError, 'snapshot mismatch'): after.restore(before)


if __name__ == '__main__': unittest.main()
