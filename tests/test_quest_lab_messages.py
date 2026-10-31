import copy
import json
from pathlib import Path
import sys
import unittest
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from quest_lab import Unsupported
from quest_lab.graph import Graph
from quest_lab.runner import Runner
from quest_lab.source import Source


GRAPH = b'''<Database><Skald><Module Name="Messages"><Ports>
<Port Name="first" Direction="In" Type="trigger"/><Port Name="second" Direction="In" Type="trigger"/>
<Port Name="receiver" Direction="In" Type="string"/><Port Name="delta" Direction="In" Type="int"/>
<Port Name="ack" Direction="In" Type="trigger"/><Port Name="sent" Direction="Out" Type="trigger"/>
<Port Name="handled" Direction="Out" Type="trigger"/></Ports><Nodes>
<InstantSendMessage Name="send" MessageType="fixture:increment"><Edge From="first" To="Exec"/>
<Edge From="second" To="Exec"/><Edge From="receiver" To="Receiver"/><Edge From="delta" To="Content_delta"/></InstantSendMessage>
<Output Name="output"><Edge From="send.OnExec" To="sent"/><Edge From="ack" To="handled"/></Output>
</Nodes></Module></Skald></Database>'''

BEHAVIOR = b'''<BehaviorTrees><BehaviorTree name="receive"><Root><Behavior>
<ProcessMessage inbox="&quot;Inbox&quot;" variable="$message" senderInfo="$sender" timeout="-1" condition="" answerVar="">
<Expression expressions="$total = $total + $message.delta"/>
<Callback_empty EventName="&quot;handled&quot;"/></ProcessMessage>
</Behavior></Root></BehaviorTree></BehaviorTrees>'''


def source(graph=GRAPH):
    return Source.blobs({'graph.xml':graph, 'behavior.xml':BEHAVIOR})


def scenario(steps):
    return dict(schema=1, name='Native message to emulated controller', entry='Messages',
        inputs=dict(receiver='actor-17',delta=3), steps=steps,
        behaviors=[dict(id='actor',file='behavior.xml',variables={'total':0},callbacks={'handled':'ack'})],
        message_routes=[dict(receiver='actor-17',message_type='fixture:increment',controller='actor',inbox='Inbox',sender={'id':'quest'})])


