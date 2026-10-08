from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_ai_registration import (NativeAIRegistry, MailboxAdapter, BrainAdapter,
    SubbrainAdapter, ConvertedTreeAdapter, SmartEntityAdapter, controller_definition,
    tree_identity, quest_controller_brains)
from campaign_dependency_adapters import EntityAdapter
from campaign_entity_links import native_guid
from campaign_trigger_areas import write_areas
from quest_import import ast
from campaign_ai_types import AITypeSource, AIEnumAdapter, AIStructureAdapter
from campaign_dependencies import DependencyImporter
from campaign_behavior import convert_behaviors
from test_campaign_behavior import source, catalog


class Tables:
    def __init__(self):
        self.rows = {
            'brain': [dict(brain_id='source-brain', brain_name='authored_controller')],
            'brain2mailbox': [dict(brain_id='source-brain', mailbox_id='source-mail', priority='3')],
            'mailbox': [dict(mailbox_id='source-mail', mailbox_name='StoryInbox', message_limit='4', on_accept='2')],
            'mailbox_filter': [dict(mailbox_filter_id='source-filter', mailbox_id='source-mail', type='storyEvent')],
            'mailbox_rule': [dict(mailbox_rule_id='rule', mailbox_filter_id='source-filter', condition='*', custom_data_type='', priority='0')],
            'brain2subbrain': [dict(brain_id='source-brain', subbrain_id='source-sub', priority='9')],
            'subbrain': [dict(subbrain_id='source-sub', subbrain_name='authored_update', subbrain_type='4', always_active='True', timeout='0')],
            'subbrain_smart_object': [dict(subbrain_id='source-sub', file_name='quests/q_other.xml', on_update_tree='onUpdate', on_release_tree='', on_request_tree='', explicit_initialization='', system_variables='')],
            'subbrain_behaviour_tree': [],
            'brain_variable': [dict(brain_id='source-brain', brain_variable_name='history', type='common:wuid,storyEvent', ai_variable_form_id='3', ai_variable_sync_id='0', is_persistent='True', init_value='')],
            'mailbox_action_type': [dict(mailbox_action_type_id='2', mailbox_action_type_name='consume')],
            'subbrain_type': [dict(subbrain_type_id='4', subbrain_type_name='SmartObject')],
            'ai_variable_form': [dict(ai_variable_form_id='3', ai_variable_form_name='custom_associative')],
            'ai_variable_sync': [dict(ai_variable_sync_id='0', ai_variable_sync_name='no_sync')],
            'so_smart_object': [dict(so_smart_object_id='source-so', so_smart_object_name='not_the_quest_name', brain_id='source-brain', body_id='plain-body', priority='False')],
            'so_smart_object2so_behaviour_tag': [dict(so_smart_object_id='source-so', so_behaviour_tag_id='callback', priority='0')],
            'so_behaviour_tag': [dict(so_behaviour_tag_id='callback', so_behaviour_tag_name='opening_callback', tree_file='final/callbacks.xml', tree_name='AfterOpening', initial_state='1', max_instances='1', on_fail_action='1', on_success_action='1', condition_id='')],
            'so_behaviour_tag2mailbox': [dict(so_behaviour_tag_id='callback', mailbox_id='source-mail', priority='4')],
            'so_behaviour_action': [dict(so_behaviour_action_id='1', so_behaviour_action_name='drop')],
            'so_behaviour_state': [dict(so_behaviour_state_id='1', so_behaviour_state_name='disabled')],
            'ai_body': [dict(ai_body_id='plain-body', ai_body_name='plain')],
            'ai_body2brain_sensor': [], 'ai_body2npc_reference_point': [],
        }

    def get(self, table):
        return dict(rows=self.rows[table.removeprefix('ai/')], provenance=[dict(archive='retail-fixture')])

    def row(self, table, field, value):
        rows = [r for r in self.get(table)['rows'] if r[field].lower() == value.lower()]
        if len(rows) != 1: raise ValueError('missing fixture ' + table + ':' + value)
        return rows[0]


