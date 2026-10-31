from pathlib import Path
import sys
import unittest
import json
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
    def test_compiled_helper_locals_shadow_caller_and_survive_parallel_reload(self):
        from test_quest_lab_parallel import wait
        helper='<Root><Behavior><Sequence><Parallel successMode="All" failureMode="Any">'+\
            '<Sequence>'+wait(1)+'<Expression expressions="$count=$count+1"/></Sequence>'+\
            '<Expression expressions="$count=$count+1"/></Parallel>'+\
            '<Expression expressions="$total=$total+$count"/></Sequence></Behavior></Root>'
        model=source('<Root><Behavior><Sequence><IncludeTree File="shared.xml" Name="helper"/>'
                     '<IncludeTree File="shared.xml" Name="helper"/></Sequence></Behavior></Root>',{
            'libs/ai/shared.xml':dict(trees={'helper':dict(variables=[
                dict(name='count',type='_int',values='1',form='single')],root=ast(E.fromstring(helper)))})})
        model['behavior_documents']['libs/ai/quests/q_test.xml']['trees']['onUpdate']['variables'].append(
            dict(name='count',type='_int',values='10',form='single'))
        files,_,report=convert_behaviors([model],'CallScopes',catalog())
        self.assertEqual(report['whole_trees_emitted'],2)
        main=next(blob for path,blob in files.items() if path.endswith('/quests/q_test.xml'))
        g=graph(files); b=Behavior('scopes',main,g,variables={'total':0})
        self.assertFalse(b.unsupported)
        task=b.start('onUpdate')
        saved=json.loads(json.dumps(b.snapshot())); saved_graph=g.snapshot()
        for replay in range(2):
            if replay:g.restore(saved_graph);b.restore(saved)
            while b.next_deadline() is not None:g.advance_to(b.next_deadline());b.pump()
            self.assertEqual(b.tasks[task]['status'],'success')
            self.assertEqual(b.tasks[task]['locals']['count'],10)
            self.assertEqual(b.variables,{'total':6})
            self.assertEqual(b.tasks[task]['call_scopes'],{})

    def test_helper_uninitialized_local_does_not_read_caller_variable(self):
        local=b'<BehaviorTree name="helper"><Variables><Variable name="x" type="_int"/></Variables><Root><Behavior><Expression expressions="$out=$x"/></Behavior></Root></BehaviorTree>'
        b=Behavior('x',caller(),graph({'AI/shared.xml':local}),variables={'x':9})
        with self.assertRaisesRegex(Unsupported,'Unbound'):b.start('main')
        self.assertEqual(b.variables,{'x':9})

    def test_canceling_waiting_helper_releases_call_locals(self):
        from test_quest_lab_parallel import wait
        local=('<BehaviorTree name="helper"><Variables><Variable name="x" type="_int" values="3"/>'
               '</Variables><Root><Behavior>'+wait(10)+'</Behavior></Root></BehaviorTree>').encode()
        main=caller().replace(b'<IncludeTree',b'<Parallel successMode="Any" failureMode="All"><IncludeTree').replace(
            b'Name="helper"/>',b'Name="helper"/><Success/></Parallel>')
        b=Behavior('x',main,graph({'AI/shared.xml':local}))
        task=b.start('main')
        self.assertEqual(b.tasks[task]['status'],'success')
        self.assertEqual(b.tasks[task]['call_scopes'],{})
        self.assertTrue(any(t['status']=='canceled' for t in b.tasks.values()))

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
