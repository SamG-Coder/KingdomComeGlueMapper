from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_campaign_ai_types import importer_at
from test_campaign_behavior import source, catalog
from test_quest_lab_includes import graph
from campaign_behavior import convert_behaviors
from quest_lab import Unsupported
from quest_lab.behavior import Behavior
from quest_lab.expressions import evaluate
from quest_lab.source import Source


def registry(value='7'):
    return ('<database><Enums version="1"><Enum Name="Status">'
            '<Value Name="Done" Value="'+value+'"/></Enum></Enums></database>').encode()


class EnumTests(unittest.TestCase):
    def test_compiled_namespaced_enum_drives_condition_and_assignment(self):
        with tempfile.TemporaryDirectory() as folder:
            _, importer = importer_at(Path(folder))
            model = source('''<Root><Behavior><IfCondition condition="$state == $enum:customState.active" failOnCondition="true">
              <Expression expressions="$state = $enum:customState.done"/></IfCondition></Behavior></Root>''')
            files, _, report = convert_behaviors([model], 'Enums', catalog(), importer=importer)
            self.assertEqual(report['whole_trees_emitted'], 1)
            enum_files = {p:b.read_bytes() if isinstance(b,Path) else b for p,b in importer.files.items()}
            g=graph(enum_files)
            b=Behavior('controller',next(iter(files.values())),g,variables={'state':4})
            self.assertEqual(b.tasks[b.start('onUpdate')]['status'],'success')
            self.assertEqual(b.variables['state'],5)
            self.assertEqual(b.tasks[b.start('onUpdate')]['status'],'fail')

    def test_unknown_ambiguous_and_malformed_enums_stay_explicit(self):
        for files, name, member in [({},'Status','Done'),({'enum.xml':registry()},'Status','Missing'),
            ({'one.xml':registry(),'two.xml':registry()},'Status','Done'),
            ({'enum.xml':registry('invalid')},'Status','Done'),
            ({'enum.xml':registry().replace(b'</Enum>',b'<Value Name="Done" Value="8"/></Enum>')},'Status','Done')]:
            with self.assertRaises(Unsupported): Source.blobs(files).enum_value(name,member)

    def test_quoted_enum_text_is_preserved_and_missing_resolver_is_not_guessed(self):
        self.assertEqual(evaluate("'$enum:Status.Done'",{}),'$enum:Status.Done')
        s=Source.blobs({'enum.xml':registry('-3')})
        self.assertTrue(evaluate('$enum:Status.Done == -3',{},s.enum_value))
        with self.assertRaises(Unsupported): evaluate('$enum:Status.Done',{})

    def test_changed_enum_registry_invalidates_behavior_snapshot(self):
        blob=b'<BehaviorTree name="main"><Root><Behavior><Success/></Behavior></Root></BehaviorTree>'
        before=Behavior('x',blob,graph({'enum.xml':registry()})).snapshot()
        after=Behavior('x',blob,graph({'enum.xml':registry('8')}))
        with self.assertRaisesRegex(ValueError,'snapshot mismatch'): after.restore(before)


if __name__ == '__main__': unittest.main()
