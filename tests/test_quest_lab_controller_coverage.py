from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E
import hashlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from campaign_dependencies import DependencyImporter
from quest_import import ast
from test_campaign_behavior import source, catalog
from quest_lab.controller_coverage import inspect


class CoverageTests(unittest.TestCase):
    def test_complete_trees_and_import_failures_are_distinguished_without_execution(self):
        model=source('<Root><Behavior><Loop count="1"><Success/></Loop></Behavior></Root>')
        model['behavior_documents']['libs/ai/quests/q_test.xml']['trees']['blocked'] = dict(
            variables=[],root=ast(E.fromstring('<Root><Behavior><NotConverted/></Behavior></Root>')))
        files, report=inspect([model],catalog(),DependencyImporter({}))
        self.assertEqual(report['conversion']['whole_trees_emitted'],1)
        self.assertEqual(report['conversion']['blocked_trees'],1)
        self.assertEqual(report['controllers'][0]['trees'],['onUpdate'])
        self.assertFalse(report['controllers'][0]['executed'])
        self.assertEqual(report['controllers'][0]['findings'],[])
        self.assertEqual(report['status'],'STRUCTURAL_AUDIT_ONLY')
        self.assertFalse(report['campaign_completed'])
        self.assertTrue(report['conversion_blockers'])
        self.assertEqual(report['files'],{p:hashlib.sha256(b).hexdigest() for p,b in files.items()})

    def test_native_convertible_operation_can_still_lack_interpreter_support(self):
        model=source('<Root><Behavior><LoopUntil until="Success" attemptCount="3"><Success/></LoopUntil></Behavior></Root>')
        _, report=inspect([model],catalog(),DependencyImporter({}))
        self.assertEqual(report['conversion']['whole_trees_emitted'],1)
        self.assertTrue(any('LoopUntil' in f['reason'] for c in report['controllers'] for f in c['findings']))


if __name__ == '__main__': unittest.main()
