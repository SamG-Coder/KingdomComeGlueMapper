from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E
import hashlib
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from campaign_dependencies import DependencyImporter
from quest_import import ast
from test_campaign_behavior import source, catalog
from quest_lab.controller_coverage import inspect


class CoverageTests(unittest.TestCase):
    def test_actor_link_conversion_is_distinct_from_traversal_execution(self):
        from test_campaign_link_types import LinkTypeTests, Scalar
        with tempfile.TemporaryDirectory() as tmp:
            importer=DependencyImporter({'link_types':LinkTypeTests().adapter(tmp),'ai_types':Scalar()})
            model=source('''<Root><GraphSearch Origin="&quot;owner&quot;">
                <LinkDataFilter tag="&quot;StoryActor&quot;" condition="&quot;$StoryActor == 'worker'&quot;"
                    Child="&quot;worker&quot;" prune="&quot;true&quot;"/></GraphSearch></Root>''')
            files, report=inspect([model],catalog(),importer)
            self.assertEqual(report['conversion']['whole_trees_emitted'],1)
            self.assertIn('AI/LinkTagDefinitions.xml',files)
            blob=next(data for path,data in files.items() if path.startswith('AI/') and path.endswith('/quests/q_test.xml'))
            node=E.fromstring(blob).find('.//LinkDataFilter')
            self.assertEqual(node.get('condition'),"$__link_imported_StoryActor == 'worker'")
            self.assertTrue(any('GraphSearch' in item['reason'] for c in report['controllers'] for item in c['findings']))
            self.assertFalse(report['native_runtime_verified'])

    def test_mailbox_dependencies_are_emitted_but_not_claimed_as_runtime_delivery(self):
        from test_campaign_ai_registration import setup, emitted
        with tempfile.TemporaryDirectory() as tmp:
            tables, native, importer, _ = setup(tmp)
            model=source('''<Root><Behavior><ProcessMessage Atomic="true" timeout="-1"
                timeType="GameTime" variable="message" senderInfo="sender" inbox="StoryInbox"
                condition="$__content.ready" answerVar=""><Success/></ProcessMessage></Behavior></Root>''')
            files, report=inspect([model],catalog(),importer)
            self.assertEqual(report['conversion']['whole_trees_emitted'],1)
            self.assertTrue(emitted(importer,'mailbox'))
            self.assertTrue(emitted(importer,'mailbox_filter'))
            self.assertIn('mailboxes',report['dependency_adapters'])
            self.assertTrue(all(p in files for p in importer.files))
            self.assertFalse(report['controllers'][0]['executed'])
            tables.rows['mailbox_rule'][0]['condition']='$ready == true'
            # A fresh importer must not reuse the preceding valid registration.
            broken=DependencyImporter(importer.adapters)
            _, blocked=inspect([model],catalog(),broken)
            self.assertEqual(blocked['conversion']['whole_trees_emitted'],0)
            self.assertFalse(emitted(broken,'mailbox'))

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
