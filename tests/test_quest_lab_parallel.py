from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from quest_lab import Unsupported
from quest_lab.behavior import Behavior
from test_quest_lab_includes import graph


def wait(seconds):
    return '<Wait duration="'+str(seconds)+'s" timeType="GameTime" variation="" doFail="false"/>'


def behavior(body, **kwargs):
    blob = ('<BehaviorTree name="main"><Root><Behavior>'+body+'</Behavior></Root></BehaviorTree>').encode()
    b = Behavior('test', blob, graph(), **kwargs)
    return b, b.start('main', 'main')


class ParallelTests(unittest.TestCase):
    def test_parallel_waits_overlap_and_saved_branches_resume(self):
        b, task = behavior('<Parallel successMode="All" failureMode="Any">'+wait(2)+wait(5)+'</Parallel>')
        self.assertEqual(b.unsupported, [])
        self.assertEqual(b.tasks[task]['status'], 'parallel')
        self.assertEqual(b.next_deadline(), 2000000)
        b.graph.advance_to(b.next_deadline()); b.pump()
        self.assertEqual(b.tasks[task]['status'], 'parallel')
        saved, saved_graph = b.snapshot(), b.graph.snapshot()
        for replay in range(2):
            if replay: b.graph.restore(saved_graph); b.restore(saved)
            self.assertEqual(b.next_deadline(), 5000000)
            b.graph.advance_to(b.next_deadline()); b.pump()
            self.assertEqual(b.tasks[task]['status'], 'success')
            self.assertIsNone(b.next_deadline())

    def test_any_failure_cancels_nested_wait_and_external_request(self):
        b, task = behavior('<Parallel successMode="All" failureMode="Any">'
            '<Parallel successMode="All" failureMode="Any">'+wait(9)+'<EngineWork/></Parallel>'
            '<Sequence>'+wait(1)+'<Fail/></Sequence></Parallel>',
            contracts={'EngineWork':{'reason':'Explicit engine outcome required'}})
        self.assertTrue(any(t['status']=='external' for t in b.tasks.values()))
        b.graph.advance_to(b.next_deadline()); b.pump()
        self.assertEqual(b.tasks[task]['status'], 'fail')
        self.assertFalse(any(t['status'] in ('external','parallel','waiting','running') for t in b.tasks.values()))
        self.assertIsNone(b.next_deadline())
        external = next(e['task'] for e in b.graph.trace if e['kind']=='behavior_request')
        with self.assertRaises(ValueError): b.resolve(external, 'success')

    def test_any_success_prevents_later_sibling_side_effects(self):
        b, task = behavior('<Parallel successMode="Any" failureMode="All"><Success/>'
            '<Expression expressions="$changed = true"/></Parallel>', variables={'changed':False})
        self.assertEqual(b.tasks[task]['status'], 'success')
        self.assertFalse(b.variables['changed'])
        self.assertIn('canceled', [t['status'] for t in b.tasks.values()])

    def test_external_branch_outcome_wakes_parent(self):
        b, task = behavior('<Parallel successMode="All" failureMode="Any"><EngineWork/><Success/></Parallel>',
            contracts={'EngineWork':{'reason':'Explicit engine outcome required'}})
        external = next(n for n,t in b.tasks.items() if t['status']=='external')
        b.resolve(external, 'success')
        self.assertEqual(b.tasks[task]['status'], 'success')

    def test_unknown_policy_and_mixed_all_results_stay_unsupported(self):
        for body in ('<Parallel successMode="All" failureMode="Unknown"><Success/></Parallel>',
                     '<Parallel successMode="All" failureMode="All"><Success/><Fail/></Parallel>'):
            with self.assertRaises(Unsupported): behavior(body)


if __name__ == '__main__': unittest.main()
