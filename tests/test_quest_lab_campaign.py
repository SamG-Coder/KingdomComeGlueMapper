import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from quest_lab.campaign import campaign, compile_linked
from quest_lab.source_audit import ordered_records


class CampaignTests(unittest.TestCase):
    def test_forwarded_trigger_still_audits_its_original_source(self):
        from quest_lab.graph import Graph
        from quest_lab.source import Source
        for source, missing in [('go', False), ('missing.Event', True)]:
            blob = f'''<Database><Skald><Module Name="Child"><Ports>
              <Port Name="go" Direction="In" Type="trigger"/>
              <Port Name="changed" Direction="Out" Type="trigger"/></Ports><Nodes>
              <Output Name="out"><Edge From="{source}" To="changed"/></Output>
              </Nodes></Module><Project Name="Main"><Nodes><Child Name="child"/>
              <Timer Name="timer"><Constant Name="Duration" Value="0s"/>
              <Constant Name="TimeType" Value="GameTime"/>
              <Edge From="child.changed" To="SetRunning"/></Timer>
              </Nodes></Project></Skald></Database>'''.encode()
            graph = Graph(Source.blobs({'test.xml': blob}), 'Main')
            self.assertEqual(bool(graph.unsupported), missing)
            if missing:
                self.assertTrue(any(i['node'] == 'child.missing.Event' for i in graph.unsupported))
            else:
                graph.fire('child.go'); graph.advance_to(graph.now)
                self.assertEqual(graph.events['timer.OnFinished'], 1)

    def test_joint_compilation_resolves_dependency_without_pretending_completion(self):
        from test_campaign_quest_graph import model
        owner, lesson = model(), model()
        lesson['quest'] = 'lesson'
        lesson['tables']['quest']['rows'][0]['quest_name'] = 'lesson'
        owner['tables']['quest_objective']['rows'][1]['condition'] = "IsQuestCompleted('lesson')"
        _, isolated = compile_linked([owner])
        self.assertEqual(isolated['quests'][0]['converted_linked_conditions'], [])
        files, linked = compile_linked([owner, lesson])
        condition = linked['quests'][0]['converted_linked_conditions'][0]
        self.assertEqual(condition['objective'], 'first')
        self.assertIn('lesson', condition['dependencies'])
        self.assertIn(b'lesson.completed', files['QuestLabCampaign.xml'])
        self.assertFalse(linked['campaign_completed'])
        self.assertFalse(linked['native_runtime_verified'])
        from quest_lab.linked_checks import scenarios
        from quest_lab.runner import Runner
        from quest_lab.source import Source
        for scenario in scenarios(condition):
            result = Runner(scenario, source=Source.blobs(files)).run()
            self.assertEqual(result['status'], 'PASS', result['error'])
            self.assertTrue(result['assertions'])

    def test_linked_scenarios_do_not_simplify_compound_predicates(self):
        from quest_lab.linked_checks import scenarios
        with self.assertRaises(ValueError):
            scenarios(dict(quest='owner', objective='first',
                           expression="IsQuestCompleted('lesson') AND IsQuestStarted('other')"))

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
