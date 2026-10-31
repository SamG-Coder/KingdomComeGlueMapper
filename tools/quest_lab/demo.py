"""Generate redistributable fixtures with real converter emitters, not game XML.

Synthetic identities and tutorial contracts are deliberate: these test the
general converter output and do not claim Van­yek or a retail save is fixed.
"""
import copy
import json
from pathlib import Path
import xml.etree.ElementTree as E


def model():
    objectives = [dict(quest_id='42', objective_id=str(i), objective_name=name,
        is_hidden='True', is_exclusive='False', condition='1' if i == 3 else '',
        autocomplete_timeout_str='2m #GT' if i == 3 else '', expiration_timeout_str='')
        for i, name in ((3,'opening_delay'),(4,'training'),(5,'return_home'))]
    nodes = {'1': dict(attributes={'Class':'Quest:Begin'}), '2':dict(attributes={'Class':'Quest:End'})}
    for o in objectives:
        nodes[o['objective_id']] = dict(attributes={'Class':'Quest:Objective'}, payload=[dict(op='Inputs',
            attributes=dict(Name=o['objective_name'], IsHidden='1', IsExclusive='0'))])
    transitions = [dict(quest_id='42', from_objective_id=a, to_objective_id=b, enabled='True')
        for a,b in [('1','3'),('3','4'),('4','5'),('5','2')]]
    return dict(quest='example_quest', quest_id='42', graph=dict(nodes=nodes, unresolved_edges=[],
        edges=[dict(nodeOut=t['from_objective_id'], nodeIn=t['to_objective_id'], portOut='Out', portIn='In', enabled='1') for t in transitions]),
        tables={'quest':dict(rows=[dict(quest_id='42',quest_name='example_quest',is_activated='False')]),
                'quest_objective':dict(rows=objectives), 'quest_transition':dict(rows=transitions)})


def fixtures():
    from campaign_quest_graph import emit_project, xml
    from campaign_native_training import OUTPUTS
    from campaign_training_runtime import emit
    from campaign_schedule_lifecycle import ScheduleAdapter, tree
    files, _ = emit_project([model()], 'EmulatorExample', startup_quest='example_quest')
    contract = {'Basic':dict(inputs=['Tutor','IsActive','CustomStepEvent'],
        outputs=[p for p,_ in OUTPUTS.values()], evidence=[dict(source='synthetic emulator fixture; no native verification')])}
    files['training.xml'], _ = emit('TrainingExample', 'Basic', contract)
    plan = dict(owner='example_controller', leases=[dict(actor='17', handle='example_job', sites=[],
        policy=dict(immediate=True, source='$this.id', once=True))])
    adapter = ScheduleAdapter(plan, '00000002-0000-0000', 'EmulatorExample')
    root = E.Element('BehaviorTrees'); root.append(adapter.consumer())
    root.append(tree('release', adapter.released(adapter.routes[0])))
    files['AI/schedule.xml'] = xml(root)
    return files, adapter


