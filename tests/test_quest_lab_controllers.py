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
