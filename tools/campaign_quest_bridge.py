"""Lower source quest-state operations to native AI/Skald registrations.

Names and relationships come from imported quest records. The adapter does not
select an opening quest, start it, or flatten its containing behavior branch.
AI signals carry actions into Skald; game contexts expose state back to AI.
The complete behavior compiler must accept every surrounding operation before
these fragments may be installed as an executable campaign.
"""
from collections import Counter
import copy
import hashlib
import xml.etree.ElementTree as ET

from campaign_quest_graph import constant, definition, edge, identifier, objective_identifier, xml
from quest_import import literal


OPERATIONS = {'SetQuest', 'SetQuestObjective', 'QuestCondition',
              'QuestObjectiveCondition', 'QuestObjectiveGate'}
QUEST_ACTIONS = {'ActivateQuest': 'activate', 'DeactivateQuest': 'deactivate',
                 'StartQuest': 'start'}
OBJECTIVE_ACTIONS = {'StartObjective': 'start', 'CompleteObjective': 'complete',
                     'CancelObjective': 'cancel', 'FailObjective': 'fail'}
QUEST_CONDITIONS = {'IsQuestActivated': 'available', 'IsQuestStarted': 'started',
                    'IsQuestCompleted': 'completed', 'IsQuestUnchanged': 'unchanged'}
OBJECTIVE_CONDITIONS = {'IsObjectiveStarted': 'Started', 'IsObjectiveCompleted': 'Completed',
                        'IsObjectiveCanceled': 'Canceled', 'IsObjectiveUnchanged': 'Unchanged'}


class UnsupportedOperation(ValueError):
    pass


class QuestBridge:
    def __init__(self, models, namespace):
        self.namespace = identifier(namespace)
        self.quests = {}
        for model in models:
            name = identifier(model['quest'])
            if name in self.quests:
                raise ValueError('Duplicate quest in bridge: ' + name)
            self.quests[name] = {r['objective_name'] for r in model['tables']['quest_objective']['rows']}
        self.actions = {}
        self.conditions = {}

    def _name(self, kind, quest, port):
        digest = hashlib.sha256((quest + ':' + port).encode()).hexdigest()[:20]
        return self.namespace + '_' + kind + '_' + digest

    def resolve(self, node, owner):
        """Return one target operation, retaining the source call's scope."""
        op = node['op']
        if op not in OPERATIONS:
            raise UnsupportedOperation('Not a quest-state operation: ' + op)
        attrs = {k: literal(v) for k, v in node['attributes'].items()}
        quest = attrs.get('quest', attrs.get('questName', ''))
        if quest in ('', '$this.name', 'this.name'):
            quest = owner
        if quest not in self.quests:
            raise UnsupportedOperation('Unregistered or dynamic source quest: ' + quest)
        objective = attrs.get('objective', attrs.get('objectiveName', ''))
        if op in ('SetQuestObjective', 'QuestObjectiveCondition', 'QuestObjectiveGate') and objective not in self.quests[quest]:
            raise UnsupportedOperation('Unregistered or dynamic objective: ' + quest + '.' + objective)
        function = attrs.get('function', attrs.get('objectiveState', ''))
        if op == 'SetQuest':
            # Older source files also dispatch objective operations through
            # SetQuest's general quest-module function selector.
            if function in OBJECTIVE_ACTIONS:
                if objective not in self.quests[quest] or attrs.get('paramInt', '0') != '0':
                    raise UnsupportedOperation('Unregistered or dynamic objective: ' + quest + '.' + objective)
                kind, port = 'action', OBJECTIVE_ACTIONS[function] + '_' + objective_identifier(objective)
            elif attrs.get('paramInt', '0') != '0' or objective or function not in QUEST_ACTIONS:
                raise UnsupportedOperation('Quest lifecycle policy needs an adapter: ' + function)
            else:
                kind, port = 'action', QUEST_ACTIONS[function]
        elif op == 'SetQuestObjective':
            if function not in OBJECTIVE_ACTIONS:
                raise UnsupportedOperation('Objective action needs an adapter: ' + function)
            kind, port = 'action', OBJECTIVE_ACTIONS[function] + '_' + objective_identifier(objective)
        elif op == 'QuestCondition':
            if function not in QUEST_CONDITIONS:
                raise UnsupportedOperation('Quest condition needs an adapter: ' + function)
            kind, port = 'condition', QUEST_CONDITIONS[function]
        elif op == 'QuestObjectiveCondition':
            if function not in OBJECTIVE_CONDITIONS:
                raise UnsupportedOperation('Objective condition needs an adapter: ' + function)
            kind, port = 'condition', objective_identifier(objective) + '_' + OBJECTIVE_CONDITIONS[function]
        else:
            # AfterReset and gate cancellation/lifetime are not a boolean check.
            # Keep the source gate intact until its native semantics are tested.
            raise UnsupportedOperation('Quest objective gate lifecycle needs an adapter: ' + function)
        if kind == 'condition' and attrs.get('failSubtMissing', 'false') not in ('false', '0'):
            raise UnsupportedOperation('Missing-branch failure semantics need an adapter')
        name = self._name(kind, quest, port)
        result = dict(kind=kind, name=name, quest=quest, port=port, source_operation=op)
        (self.actions if kind == 'action' else self.conditions)[name] = result
        return result

    def lower(self, node, owner):
        """Replace only this operation; child order and branch structure survive."""
        if node['op'] in ('SetQuest', 'SetQuestObjective') and node['children']:
            raise UnsupportedOperation('Quest action unexpectedly has executable children')
        binding = self.resolve(node, owner)
        if binding['kind'] == 'action':
            return dict(op='SendAIConceptSignal_' + binding['name'], attributes={}, children=[], text='')
        return dict(op='GameContextCheck', attributes={'context': binding['name'], 'saveVersion': '2'},
                    children=copy.deepcopy(node['children']), text=node.get('text', ''))

    def emit(self):
        """Emit complete declarations for the operations resolved so far."""
        root, module = definition('Module', self.namespace + '_quest_bridge')
        ports = ET.SubElement(module, 'Ports')
        nodes = ET.SubElement(module, 'Nodes')
        output = ET.SubElement(nodes, 'Output', Name='Output')
        signal_root = ET.Element('database')
        signals = ET.SubElement(signal_root, 'AIConceptSignalDatabase', version='1')
        context_root = ET.Element('database', name='barbora')
        contexts = ET.SubElement(context_root, 'ScriptContexts', version='1')
        wiring = []
        for name, record in sorted(self.actions.items()):
            ET.SubElement(signals, 'AIConceptSignal', Name=name)
            ET.SubElement(ports, 'Port', Name=name, Direction='Out', Type='trigger')
            trigger = ET.SubElement(nodes, 'AIConceptSignalTrigger', Name=name, NotificationName=name)
            constant(trigger, 'IsActive', 'true')
            edge(output, name + '.OnNotification', name)
            wiring.append(dict(direction='action', bridge_port=name, quest=record['quest'], quest_port=record['port']))
        for name, record in sorted(self.conditions.items()):
            ET.SubElement(contexts, 'ScriptContextDatabaseNode', Name=name, Class='Game')
            ET.SubElement(ports, 'Port', Name=name, Direction='In', Type='bool')
            state = ET.SubElement(nodes, 'SetGameContext', Name=name)
            constant(state, 'Context', name)
            edge(state, name, 'IsActive')
            wiring.append(dict(direction='condition', bridge_port=name, quest=record['quest'], quest_port=record['port']))
        return {
            'Quests/' + self.namespace + '_quest_bridge.xml': xml(root),
            'Libs/Tables/ai/AIConceptSignalDatabase__' + self.namespace.lower() + '.xml': xml(signal_root),
            'Libs/Tables/ai/ScriptContext__' + self.namespace.lower() + '.xml': xml(context_root),
        }, wiring


