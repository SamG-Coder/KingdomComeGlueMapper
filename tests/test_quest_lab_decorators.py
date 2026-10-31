import unittest
from test_quest_lab_parallel import behavior, wait
from test_quest_lab_includes import graph
from test_campaign_behavior import source, catalog
from campaign_behavior import convert_behaviors
from quest_lab import Unsupported
from quest_lab.behavior import Behavior


class DecoratorTests(unittest.TestCase):
    def test_production_compiler_preserves_result_transformations(self):
        for wrapper, child, expected in [('SuppressFailure','Fail','success'),
                ('SuppressFailure','Success','success'),('Invertor','Fail','success'),('Invertor','Success','fail')]:
            model=source('<Root><Behavior><'+wrapper+'><'+child+'/></'+wrapper+'></Behavior></Root>')
            files,_,report=convert_behaviors([model],'Decorators',catalog())
            self.assertEqual(report['whole_trees_emitted'],1)
            b=Behavior('test',next(iter(files.values())),graph())
            self.assertFalse(b.unsupported)
            self.assertEqual(b.tasks[b.start('onUpdate')]['status'],expected)

    def test_waiting_child_and_reload_do_not_complete_wrapper_early(self):
        b,task=behavior('<SuppressFailure><Sequence>'+wait(2)+'<Fail/></Sequence></SuppressFailure>')
        self.assertEqual(b.tasks[task]['status'],'waiting')
        saved, saved_graph=b.snapshot(),b.graph.snapshot()
        for replay in range(2):
            if replay:b.graph.restore(saved_graph);b.restore(saved)
            b.graph.advance_to(b.next_deadline());b.pump()
            self.assertEqual(b.tasks[task]['status'],'success')

    def test_wrapper_cannot_suppress_unknown_operations_or_malformed_schema(self):
        for body in ('<SuppressFailure><Unknown/></SuppressFailure>',
                     '<SuppressFailure/>','<Invertor><Fail/><Success/></Invertor>',
                     '<Invertor mode="unknown"><Fail/></Invertor>'):
            with self.assertRaises(Unsupported):behavior(body)


if __name__ == '__main__': unittest.main()