def setup(tmp, operation='Success'):
    tables = Tables()
    # Native enumeration IDs deliberately differ: conversion must use the
    # declared meanings, not reuse old numeric constants.
    native = {name: dict(container=name+'s', attributes=dict(version='1'), row_tag=name,
                         rows=rows) for name, rows in tables.rows.items()}
    for name in ('subbrain_type', 'mailbox_action_type', 'ai_variable_form', 'ai_variable_sync'):
        native[name]['rows'] = [dict(row, **{name+'_id': str(int(row[name+'_id'])+20)}) for row in native[name]['rows']]
    native = NativeAIRegistry(native, 'TestCampaign')
    types = AITypeSource(E.fromstring('<TypeDefinitions version="1"><Type name="storyEvent"><Member name="ready" type="bool">false</Member></Type></TypeDefinitions>'), [], 'TestCampaign', tmp)
    importer = DependencyImporter(dict(ai_types=AIStructureAdapter(types), ai_enums=AIEnumAdapter(types),
                                       mailboxes=MailboxAdapter(tables, native)))
    model = source('<Root><Behavior><'+operation+'/></Behavior></Root>', name='q_other')
    model['tables']['quest']['rows'][0]['smart_object'] = 'not_the_quest_name'
    files, _, report = convert_behaviors([model], 'TestBehaviors', catalog(), importer=importer)
    importer.adapters.update(behavior_trees=ConvertedTreeAdapter(report, files),
        subbrains=SubbrainAdapter(tables, native), brains=BrainAdapter(tables, native, types))
    return tables, native, importer, model


def emitted(importer, table):
    return [row for name, data in importer.files.items() if name.startswith('Libs/Tables/ai/'+table+'__')
            for row in E.fromstring(data).findall('./'+table+'s/'+table)]