def convert_state_operations(models, namespace):
    """Resolve source sites independently, reporting every unsupported site.

    These are conversion fragments, not a runnable approximation of a quest.
    Unknown branches remain in the original IR and block full compilation.
    """
    bridge = QuestBridge(models, namespace)
    converted, unresolved = [], []
    for model in models:
        document = 'libs/ai/quests/' + model['quest'].lower() + '.xml'
        if document not in model['behavior_documents']:
            if model.get('behavior_status') == 'no_same_named_retail_program':
                continue
            raise ValueError('Missing source quest behavior: ' + document)
        for tree, body in model['behavior_documents'][document]['trees'].items():
            def visit(node, path, ancestors):
                if node['op'] in OPERATIONS:
                    site = dict(quest=model['quest'], document=document, tree=tree, path=path,
                                operation=node['op'], arguments=node['attributes'], ancestors=ancestors)
                    try:
                        target = bridge.lower(node, model['quest'])
                    except UnsupportedOperation as error:
                        unresolved.append(dict(site=site, reason=str(error)))
                    else:
                        converted.append(dict(site=site, target=target))
                for i, child in enumerate(node['children']):
                    visit(child, path + '/' + str(i), ancestors + [dict(op=node['op'], attributes=node['attributes'])])
            visit(body['root'], 'Root', [])
    files, wiring = bridge.emit()
    return files, dict(schema=1, namespace=namespace, converted=converted, unresolved=unresolved,
                       wiring=wiring, actions=len(bridge.actions), conditions=len(bridge.conditions),
                       converted_operations=dict(Counter(r['site']['operation'] for r in converted)),
                       executable=False, runtime_validated=False,
                       requires='Complete behavior, world, dialogue, inventory and lifecycle compilation')