def scenarios(adapter):
    from campaign_native_training import OUTPUTS
    base = dict(schema=1, source='graphs', entry='EmulatorExample')
    timed = dict(base, name='Converted quest timer, save/reload and source transition chain', steps=[
        dict(action='fire',port='new_game.OnStart'),
        dict(action='assert',values={'example_quest.opening_delay_Started':True}),
        dict(action='advance',duration='1m'), dict(action='save',name='pending'),
        dict(action='advance',duration='59s'),
        dict(action='assert',values={'example_quest.training_Started':False}),
        dict(action='advance',duration='1s'),
        dict(action='assert',values={'example_quest.training_Started':True}),
        dict(action='load',name='pending'), dict(action='advance',duration='1m',mode='skip'),
        dict(action='assert',values={'example_quest.training_Started':True}),
        dict(action='fire',port='example_quest.complete_training'),
        dict(action='assert',values={'example_quest.return_home_Started':True,'example_quest.completed':False}),
        dict(action='fire',port='example_quest.complete_return_home'),
        dict(action='assert',values={'example_quest.completed':True})])
    training = dict(schema=1, source='graphs', entry='TrainingExample',
        description='Host-logic test. Preparation, arrival and combat outcomes are explicit scenario inputs.',
        inputs=dict(tutor='fixture_tutor',tutor_ready=False,dialogue_busy=True,lesson_authorized=False,ack_phase=0),
        contracts={'training':dict(reason='Combat execution is supplied by the scenario, not emulated',
            inputs={'Tutor':'value','IsActive':'value','CustomStepEvent':'trigger'},
            outputs={port:typ for port,typ in OUTPUTS.values()}, values={'Phase':0})})
    common = [dict(action='fire',port='request'), dict(action='fire',port='arrived'),
        dict(action='set',values={'lesson_authorized':True}), dict(action='fire',port='authorization_changed'),
        dict(action='assert',states={'at_destination':True,'preparing':False},events={'output.prepare':0}),
        dict(action='set',values={'dialogue_busy':False}), dict(action='fire',port='dialogue_finished'),
        dict(action='assert',states={'preparing':True},events={'output.prepare':1}),
        dict(action='set',values={'tutor_ready':True}), dict(action='fire',port='prepared'),
        dict(action='assert',values={'output.training_active':True})]
    success = dict(copy.deepcopy(training), name='Converted training host waits for scene release and cleanup',steps=[*common,
        dict(action='fire',port='training.OnTutorialEnd'),
        dict(action='assert',states={'stopping':True},events={'output.finished':0}),
        dict(action='fire',port='cleanup_done'),dict(action='assert',events={'output.finished':0}),
        dict(action='fire',port='workers_stopped'),dict(action='save',name='cleanup_pending'),
        dict(action='load',name='cleanup_pending'),dict(action='fire',port='cleanup_done'),
        dict(action='assert',values={'output.busy':False},events={'output.finished':1})])
    canceled = dict(copy.deepcopy(training), name='Converted training cancellation rejects late callbacks',steps=[
        *common[:8], dict(action='fire',port='cancel'),dict(action='fire',port='prepared'),
        dict(action='fire',port='training.OnTutorialEnd'),
        dict(action='assert',values={'output.training_active':False},events={'output.finished':0}),
        dict(action='fire',port='workers_stopped'), dict(action='fire',port='cleanup_done'),
        dict(action='assert',values={'output.busy':False},events={'output.canceled':1,'output.finished':0})])
    route = adapter.routes[0]; alias = route['name']; v = adapter.variables(route)
    variables = dict(this={'id':2}, __null=0, __behaviors={alias:dict(count=0)})
    variables.update({v[k]:0 if k in ('generation','actor') else False for k in v})
    def message(kind, generation=0):
        return dict(action='message',controller='schedule',inbox=adapter.mailbox,
            value=dict(route=0,kind=kind,generation=generation,actor=17),sender={'id':2 if kind<2 else 17})
    def check(**kwargs): return dict(action='assert_behavior',controller='schedule',**kwargs)
    lease = dict(base,name='Converted NPC lease replacement rejects stale worker completion',
        behaviors=[dict(id='schedule',file='AI/schedule.xml',variables=variables)],steps=[
            dict(action='behavior_start',controller='schedule',tree=adapter.stem+'_consume',task='mailbox'),
            message(1),check(availability={alias:'Enabled'},variables={v['generation']:1}),
            dict(action='behavior_set',controller='schedule',values={'__behaviors':{alias:dict(count=1)}}),
            message(2),check(task_variables={'mailbox':{'answer':1}}),message(1),
            check(availability={alias:'Disabled'},variables={v['draining']:True}),
            dict(action='save',name='replacement'), dict(action='load',name='replacement'),
            message(3,1),check(availability={alias:'Disabled'}),
            dict(action='behavior_set',controller='schedule',values={'__behaviors':{alias:dict(count=0)}}),
            dict(action='behavior_start',controller='schedule',tree='release',task='release1'),
            check(availability={alias:'Enabled'},variables={v['generation']:3}),
            dict(action='behavior_set',controller='schedule',values={'__behaviors':{alias:dict(count=1)}}),
            message(2), check(task_variables={'mailbox':{'answer':3}}), message(3,1),
            check(availability={alias:'Enabled'}), message(3,3),
            check(availability={alias:'NoRequests'},variables={v['draining']:True}),
            dict(action='behavior_set',controller='schedule',values={'__behaviors':{alias:dict(count=0)}}),
            dict(action='behavior_start',controller='schedule',tree='release',task='release2'),
            check(variables={v['draining']:False,v['wanted']:False})])
    return [timed, success, canceled, lease]


def generate(output):
    output = Path(output)
    if output.exists(): raise FileExistsError('Choose a new demo output directory')
    files, adapter = fixtures()
    for name, blob in files.items():
        path = output/'graphs'/name
        path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(blob)
    paths = []
    for i, scenario in enumerate(scenarios(adapter),1):
        path = output/f'{i:02d}-scenario.json'
        path.write_text(json.dumps(scenario,indent=2),encoding='utf-8'); paths.append(path)
    return paths
