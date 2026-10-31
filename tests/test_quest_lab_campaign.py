import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from quest_lab.campaign import campaign
from quest_lab.source_audit import ordered_records


class CampaignTests(unittest.TestCase):
    def test_priority_retains_every_record_and_rejects_unknown_names(self):
        rows = [dict(quest_name=n, group='main') for n in ('later', 'opening', 'middle')]
        self.assertEqual([r['quest_name'] for r in ordered_records(rows, ['opening'])],
                         ['opening', 'later', 'middle'])
        self.assertEqual(rows[0]['quest_name'], 'later')
        for names in (['missing'], ['opening', 'opening']):
            with self.assertRaises(ValueError): ordered_records(rows, names)

    def test_interruption_retains_completed_evidence_without_success_claim(self):
        def interrupted(game, progress, **kwargs):
            kwargs['checkpoint']([{'name': 'opening', 'status': 'DRY_RUN_PARTIAL'}], 2)
            raise InterruptedError('cancelled')
        with tempfile.TemporaryDirectory() as folder, patch('quest_lab.campaign.run', interrupted):
            with self.assertRaises(InterruptedError): campaign('source', folder)
            root = Path(folder)
            saved = json.loads((root / 'checkpoint.json').read_text())
            self.assertEqual(saved['completed_records'], 1)
            self.assertEqual(saved['total_records'], 2)
            self.assertEqual(saved['status'], 'IN_PROGRESS')
            self.assertFalse(saved['native_runtime_verified'])
            self.assertFalse((root / 'campaign.json').exists())

    def test_finished_audit_preserves_failures(self):
        result = dict(quests=[dict(name='opening', status='CONVERSION_BLOCKED')])
        with tempfile.TemporaryDirectory() as folder, patch('quest_lab.campaign.run', return_value=result):
            self.assertEqual(campaign('source', folder), result)
            saved = json.loads((Path(folder) / 'checkpoint.json').read_text())
            self.assertEqual(saved['status'], 'AUDIT_FINISHED')
            self.assertEqual(saved['quests'][0]['status'], 'CONVERSION_BLOCKED')
            with self.assertRaisesRegex(ValueError, 'already contains'):
                campaign('source', folder)
