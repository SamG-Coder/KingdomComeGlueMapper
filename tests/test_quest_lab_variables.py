import json
import unittest

from test_campaign_behavior import source, catalog
from test_quest_lab_includes import graph
from test_quest_lab_parallel import wait
from test_quest_lab_enums import registry
from campaign_behavior import convert_behaviors
from quest_lab import Unsupported
from quest_lab.behavior import Behavior


def controller(body, declarations, inputs=None, files=None):
    blob=('<BehaviorTree name="main"><Variables>'+declarations+'</Variables><Root><Behavior>'+
          body+'</Behavior></Root></BehaviorTree>').encode()
    return Behavior('locals',blob,graph(files),variables=inputs)


class VariableTests(unittest.TestCase):
    def test_compiled_default_is_initialized_and_isolated_between_invocations(self):
        model=source('<Root><Behavior><Sequence><Expression expressions="$hasStarted = !$hasStarted"/>'+
                     wait(1)+'<Success/></Sequence></Behavior></Root>')
        files, _, report=convert_behaviors([model],'Locals',catalog())
        self.assertEqual(report['whole_trees_emitted'],1)
        b=Behavior('test',next(iter(files.values())),graph())
        first=b.start('onUpdate'); second=b.start('onUpdate')
        self.assertEqual(b.tasks[first]['locals'],{'hasStarted':True})
        self.assertEqual(b.tasks[second]['locals'],{'hasStarted':True})
        self.assertEqual(b.variables,{})

    def test_parallel_branches_share_scope_and_json_reload_preserves_it(self):
        b=controller('<Parallel successMode="All" failureMode="Any"><Sequence>'+
            wait(1)+'<Expression expressions="$count = $count + 1"/></Sequence><Sequence>'+
            wait(2)+'<Expression expressions="$count = $count + 2"/></Sequence></Parallel>',
            '<Variable name="count" type="_int" values="0"/>')
        task=b.start('main')
        saved=json.loads(json.dumps(b.snapshot())); saved_graph=b.graph.snapshot()
        for replay in range(2):
            if replay: b.graph.restore(saved_graph); b.restore(saved)
            while b.next_deadline() is not None:
                b.graph.advance_to(b.next_deadline()); b.pump()
            self.assertEqual(b.tasks[task]['status'],'success')
            self.assertEqual(b.tasks[task]['locals']['count'],3)
            self.assertTrue(all(t['scope']==task for t in b.tasks.values()))

    def test_explicit_inputs_copy_records_and_empty_collections_are_local(self):
        b=controller('<Expression expressions="$record.value = 4, $map[\'x\'] = 3"/>',
            '<Variable name="record" type="engineRecord"/><Variable name="map" type="_int" form="associative"/>'
            '<Variable name="array" type="_int" form="array"/>',{'record':{'value':1}})
        task=b.start('main')
        self.assertEqual(b.variables,{'record':{'value':1}})
        self.assertEqual(b.tasks[task]['locals'],{'record':{'value':4},'map':{'x':3},'array':[]})

    def test_uninitialized_outputs_bind_locally_and_unbound_reads_fail(self):
        decl='<Variable name="result" type="_int"/>'
        b=controller('<Expression expressions="$result = 3"/>',decl)
        task=b.start('main')
        self.assertEqual(b.tasks[task]['locals'],{'result':3})
        self.assertEqual(b.variables,{})
        b=controller('<Expression expressions="$other = $result"/>',decl)
        with self.assertRaises(Unsupported): b.start('main')

    def test_enum_default_and_invalid_declarations(self):
        b=controller('<Success/>','<Variable name="state" type="enum:Status" values="$enum:Status.Done"/>',
                     files={'enums.xml':registry()})
        task=b.start('main'); self.assertEqual(b.tasks[task]['locals'],{'state':7})
        for decl in ('<Variable name="x" type="_bool" values="1"/>',
                     '<Variable name="x" type="_int" values="2147483648"/>',
                     '<Variable name="x" type="record" values="field(1)"/>',
                     '<Variable name="x" type="_int" form="custom_associative"/>',
                     '<Variable name="x" type="_int"/><Variable name="x" type="_int"/>'):
            with self.subTest(decl=decl), self.assertRaises(Unsupported):
                controller('<Success/>',decl).start('main')


if __name__ == '__main__': unittest.main()
