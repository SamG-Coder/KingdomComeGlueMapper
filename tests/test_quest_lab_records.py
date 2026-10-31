from pathlib import Path
import tempfile
import unittest

from test_campaign_behavior import source, catalog
from test_campaign_ai_types import importer_at
from test_quest_lab_variables import controller
from test_quest_lab_includes import graph
from campaign_behavior import convert_behaviors
from quest_lab import Unsupported
from quest_lab.behavior import Behavior


TYPES=b'''<database><Types version="1"><Type Name="story">
<Type Name="Point"><Member Name="x" Type="int" InitialValue="2"/></Type>
<Type Name="Request"><Member Name="enabled" Type="bool" InitialValue="true"/>
<Member Name="label" Type="string" InitialValue="literal $enum:Unknown.Value"/>
<Member Name="point" Type="story:Point"/></Type></Type></Types></database>'''


class RecordTests(unittest.TestCase):
    def test_compiler_imported_record_default_and_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, importer=importer_at(Path(tmp))
            model=source('<Root><Behavior><Expression expressions="$result = $event.enabled"/></Behavior></Root>')
            model['behavior_documents']['libs/ai/quests/q_test.xml']['trees']['onUpdate']['variables'].append(
                dict(name='event',type='customMessage',values='enabled(false)',form='single'))
            files, _, report=convert_behaviors([model],'RecordTest',catalog(),importer=importer)
            self.assertEqual(report['whole_trees_emitted'],1)
            dependencies={p:b.read_bytes() if isinstance(b,Path) else b for p,b in importer.files.items()}
            b=Behavior('record',next(iter(files.values())),graph(dependencies))
            task=b.start('onUpdate')
            self.assertEqual(b.tasks[task]['locals']['event'],{'enabled':False})
            self.assertFalse(b.variables['result'])

    def test_nested_defaults_raw_strings_and_instance_isolation(self):
        b=controller('<Success/>','<Variable name="request" type="story:Request"/>',files={'types.xml':TYPES})
        first=b.start('main'); second=b.start('main')
        expected={'enabled':True,'label':'literal $enum:Unknown.Value','point':{'x':2}}
        self.assertEqual(b.tasks[first]['locals']['request'],expected)
        b.tasks[first]['locals']['request']['point']['x']=9
        self.assertEqual(b.tasks[second]['locals']['request'],expected)
        b=controller('<Success/>',
            '<Variable name="request" type="story:Request" values="point(x(5)),label(\'override\')"/>',
            files={'types.xml':TYPES})
        task=b.start('main')
        self.assertEqual(b.tasks[task]['locals']['request'],dict(expected,label='override',point={'x':5}))

    def test_incomplete_defaults_remain_unbound_and_bad_schemas_fail(self):
        incomplete=TYPES.replace(b' InitialValue="2"',b'')
        b=controller('<Expression expressions="$result = $request.point.x"/>',
            '<Variable name="request" type="story:Request"/>',files={'types.xml':incomplete})
        with self.assertRaises(Unsupported):b.start('main')
        for payload in (TYPES.replace(b'Type="int"',b'Type="story:Point"'),
                        TYPES.replace(b'version="1"',b'version="2"'),
                        TYPES.replace(b'InitialValue="2"',b'InitialValue="true"')):
            b=controller('<Success/>','<Variable name="request" type="story:Request"/>',files={'types.xml':payload})
            with self.assertRaises(Unsupported):b.start('main')
        for values in ('unknown(1)','enabled(false),enabled(true)','point(x(true))'):
            b=controller('<Success/>','<Variable name="request" type="story:Request" values="'+values+'"/>',
                         files={'types.xml':TYPES})
            with self.assertRaises(Unsupported):b.start('main')

    def test_changed_type_defaults_reject_old_snapshot(self):
        decl='<Variable name="request" type="story:Request"/>'
        b=controller('<Success/>',decl,files={'types.xml':TYPES}); b.start('main')
        changed=controller('<Success/>',decl,files={'types.xml':TYPES.replace(b'InitialValue="2"',b'InitialValue="3"')})
        with self.assertRaises(ValueError): changed.restore(b.snapshot())


if __name__ == '__main__': unittest.main()
