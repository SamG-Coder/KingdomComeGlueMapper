import copy
import json
import unittest

from test_campaign_behavior import source, catalog
from test_quest_lab_includes import graph
from campaign_behavior import convert_behaviors
from quest_lab import Unsupported
from quest_lab.behavior import Behavior
from quest_lab.expressions import assignments


class AssignmentTests(unittest.TestCase):
    def test_compiled_nested_record_and_map_assignments(self):
        model=source('''<Root><Behavior><Expression expressions="$request.ready = true,
            $request.target.id = $actor.id, $actors['teacher'] = $actor.id,
            $request.ready = $actors['teacher'] == 17"/></Behavior></Root>''')
        files, _, report=convert_behaviors([model],'Assignments',catalog())
        self.assertEqual(report['whole_trees_emitted'],1)
        b=Behavior('worker',next(iter(files.values())),graph(),variables={
            'request':{'ready':False,'target':{'id':0}},'actor':{'id':17},'actors':{}})
        task=b.start('onUpdate')
        self.assertEqual(b.tasks[task]['status'],'success')
        self.assertEqual(b.variables['request'],{'ready':True,'target':{'id':17}})
        self.assertEqual(b.variables['actors'],{'teacher':17})

    def test_array_and_enum_indices_and_value_copy(self):
        values={'entries':[{'value':0},{'value':0}], 'index':1,'source':{'value':8},'states':[0,0]}
        assignments('$entries[$index] = $source, $source.value = 9, '
                    '$states[$enum:State.ready] = $entries[1].value',values,
                    lambda name,member: 1 if (name,member)==('State','ready') else None)
        self.assertEqual(values['entries'],[{'value':0},{'value':8}])
        self.assertEqual(values['source']['value'],9)
        self.assertEqual(values['states'],[0,8])

    def test_invalid_targets_do_not_mutate_values(self):
        initial={'record':{'value':1},'array':[2],'map':{},'index':True}
        for target in ('$missing.value','$record.missing','$array[-1]','$array[1]',
                       '$array[$index]','$record.value.real','$map[true]','$map[1]','fn()','1',
                       '$map[unknown()]','$array[0:1]'):
            with self.subTest(target=target):
                values=copy.deepcopy(initial)
                with self.assertRaises(Unsupported): assignments(target+' = 3',values)
                self.assertEqual(values,initial)

    def test_literal_delimiters_and_prior_assignment_visibility(self):
        values={'record':{'text':''},'map':{}}
        assignments("$record.text = 'x=y,z', $map['a=b,c'] = $record.text",values)
        self.assertEqual(values['map'],{'a=b,c':'x=y,z'})

    def test_nested_changes_survive_json_snapshot_and_replay(self):
        from test_quest_lab_parallel import behavior, wait
        b, task=behavior('<Sequence><Expression expressions="$record.count = 2"/>'+wait(1)+
            '<Expression expressions="$record.count = $record.count + 1, '
            '$map[\'done\'] = $record.count"/></Sequence>',variables={'record':{'count':0},'map':{}})
        saved=json.loads(json.dumps(b.snapshot()))
        saved_graph=b.graph.snapshot()
        for replay in range(2):
            if replay: b.graph.restore(saved_graph); b.restore(saved)
            b.graph.advance_to(b.next_deadline()); b.pump()
            self.assertEqual(b.tasks[task]['status'],'success')
            self.assertEqual(b.variables,{'record':{'count':3},'map':{'done':3}})


if __name__ == '__main__': unittest.main()
