import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_campaign_behavior import source
from quest_lab.controllers import audit


class ControllerAuditTests(unittest.TestCase):
    def test_recursive_dependencies_are_deduplicated_and_cycles_terminate(self):
        first = source('<Root><Behavior><SetQuest quest="later" function="StartQuest"/></Behavior></Root>')
        later = source('<Root><Behavior><Sequence><SetQuest quest="q_test" function="StartQuest"/>'
            '<SetQuest quest="absent" function="StartQuest"/>'
            '<SetQuest quest="$dynamic" function="StartQuest"/></Sequence></Behavior></Root>', name='later')
        with tempfile.TemporaryDirectory() as folder, \
                patch('quest_lab.controllers.RetailSourceReader'), \
                patch('quest_lab.controllers.SourceTables') as tables, \
                patch('quest_lab.controllers.controller_definition', return_value=None), \
                patch('quest_lab.controllers.import_quest', side_effect=[(first, {}), (later, {})]) as imports:
            def lookup(table, field, name):
                if name=='absent': raise KeyError(name)
                return {'quest_name':name}
            tables.return_value.row.side_effect=lookup
            report=audit('game',['q_test'],Path(folder)/'audit',follow_dependencies=True)
            self.assertEqual([r['quest'] for r in report['quests']], ['q_test','later','absent'])
            self.assertEqual(imports.call_count, 2)
            self.assertEqual(report['missing_quest_dependencies'], ['absent'])
            self.assertEqual(report['quests'][-1]['status'],'CONVERSION_BLOCKED')
            self.assertTrue(report['follow_dependencies'])
            self.assertTrue(any(d['dynamic'] for d in report['quests'][1]['dependencies']))

    def test_reports_missing_dependency_and_keeps_failed_quest_out_of_compiled_set(self):
        model = source('''<Root><Behavior><SetQuest quest="later" function="StartQuest"/>
                       </Behavior></Root>''')
        with tempfile.TemporaryDirectory() as folder, \
                patch('quest_lab.controllers.RetailSourceReader'), \
                patch('quest_lab.controllers.SourceTables'), \
                patch('quest_lab.controllers.controller_definition', return_value=None), \
                patch('quest_lab.controllers.import_quest', side_effect=[(model, {}), ValueError('bad source')]):
            output = Path(folder)/'audit'
            report = audit('game', ['q_test','broken'], output)
            self.assertEqual(report['missing_quest_dependencies'], ['later'])
            self.assertEqual(report['quests'][1]['status'], 'CONVERSION_BLOCKED')
            self.assertFalse(report['campaign_completed'])
            self.assertEqual(len(report['controller_operations']['unresolved']), 1)
            for name, digest in report['compiled_files'].items():
                self.assertEqual(hashlib.sha256((output/'compiled'/name).read_bytes()).hexdigest(), digest)
            self.assertEqual(json.loads((output/'checkpoint.json').read_text())['status'], 'AUDIT_FINISHED')
            with self.assertRaisesRegex(ValueError, 'fresh'):
                audit('game', ['q_test'], output)

    def test_cancelled_import_leaves_completed_records_checkpointed(self):
        model = source('<Root><Behavior><Success/></Behavior></Root>')
        with tempfile.TemporaryDirectory() as folder, \
                patch('quest_lab.controllers.RetailSourceReader'), \
                patch('quest_lab.controllers.SourceTables'), \
                patch('quest_lab.controllers.controller_definition', return_value=None), \
                patch('quest_lab.controllers.import_quest', side_effect=[(model, {}), InterruptedError('cancel')]):
            output = Path(folder)/'audit'
            with self.assertRaises(InterruptedError): audit('game',['q_test','later'],output)
            checkpoint=json.loads((output/'checkpoint.json').read_text())
            self.assertEqual(checkpoint['status'],'IN_PROGRESS')
            self.assertEqual(len(checkpoint['quests']),1)
            self.assertFalse((output/'report.json').exists())
