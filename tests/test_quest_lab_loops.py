from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from quest_lab import Unsupported, BudgetExceeded
from quest_lab.behavior import Behavior
from test_quest_lab_parallel import behavior, wait
from test_quest_lab_includes import graph


MAIL = '''<ProcessMessage inbox="'events'" timeout="-1" condition="" variable="$event"
 senderInfo="$sender" answerVar="$answer"><Expression expressions="$total = $total + $event"/></ProcessMessage>'''


class LoopTests(unittest.TestCase):
    def test_repeated_mailbox_wait_preserves_progress_on_reload(self):
        b, task = behavior('<Loop count="3">'+MAIL+'</Loop>', variables={'total':0})
        b.message('events', 2, {'id':'actor'})
        self.assertEqual(b.variables['total'], 2)
        self.assertEqual(b.tasks[task]['status'], 'message')
        saved, saved_graph = b.snapshot(), b.graph.snapshot()
        for replay in range(2):
            if replay: b.graph.restore(saved_graph); b.restore(saved)
            b.message('events', 3, {'id':'actor'})
            self.assertEqual(b.tasks[task]['status'], 'message')
            b.message('events', 4, {'id':'actor'})
            self.assertEqual(b.variables['total'], 9)
            self.assertEqual(b.tasks[task]['status'], 'success')

    def test_infinite_message_listener_cancels_when_parallel_work_finishes(self):
        b, task = behavior('<Parallel successMode="Any" failureMode="Any"><Loop count="-1">'
            +MAIL+'</Loop>'+wait(5)+'</Parallel>', variables={'total':0})
        b.message('events', 2, {})
        b.message('events', 7, {})
        self.assertEqual(b.variables['total'], 9)
        b.graph.advance_to(b.next_deadline()); b.pump()
        self.assertEqual(b.tasks[task]['status'], 'success')
        b.message('events', 100, {})
        self.assertEqual(b.variables['total'], 9)

    def test_unbounded_instantaneous_loop_hits_execution_budget(self):
        blob=b'<BehaviorTree name="main"><Root><Behavior><Loop count="-1"><Success/></Loop></Behavior></Root></BehaviorTree>'
        b=Behavior('busy',blob,graph(budget=50))
        with self.assertRaises(BudgetExceeded): b.start('main')

    def test_unknown_counts_schema_and_failure_are_not_assumed_success(self):
        for body in ('<Loop count="true"><Success/></Loop>', '<Loop count="-2"><Success/></Loop>',
                     '<Loop count="0"><Success/></Loop>', '<Loop count="1"><Fail/></Loop>',
                     '<Loop count="1"><Success/><Success/></Loop>'):
            with self.assertRaises(Unsupported): behavior(body)


if __name__ == '__main__': unittest.main()