class AIRegistrationTests(unittest.TestCase):
    def test_controller_imports_authored_brain_trees_mailboxes_and_persistent_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            tables, native, importer, model = setup(tmp)
            requests = quest_controller_brains([model], tables, importer)
            self.assertIsNotNone(requests[0]['target_brain'])
            self.assertEqual(requests[0]['controller'], 'not_the_quest_name')
            self.assertEqual(emitted(importer, 'brain2mailbox')[0].get('priority'), '3')
            self.assertEqual(emitted(importer, 'brain2subbrain')[0].get('priority'), '9')
            self.assertEqual(emitted(importer, 'mailbox')[0].get('on_accept'), '22')
            self.assertEqual(emitted(importer, 'mailbox')[0].get('message_limit'), '4')
            self.assertEqual(emitted(importer, 'subbrain')[0].get('subbrain_type'), '24')
            self.assertEqual(emitted(importer, 'subbrain')[0].get('always_active'), 'true')
            entry = emitted(importer, 'subbrain_smart_object')[0]
            self.assertEqual(entry.get('file_name'), 'testbehaviors/q_other/quests/q_other.xml')
            self.assertEqual(entry.get('on_update_tree'), 'onUpdate')
            variable = emitted(importer, 'brain_variable')[0]
            self.assertEqual(variable.get('type'), 'wuid,testcampaign:storyEvent')
            self.assertEqual(variable.get('is_persistent'), 'true')
            self.assertEqual(variable.get('ai_variable_form_id'), '23')

    def test_unconverted_entry_tree_does_not_publish_empty_brain(self):
        with tempfile.TemporaryDirectory() as tmp:
            tables, native, importer, model = setup(tmp, 'UnsupportedOpeningAction')
            requests = quest_controller_brains([model], tables, importer)
            self.assertIsNone(requests[0]['target_brain'])
            self.assertFalse(emitted(importer, 'brain'))
            self.assertFalse(emitted(importer, 'subbrain'))
            self.assertTrue(emitted(importer, 'mailbox'))
            self.assertTrue(any('UnsupportedOpeningAction' in j.get('error','') for j in importer.jobs.values()))

    def test_conditional_mailbox_filter_is_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            tables, native, importer, model = setup(tmp)
            tables.rows['mailbox_rule'][0]['condition'] = '$ready == true'
            self.assertIsNone(importer.ensure('mailboxes', 'StoryInbox'))
            self.assertFalse(emitted(importer, 'mailbox'))

    def test_send_and_receive_use_the_same_converted_message_and_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            tables, native, importer, model = setup(tmp)
            model = source('''<Root><Sequence>
<InstantSendMessageToNPC target="this.id" type="storyEvent" values="ready(true)"/>
<ProcessMessage Atomic="true" timeout="-1" timeType="GameTime" variable="message" senderInfo="sender" inbox="StoryInbox" condition="$__content.ready" answerVar="" ><Success/></ProcessMessage>
</Sequence></Root>''')
            files, _, report = convert_behaviors([model], 'MessageTest', catalog(), importer=importer)
            self.assertEqual(report['whole_trees_emitted'], 1)
            root = E.fromstring(next(iter(files.values())))
            sent = root.find('.//InstantSendMessageToNPC')
            received = root.find('.//ProcessMessage')
            self.assertEqual(sent.get('type'), "'testcampaign:storyEvent'")
            self.assertEqual(sent.get('target'), '$this.id')
            self.assertEqual(received.get('inbox'), "'testcampaign_StoryInbox'")
            self.assertEqual(received.get('timeout'), "'-1'")
            self.assertEqual(emitted(importer, 'mailbox_filter')[0].get('type'), 'testcampaign:storyEvent')
            self.assertEqual(emitted(importer, 'mailbox')[0].get('mailbox_name'), 'testcampaign_StoryInbox')

    def test_database_callbacks_are_discovered_registered_and_placed_with_original_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            tables, native, importer, model = setup(tmp)
            definition = controller_definition(tables, model['tables']['quest']['rows'][0])
            self.assertEqual({p['file'] for p in definition['entry_points']}, {'quests/q_other.xml', 'final/callbacks.xml'})
            model['behavior_documents']['libs/ai/final/callbacks.xml'] = dict(trees={
                'AfterOpening':dict(variables=[],root=ast(E.fromstring('<Root><Success/></Root>'))),
                'afteropening':dict(variables=[],root=ast(E.fromstring('<Root><Fail/></Root>')))})
            files, _, report = convert_behaviors([model], 'TestBehaviors', catalog(), importer=importer)
            importer.adapters['behavior_trees'] = ConvertedTreeAdapter(report, files)
            importer.adapters['smart_entities'] = SmartEntityAdapter(tables, native)
            identity='q_other|source-so'
            target=importer.ensure('smart_entities',identity)
            self.assertIsNotNone(target)
            document=next(E.fromstring(data) for name,data in importer.files.items() if name.startswith('Libs/Tables/ai/smartEntity/'))
            behavior=document.find('.//SmartBehaviorTemplate')
            self.assertEqual(behavior.get('Name'),'opening_callback')
            self.assertEqual(behavior.get('InitialState'),'Disabled')
            self.assertEqual(behavior.find('TreeLocation').get('TreeName'),'AfterOpening')
            self.assertEqual(behavior.find('Inboxes/InboxTemplate').get('Priority'),'4')
            # Case-distinct authored callbacks must not collapse in the generic
            # case-insensitive dependency registry.
            other=importer.ensure('behavior_trees',tree_identity('q_other','final/callbacks.xml','afteropening'))
            self.assertTrue(other.endswith('|afteropening'))
            entity=E.fromstring('<Entity EntityClass="QuestObject" EntityGuid="0000000000001234" Name="not_the_quest_name" Pos="1,2,3"><Properties bSaved_by_game="1" sWH_AI_EntityCategory=""><Script Misc=""/></Properties></Entity>')
            record=dict(source='objects_mission0.xml',attributes=dict(entity.attrib),entity=ast(entity))
            importer.adapters['entities']=EntityAdapter(dict(entities={'controller':record}), E.fromstring('<Root/>'),
                write_areas([],1),1,controllers={'not_the_quest_name':[identity]})
            guid=native_guid(0x1234)
            self.assertEqual(importer.ensure('entities',guid),guid)
            placed=E.fromstring(importer.files['world/entities/'+guid+'.xml'])
            self.assertEqual(placed.get('EntityClass'),'SmartObjectHolder')
            self.assertEqual(placed.get('Name'),'not_the_quest_name')
            self.assertEqual(placed.get('Pos'),'1,2,3')
            self.assertEqual(placed.find('Properties').get('guidSmartObjectType'),target)


if __name__ == '__main__': unittest.main()
