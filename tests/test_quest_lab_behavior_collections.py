import json
import unittest

from test_campaign_behavior import source, catalog
from test_quest_lab_includes import graph
from test_quest_lab_variables import controller
from test_quest_lab_parallel import wait
from campaign_behavior import convert_behaviors
from quest_lab import Unsupported
from quest_lab.behavior import Behavior


class CollectionTests(unittest.TestCase):
    def test_compiled_array_append_and_size_use_declared_locals(self):
        model=source('''<Root><Behavior><Sequence>
            <VarOperation operation="PushBack" varName="entries" targetVarName="" argument="(17)"/>
            <VarOperation operation="PushBack" varName="entries" targetVarName="" argument="(23)"/>
            <VarOperation operation="Size" varName="entries" targetVarName="count" argument=""/>
            </Sequence></Behavior></Root>''')
        model['behavior_documents']['libs/ai/quests/q_test.xml']['trees']['onUpdate']['variables'].extend([
            dict(name='entries',type='_int',form='array',values=''),
            dict(name='count',type='_int',form='single',values='')])
        files, _, report=convert_behaviors([model],'Collections',catalog())
        self.assertEqual(report['whole_trees_emitted'],1)
        b=Behavior('arrays',next(iter(files.values())),graph())
        task=b.start('onUpdate')
        self.assertFalse(b.unsupported)
        self.assertEqual(b.tasks[task]['locals']['entries'],[17,23])
        self.assertEqual(b.tasks[task]['locals']['count'],2)
        self.assertEqual(b.variables,{})

    def test_append_copies_record_and_waiting_task_replays(self):
        b=controller('<Sequence><VarOperation operation="PushBack" varName="$entries" argument="($record)"/>'+
            '<Expression expressions="$record.value = 9"/>'+wait(1)+
            '<VarOperation operation="Size" varName="$entries" targetVarName="$size"/></Sequence>',
            '<Variable name="entries" type="record" form="array"/>',{'record':{'value':3}})
        task=b.start('main'); saved=json.loads(json.dumps(b.snapshot())); saved_graph=b.graph.snapshot()
        for replay in range(2):
            if replay:b.graph.restore(saved_graph);b.restore(saved)
            b.graph.advance_to(b.next_deadline());b.pump()
            self.assertEqual(b.tasks[task]['locals']['entries'],[{'value':3}])
            self.assertEqual(b.variables['size'],1)
            self.assertEqual(b.variables['record'],{'value':9})

    def test_size_accepts_empty_array_and_string_map(self):
        for collection in ([],{'first':1,'second':2}):
            b=controller('<VarOperation operation="Size" varName="$entries" targetVarName="$out.value"/>',
                         '',{'entries':collection,'out':{'value':-1}})
            b.start('main');self.assertEqual(b.variables['out']['value'],len(collection))

    def test_invalid_operations_do_not_mutate_collection(self):
        for attrs,child in (
            ('operation="PushBack" argument="1" targetVarName="$out"',''),
            ('operation="PushBack" argument="$missing"',''),
            ('operation="Size" argument="1" targetVarName="$out"',''),
            ('operation="Size" targetVarName="$missing.field"',''),
            ('operation="Erase" argument="0"',''),
            ('operation="PushBack" argument="1"','<Success/>')):
            b=controller('<VarOperation varName="$entries" '+attrs+'>'+child+'</VarOperation>',
                         '',{'entries':[7]})
            with self.subTest(attrs=attrs),self.assertRaises(Unsupported):b.start('main')
            self.assertEqual(b.variables,{'entries':[7]})


if __name__ == '__main__':unittest.main()