class MessageTests(unittest.TestCase):
    def test_filtered_wait_preserves_other_messages_and_reload(self):
        from quest_lab.behavior import Behavior
        blob=BEHAVIOR.replace(b'condition=""',b'condition="$__content.delta == $wanted"')
        b=Behavior('receiver',blob,Graph(source(),'Messages'),variables={'total':0,'wanted':7},
                   callbacks={'handled':'ack'})
        b.message('Inbox',{'delta':3},{'id':'first'})
        task=b.start('receive')
        self.assertEqual(b.tasks[task]['status'],'message')
        self.assertNotIn('message',b.variables)
        saved=b.snapshot()
        for replay in range(2):
            if replay: b.restore(saved)
            b.message('Inbox',{'delta':7},{'id':'second'})
            self.assertEqual(b.tasks[task]['status'],'success')
            self.assertEqual(b.variables['total'],7)
            self.assertEqual(b.variables['sender'],{'id':'second'})
            self.assertNotIn('__content',b.variables)
            self.assertEqual(b.inboxes['Inbox'],[{'value':{'delta':3},'sender':{'id':'first'}}])

    def test_bad_filter_does_not_dequeue_or_publish_outputs(self):
        from quest_lab.behavior import Behavior
        for condition in ('$__content.missing', '$__content.delta', 'unknown()'):
            blob=BEHAVIOR.replace(b'condition=""',('condition="'+condition+'"').encode())
            b=Behavior('receiver',blob,Graph(source(),'Messages'),variables={'__content':'keep'})
            b.message('Inbox',{'delta':3},{'id':'first'})
            with self.assertRaises(Unsupported): b.start('receive')
            self.assertEqual(len(b.inboxes['Inbox']),1)
            self.assertEqual(b.variables,{'__content':'keep'})

    def test_compiled_handler_allows_omitted_message_outputs_and_replays(self):
        from test_campaign_ai_registration import setup
        from test_campaign_behavior import source as model_source, catalog
        from campaign_behavior import convert_behaviors
        from quest_lab.behavior import Behavior
        from test_quest_lab_includes import graph
        for payload, sender in (('message',''), ('','sender'), ('','')):
            with self.subTest(payload=payload,sender=sender), tempfile.TemporaryDirectory() as tmp:
                _, _, importer, _ = setup(tmp)
                model=model_source('<Root><Behavior><ProcessMessage Atomic="true" timeout="-1" '
                    'timeType="GameTime" variable="'+payload+'" senderInfo="'+sender+'" '
                    'inbox="StoryInbox" condition="$__content.ready" answerVar=""><Success/></ProcessMessage></Behavior></Root>')
                files, _, report=convert_behaviors([model],'OptionalOutputs',catalog(),importer=importer)
                self.assertEqual(report['whole_trees_emitted'],1)
                b=Behavior('receiver',next(iter(files.values())),graph())
                task=b.start('onUpdate')
                self.assertEqual(b.tasks[task]['status'],'message')
                saved=b.snapshot()
                for replay in range(2):
                    if replay: b.restore(saved)
                    b.message('testcampaign_StoryInbox',{'ready':False},{'id':'other'})
                    self.assertEqual(b.tasks[task]['status'],'message')
                    b.message('testcampaign_StoryInbox',{'ready':True},{'id':'quest'})
                    self.assertEqual(b.tasks[task]['status'],'success')
                    expected={}
                    if payload: expected[payload]={'ready':True}
                    if sender: expected[sender]={'id':'quest'}
                    self.assertEqual(b.variables,expected)
                    self.assertEqual(b.inboxes['testcampaign_StoryInbox'],
                        [{'value':{'ready':False},'sender':{'id':'other'}}])

    def test_invalid_message_binding_does_not_consume_queued_message(self):
        from quest_lab.behavior import Behavior
        for attr in ('variable','senderInfo'):
            blob=BEHAVIOR.replace((attr+'="$'+('message' if attr=='variable' else 'sender')+'"').encode(),
                                  (attr+'="$bad.field"').encode())
            b=Behavior('receiver',blob,Graph(source(),'Messages'))
            b.message('Inbox',{'delta':3},{'id':'quest'})
            with self.assertRaises(Unsupported): b.start('receive')
            self.assertEqual(len(b.inboxes['Inbox']),1)
            self.assertEqual(b.variables,{})

    def test_behavior_sender_routes_payload_to_repeating_receiver(self):
        sender = b'''<BehaviorTree name="send"><Root><Behavior><Sequence>
          <InstantSendMessageToNPC target="$recipient" type="fixture:increment" values="delta($amount + 1)"/>
          <Expression expressions="$amount = 99"/>
          </Sequence></Behavior></Root></BehaviorTree>'''
        receiver = BEHAVIOR.replace(b'<ProcessMessage ',b'<Loop count="-1"><ProcessMessage ').replace(
            b'</ProcessMessage>',b'</ProcessMessage></Loop>')
        s = scenario([dict(action='behavior_start',controller='actor',tree='receive',task='listen'),
                      dict(action='behavior_start',controller='sender',tree='send'),
                      dict(action='assert_behavior',controller='actor',variables={'total':4}),
                      dict(action='assert_behavior',controller='sender',variables={'amount':99}),
                      dict(action='save',name='first'),
                      dict(action='behavior_start',controller='sender',tree='send'),
                      dict(action='assert_behavior',controller='actor',variables={'total':104}),
                      dict(action='load',name='first'),
                      dict(action='behavior_start',controller='sender',tree='send'),
                      dict(action='assert_behavior',controller='actor',variables={'total':104})])
        s['behaviors'].append(dict(id='sender',file='sender.xml',variables={'recipient':'actor-17','amount':3}))
        r=Runner(s,source=Source.blobs({'graph.xml':GRAPH,'behavior.xml':receiver,'sender.xml':sender})).run()
        self.assertEqual(r['status'],'PASS',r.get('error'))
        messages=[e for e in r['timeline'] if e['kind']=='message_queued']
        self.assertEqual([m['content']['delta'] for m in messages],[4,100,100])

    def test_behavior_message_variable_is_copied_and_unrouted_message_blocks_pass(self):
        sender=b'''<BehaviorTree name="send"><Root><Behavior>
          <InstantSendMessageToNPC target="$recipient" type="fixture:increment" variable="$payload"/>
          </Behavior></Root></BehaviorTree>'''
        s=scenario([dict(action='behavior_start',controller='sender',tree='send'),
                    dict(action='behavior_set',controller='sender',values={'payload':{'delta':99}}),
                    dict(action='assert_behavior',controller='sender',tasks={'send:1':'success'})])
        s['message_routes']=[]
        s['behaviors'].append(dict(id='sender',file='sender.xml',variables={'recipient':'actor-17','payload':{'delta':5}}))
        runner=Runner(s,source=Source.blobs({'graph.xml':GRAPH,'behavior.xml':BEHAVIOR,'sender.xml':sender}))
        self.assertEqual(runner.run()['status'],'UNSUPPORTED')
        self.assertEqual(runner.graph.messages[0]['content'],{'delta':5})

    def test_behavior_message_invalid_payloads_do_not_dispatch(self):
        from quest_lab.behavior import Behavior
        for fields in ('values="delta(1),delta(2)"', 'values="bad"',
                       'variable="$payload" values="delta(2)"', 'variable="$scalar"'):
            blob=('<BehaviorTree name="send"><Root><Behavior><InstantSendMessageToNPC '
                  'target="$recipient" type="fixture:increment" '+fields+'/></Behavior></Root></BehaviorTree>').encode()
            g=Graph(source(),'Messages')
            b=Behavior('sender',blob,g,variables={'recipient':'actor-17','payload':{'delta':5},'scalar':3})
            with self.assertRaises(Unsupported): b.start('send')
            self.assertEqual(g.messages,[])

    def test_repeating_handler_consumes_routed_graph_messages_after_reload(self):
        repeated = BEHAVIOR.replace(b'<ProcessMessage ', b'<Loop count="-1"><ProcessMessage ').replace(
            b'</ProcessMessage>', b'</ProcessMessage></Loop>')
        steps = [dict(action='behavior_start', controller='actor', tree='receive', task='listen'),
                 dict(action='fire', port='first'), dict(action='save', name='one'),
                 dict(action='fire', port='second'),
                 dict(action='assert_behavior', controller='actor', variables={'total':6}),
                 dict(action='load', name='one'),
                 dict(action='assert_behavior', controller='actor', variables={'total':3}),
                 dict(action='fire', port='second'),
                 dict(action='assert_behavior', controller='actor', variables={'total':6}, tasks={'listen':'message'})]
        runner = Runner(scenario(steps), source=Source.blobs({'graph.xml':GRAPH, 'behavior.xml':repeated}))
        result = runner.run()
        self.assertEqual(result['status'], 'PASS', result.get('error'))
        # Trace/event counters include the replay; persisted variables rewind.
        self.assertEqual(runner.graph.events['output.handled'], 3)

    def test_native_exec_fanin_queues_two_messages_and_does_not_acknowledge_handling(self):
        g=Graph(source(),'Messages'); self.assertEqual(g.unsupported,[])
        g.set_inputs(dict(receiver='actor-17',delta=2)); g.fire('first')
        g.set_inputs({'delta':5}); g.fire('second')
        self.assertEqual([m['content'] for m in g.messages],[{'delta':2},{'delta':5}])
        self.assertEqual([m['id'] for m in g.messages],[1,2])
        self.assertEqual(g.events['output.sent'],2)
        self.assertEqual(g.events['output.handled'],0)

    def test_queue_survives_json_snapshot_and_message_identity_replay(self):
        g=Graph(source(),'Messages'); g.set_inputs(dict(receiver='actor-17',delta=2)); g.fire('first')
        snapshot=json.loads(json.dumps(g.snapshot()))
        g.fire('second'); self.assertEqual(len(g.messages),2)
        g.restore(snapshot); self.assertEqual(len(g.messages),1)
        g.fire('second'); self.assertEqual([m['id'] for m in g.messages],[1,2])

    def test_actual_behavior_consumes_fifo_after_delivery_and_replays_from_save(self):
        s=scenario([
            dict(action='fire',port='first'),dict(action='set',values={'delta':7}),dict(action='fire',port='second'),
            dict(action='assert',events={'output.sent':2,'output.handled':0}),dict(action='save',name='queued'),
            dict(action='behavior_start',controller='actor',tree='receive',task='one'),
            dict(action='assert_behavior',controller='actor',variables={'total':3,'sender':{'id':'quest'}}),
            dict(action='behavior_start',controller='actor',tree='receive',task='two'),
            dict(action='assert_behavior',controller='actor',variables={'total':10}),
            dict(action='assert',events={'output.handled':2}),dict(action='load',name='queued'),
            dict(action='behavior_start',controller='actor',tree='receive',task='replay'),
            dict(action='assert_behavior',controller='actor',variables={'total':3}),
            dict(action='assert_messages',count=0)])
        r=Runner(s,source=source()).run()
        self.assertEqual(r['status'],'PASS',r['error'])
        self.assertEqual(len([e for e in r['timeline'] if e['kind']=='message_delivered']),2)

    def test_missing_route_keeps_message_pending_and_prevents_pass(self):
        s=scenario([dict(action='fire',port='first'),dict(action='assert_messages',count=1)])
        s['message_routes']=[]
        r=Runner(s,source=source()).run()
        self.assertEqual(r['status'],'UNSUPPORTED')
        self.assertEqual(r['graph']['pending_messages'][0]['receiver'],'actor-17')
        self.assertNotIn('message_processed',[e['kind'] for e in r['timeline']])

    def test_receiver_and_type_must_both_match_route(self):
        for key,value in [('receiver','another-actor'),('message_type','fixture:other')]:
            s=scenario([dict(action='fire',port='first'),dict(action='assert_messages',count=1)])
            s['message_routes'][0][key]=value
            self.assertEqual(Runner(s,source=source()).run()['status'],'UNSUPPORTED')

    def test_unknown_receiver_controller_and_duplicate_routes_are_rejected(self):
        s=scenario([dict(action='assert_messages',count=0)])
        s['message_routes'].append(copy.deepcopy(s['message_routes'][0]))
        with self.assertRaisesRegex(ValueError,'Ambiguous message route'):Runner(s,source=source())
        s['message_routes'].pop();s['message_routes'][0]['controller']='missing'
        with self.assertRaisesRegex(ValueError,'Unknown message receiver controller'):Runner(s,source=source())

    def test_receiver_route_change_invalidates_snapshot_before_mutation(self):
        s=scenario([dict(action='assert_messages',count=0)])
        a=Runner(s,source=source());snapshot=a.snapshot()
        s['message_routes'][0]['sender']={'id':'different-quest'}
        b=Runner(s,source=source())
        with self.assertRaisesRegex(ValueError,'Snapshot message routing mismatch'):b.restore(snapshot)
        self.assertEqual(b.behaviors['actor'].variables['total'],0)

    def test_message_assets_require_explicit_binding(self):
        blob=GRAPH.replace(b'<Edge From="receiver" To="Receiver"/>',b'<Asset Name="Receiver" Alias="actor_alias"/>')
        g=Graph(source(blob),'Messages');g.set_inputs({'delta':3})
        with self.assertRaisesRegex(Unsupported,'Unbound asset'):g.fire('first')
        g=Graph(source(blob),'Messages',assets={'actor_alias':'actor-17'});g.set_inputs({'delta':3});g.fire('first')
        self.assertEqual(g.messages[0]['receiver'],'actor-17')

    def test_asset_input_is_lazy_and_explicit_call_input_takes_precedence(self):
        blob=GRAPH.replace(b'<Port Name="receiver" Direction="In" Type="string"/>',
                           b'<AssetPort Name="receiver" Direction="In" Type="string" Alias="actor_alias"/>')
        g=Graph(source(blob),'Messages',assets={'actor_alias':'actor-17'});g.set_inputs({'delta':3});g.fire('first')
        self.assertEqual(g.messages[0]['receiver'],'actor-17')
        g.set_inputs({'receiver':'actor-18'});g.fire('second')
        self.assertEqual(g.messages[1]['receiver'],'actor-18')

    def test_duplicate_asset_bindings_and_changed_asset_identity_are_rejected(self):
        duplicate=GRAPH.replace(b'<Edge From="receiver" To="Receiver"/>',
                                b'<Asset Name="Receiver" Alias="a"/><Asset Name="Receiver" Alias="b"/>')
        with self.assertRaisesRegex(ValueError,'Multiple asset/value drivers'):Graph(source(duplicate),'Messages')
        a=Graph(source(),'Messages',assets={'player':'actor-1'})
        b=Graph(source(),'Messages',assets={'player':'actor-2'})
        with self.assertRaisesRegex(ValueError,'Snapshot graph/contract mismatch'):b.restore(a.snapshot())

    def test_message_payload_is_copied_at_dispatch(self):
        g=Graph(source(),'Messages');g.set_inputs(dict(receiver='actor-17',delta=3));g.fire('first')
        g.set_inputs({'delta':9});self.assertEqual(g.messages[0]['content']['delta'],3)

    def test_unknown_message_fields_and_unbound_target_cannot_pass(self):
        blob=GRAPH.replace(b'To="Content_delta"',b'To="MadeUp"')
        g=Graph(source(blob),'Messages');self.assertTrue(g.unsupported)
        g.set_inputs(dict(receiver='actor-17',delta=3))
        with self.assertRaises(Unsupported):g.fire('first')
        g=Graph(source(),'Messages');g.set_inputs({'delta':3})
        with self.assertRaises(Unsupported):g.fire('first')

    def test_wrong_inbox_does_not_acknowledge_message_consumption(self):
        s=scenario([dict(action='behavior_start',controller='actor',tree='receive'),
                    dict(action='fire',port='first'),dict(action='await',port='output.handled',timeout='1m')])
        s['message_routes'][0]['inbox']='WrongInbox'
        r=Runner(s,source=source()).run()
        self.assertEqual(r['status'],'TIMEOUT')
        self.assertIn('output.handled',r['error'])

    def test_external_receiver_fixture_is_hashed_and_exclusive_with_package_path(self):
        s=scenario([dict(action='behavior_start',controller='actor',tree='receive'),dict(action='fire',port='first'),
                    dict(action='assert_behavior',controller='actor',variables={'total':3})])
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'fixture.xml';path.write_bytes(BEHAVIOR)
            s['behaviors'][0].pop('file');s['behaviors'][0]['path']='fixture.xml'
            r=Runner(s,base=Path(d),source=source()).run();self.assertEqual(r['status'],'PASS',r['error'])
            a=Runner(s,base=Path(d),source=source());snapshot=a.snapshot()
            path.write_bytes(BEHAVIOR.replace(b'$total +',b'$total + 1 +'))
            b=Runner(s,base=Path(d),source=source())
            with self.assertRaisesRegex(ValueError,'Snapshot behavior mismatch'):b.restore(snapshot)
            s['behaviors'][0]['file']='behavior.xml'
            with self.assertRaisesRegex(ValueError,'exactly one'):Runner(s,base=Path(d),source=source())


if __name__ == '__main__':unittest.main()
