"""Interpret emitted Skald control flow. Engine-dependent nodes need contracts.

The ordering model is synchronous XML-order fanout and A..Z trigger sequences,
with Timer callbacks deferred to the deterministic game clock. This is a test
model, not a claim of engine scheduling/serialization equivalence.
"""
from collections import Counter, defaultdict
import copy
import hashlib
import heapq
import json

from . import BudgetExceeded, Unsupported
from .expressions import boolean, duration, literal
from .pure import operation, comparison, compare, COMPARE
from .dialogue import Dialogue, KINDS as DIALOGUES, INPUTS as DIALOGUE_INPUTS, OUTPUTS as DIALOGUE_OUTPUTS
from .native import NativeCalls
from . import collections as collection_nodes
from .objects import Objects, describe as object_description


FUNCTIONS = {
    'math::boolean::Not': lambda v: not boolean(v['Value']),
    'math::boolean::And': lambda v: boolean(v['A']) and boolean(v['B']),
    'math::boolean::Or': lambda v: boolean(v['A']) or boolean(v['B']),
    'math::boolean::Xor': lambda v: boolean(v['A']) != boolean(v['B']),
}
BUILTINS = {'State', 'Function', 'IfFunction', 'If', 'TriggerSequence', 'Timer',
            'Trace', 'HasteTrigger', 'GameStart', 'InstantSendMessage', 'ItemDescriptorTrigger',
            'AIConceptSignalTrigger', 'SetGameContext'} | collection_nodes.KINDS


def qualified(scope, value):
    return scope + '.' + value if scope else value


class Graph:
    def __init__(self, source, entry, contracts=None, budget=100000, entry_file=None, assets=None, objects=None):
        self.source, self.entry = source, entry
        self.contracts = copy.deepcopy(contracts or {})
        self.budget = budget
        self.nodes, self.inputs, self.aliases = {}, {}, {}
        self.routes = defaultdict(list)
        self.states, self.enum_values, self.external_values = {}, {}, {}
        self.timers, self.generations, self.external_last = [], {}, {}
        self.input_types, self.event_ports, self.output_ports = {}, set(), set()
        self.relay_outputs = set()
        self.unsupported, self.used_files = [], set()
        self.visited, self.events, self.trace = Counter(), Counter(), []
        self.now, self.serial, self.steps = 0, 0, 0
        self.stack = []
        self.assets = copy.deepcopy(assets if assets is not None else {})
        if not isinstance(self.assets, dict) or any(not isinstance(k,str) or not k for k in self.assets):
            raise ValueError('Assets must map nonempty aliases to explicit values')
        self.asset_inputs = {}
        self.messages, self.message_serial = [], 0
        self.dialogues = {}
        self.dialogue_outputs = set()
        self.native_functions = set()
        self.native = NativeCalls(self)
        self.objects = Objects(self, objects)
        if entry_file and entry_file.replace('\\','/').lower() not in {p for p,_ in source.definitions.get(entry,[])}:
            raise Unsupported('Entry file does not define ' + entry)
        self._load(entry, '', (), {entry_file.replace('\\','/').lower()} if entry_file else None)
        self._audit()
        for name in self.contracts:
            if name not in self.nodes: raise ValueError('Contract refers to missing node: ' + name)
            if name in self.dialogues: raise ValueError('Cannot replace dialogue logic with a contract: ' + name)
        self.fingerprint = hashlib.sha256(json.dumps(dict(entry=entry,
            files={p: source.hashes[p] for p in sorted(self.used_files)},
            contracts=self.contracts, assets=self.assets, objects=objects), sort_keys=True).encode()).hexdigest()

    def record(self, kind, **values):
        self.trace.append(dict(time_us=self.now, kind=kind, **values))

    def tick(self):
        self.steps += 1
        if self.steps > self.budget: raise BudgetExceeded('Event/operation budget exceeded')

    def issue(self, node, reason):
        item = dict(node=node, reason=reason)
        if item not in self.unsupported: self.unsupported.append(item)

    def _load(self, definition, scope, ancestors, preferred=None):
        path, host = self.source.definition(definition, preferred)
        identity = (path,definition)
        if identity in ancestors: raise Unsupported('Recursive graph definition: ' + definition)
        self.used_files.add(path)
        includes = set()
        for dependency in host.findall('Definitions/Definition'):
            try: includes.add(self.source.reference(dependency.get('File',''), path))
            except Unsupported as exc: self.issue(scope or definition, str(exc))
        ports = {p.get('Name'): (p.get('Direction'), p.get('Type')) for p in host.findall('Ports/*')
                 if p.tag in ('Port', 'ConstantPort', 'AssetPort')}
        if host.tag in DIALOGUES:
            for port, typ in DIALOGUE_INPUTS.items(): ports.setdefault(port, ('In', typ))
            for port in DIALOGUE_OUTPUTS: ports.setdefault(port, ('Out', 'trigger'))
        for p in host.findall('Ports/ConstantPort'):
            if p.get('Direction') == 'In' and 'Value' in p.attrib:
                self.inputs[qualified(scope, p.get('Name'))] = literal(p.get('Value'))
        for p in host.findall('Ports/AssetPort'):
            if p.get('Direction') == 'In': self.asset_inputs[qualified(scope, p.get('Name'))] = p.get('Alias')
        journals = {o.get('Name') for o in host.findall('Objectives/Objective')}
        types = defaultdict(list)
        for candidates in self.source.definitions.values():
            for type_path, h in candidates:
                for t in h.findall('Types/Type'):
                    types[t.get('TypeName')].append((type_path, [v.get('Name') for v in t.findall('StateTypeEnumeration')]))
        for port, (direction, typ) in ports.items():
            ref = qualified(scope, port)
            if direction == 'In':
                self.input_types[ref] = typ
                if typ == 'trigger': self.event_ports.add(ref)
            elif direction == 'Out':
                self.output_ports.add(qualified(scope or 'output', port))
        if host.tag in DIALOGUES:
            name = scope or definition
            self.nodes[name] = dict(element=host, kind='Dialogue', scope=scope, constants={}, values={}, file=path, journal=False)
            self.dialogues[name] = Dialogue(self, name, scope, host)
            self.dialogue_outputs.update(qualified(scope or 'output', p) for p, (direction, _) in ports.items()
                                         if direction == 'Out')
            for port, typ in DIALOGUE_INPUTS.items():
                if typ == 'trigger': self.routes[qualified(scope, port)].append(('deliver', (name, port)))
            return
        seen = set()
        for element in host.findall('Nodes/*'):
            local = element.get('Name')
            if not local or local in seen: raise ValueError('Duplicate/missing node name in ' + definition)
            seen.add(local)
            name = qualified(scope, local)
            constants = {c.get('Name'): literal(c.get('Value', '')) for c in element.findall('Constant')}
            edges = [(qualified(scope, e.get('From')), e.get('To')) for e in element.findall('Edge')]
            if element.tag == 'Output':
                for ref, port in edges:
                    out = qualified(scope or 'output', port)
                    typ = ports.get(port, ('Out', None))[1]
                    if typ == 'trigger' or port == 'source_state_changed':
                        self.routes[ref].append(('relay', out))
                        self.relay_outputs.add(out)
                    else:
                        if out in self.aliases: raise ValueError('Multiple data drivers: ' + out)
                        self.aliases[out] = ref
                    self.output_ports.add(out)
                continue
            if element.tag in self.source.definitions:
                self._load(element.tag, name, (*ancestors, identity), includes)
                bound = set()
                for ref, port in edges:
                    dest = name + '.' + port
                    if self.input_types.get(dest) == 'trigger': self.routes[ref].append(('relay', dest))
                    elif dest in self.input_types:
                        if dest in self.aliases: raise ValueError('Multiple data drivers: ' + dest)
                        self.aliases[dest] = ref
                        bound.add(port)
                    else: self.issue(name, 'Unknown child input: ' + port)
                for port, value in constants.items():
                    dest = name + '.' + port
                    if port in bound: raise ValueError('Multiple data drivers: ' + dest)
                    bound.add(port)
                    if dest in self.input_types: self.inputs[dest] = value
                    else: self.issue(name, 'Unknown child constant: ' + port)
                for asset in element.findall('Asset'):
                    port, alias = asset.get('Name'), asset.get('Alias')
                    dest = name + '.' + str(port)
                    if port in bound: raise ValueError('Multiple asset/value drivers: ' + dest)
                    bound.add(port)
                    if dest not in self.input_types or self.input_types[dest] == 'trigger':
                        self.issue(name, 'Unknown child asset input: ' + str(port))
                    elif not alias:
                        self.issue(name, 'Child asset has no alias: ' + str(port))
                    else:
                        # Instance bindings override the definition's default.
                        self.inputs.pop(dest, None)
                        self.asset_inputs[dest] = alias
                continue
            node = dict(element=element, kind=element.tag, scope=scope, constants=constants,
                        values={}, unresolved={}, file=path, journal=element.tag in journals)
            self.nodes[name] = node
            node['object_spec'] = object_description(element)
            node['value_spec'] = None
            if element.tag in collection_nodes.KINDS:
                enums = {t: list(next(iter(v))) for t, rows in types.items()
                         if len(v := {tuple(values) for _, values in rows}) == 1}
                try: node['value_spec'] = collection_nodes.describe(element, enums)
                except Unsupported as exc: self.issue(name, str(exc))
                if node['value_spec']:
                    for typ in [element.get('TypeT'), *element.get('ValueTypes','').split()]:
                        self.used_files.update(p for p, _ in types.get(typ, []))
            native_call = (element.tag in ('Function', 'MemberFunction') and not operation(element)
                and element.get('MethodName') not in FUNCTIONS and any(p == 'Exec' for _,p in edges))
            if native_call: self.native_functions.add(name)
            contract = self.contracts.get(name)
            if element.tag in BUILTINS or operation(element) or node['object_spec']:
                assets_seen = set()
                for asset in element.findall('Asset'):
                    port = asset.get('Name')
                    if port in constants or port in assets_seen or any(p == port for _,p in edges):
                        raise ValueError('Multiple asset/value drivers: ' + name + '.' + str(port))
                    assets_seen.add(port)
                constant_names = [c.get('Name') for c in element.findall('Constant')]
                if len(set(constant_names)) != len(constant_names) or any(p in constants for _, p in edges):
                    raise ValueError('Multiple constant/value drivers: ' + name)
            if contract:
                if ((element.tag in BUILTINS and not native_call) or node['journal'] or operation(element)
                        or node['object_spec'] and self.objects.enabled):
                    raise ValueError('Cannot replace interpreted logic with a contract: ' + name)
                if not contract.get('reason') or not isinstance(contract.get('outputs', {}), dict):
                    raise ValueError('External contract requires reason and typed outputs: ' + name)
                if set(contract) - ({'reason','inputs','outputs','values'} | ({'native'} if native_call else set())):
                    raise ValueError('Unknown external contract fields: ' + name)
                if native_call: self.native.validate(name, contract)
                if any(t not in ('value','trigger') for t in contract.get('inputs', {}).values()):
                    raise ValueError('Contract input kinds must be value/trigger: ' + name)
                for port, typ in contract.get('outputs', {}).items():
                    ref = name + '.' + port
                    if typ == 'trigger': self.event_ports.add(ref)
                    else: self.input_types[ref] = typ
                for port, value in contract.get('values', {}).items():
                    if contract.get('outputs', {}).get(port) in (None, 'trigger'):
                        raise ValueError('Undeclared contract value: ' + name + '.' + port)
                    self.external_values[name + '.' + port] = value
            elif node['object_spec']:
                if not self.objects.enabled: self.issue(name, 'Native object queries require explicit object snapshots')
            elif element.tag not in BUILTINS and not node['journal'] and not operation(element):
                self.issue(name, 'Unsupported node: ' + element.tag)
            if element.tag == 'Function' and element.get('MethodName') not in FUNCTIONS and not operation(element) and not contract:
                self.issue(name, 'Unsupported function: ' + str(element.get('MethodName')))
            if element.tag == 'IfFunction' and element.get('MethodName') not in FUNCTIONS and not comparison(element):
                self.issue(name, 'Unsupported comparison')
            if comparison(element) and 'Op' in constants and constants['Op'] not in COMPARE:
                self.issue(name, 'Unsupported comparison operator: ' + str(constants['Op']))
            if element.tag == 'State':
                typ = element.get('TypeT')
                if 'DefaultValue' not in constants: self.issue(name, 'Missing explicit state default')
                self.states[name] = constants.get('DefaultValue')
                if typ == 'bool': self.enum_values[name] = [False, True]
                elif typ == 'wh::questmodule::QuestProgress': self.enum_values[name] = ['None','Active','Done','Failed']
                elif typ in types:
                    values = {tuple(v) for _,v in types[typ]}
                    if len(values) != 1: self.issue(name, 'Ambiguous state enumeration: ' + typ)
                    else: self.enum_values[name] = list(next(iter(values)))
                    self.used_files.update(p for p,_ in types[typ])
                elif typ not in ('int', 'float', 'string'):
                    self.issue(name, 'Unknown state type: ' + str(typ))
            if element.tag == 'Timer' and constants.get('TimeType') != 'GameTime':
                self.issue(name, 'Only explicit GameTime timers are emulated')
            if element.tag == 'InstantSendMessage':
                if not element.get('MessageType'): self.issue(name, 'MessageType must be explicit')
                for asset in element.findall('Asset'):
                    port, alias = asset.get('Name'), asset.get('Alias')
                    if not alias: self.issue(name, 'Message asset has no alias')
            if element.tag in ('HasteTrigger', 'GameStart'):
                self.event_ports.add(name + ('.OnTrigger' if element.tag == 'HasteTrigger' else '.OnStart'))
            for ref, port in edges:
                if port in node['unresolved']:
                    node['unresolved'][port].append(ref)
                    self.routes[ref].append(('unsupported', name + '.' + port))
                    continue
                control = self._control(name, port)
                if control: self.routes[ref].append(('deliver', (name, port)))
                else:
                    if port in node['values']:
                        native_unknown = (element.tag not in BUILTINS or
                            element.tag == 'Function' and element.get('MethodName') not in FUNCTIONS)
                        if native_unknown and not node['journal'] and not contract and not operation(element) and not node['object_spec']:
                            # Preserve unknown fan-in instead of terminating a
                            # whole-package audit at its first opaque interface.
                            # It is not guessed to be either data or execution.
                            refs = [node['values'].pop(port), ref]
                            node['unresolved'][port] = refs
                            self.issue(name, 'Native input wiring is not modeled: ' + port)
                            for source_ref in refs:
                                self.routes[source_ref].append(('unsupported', name + '.' + port))
                            continue
                        raise ValueError('Multiple data drivers: ' + name + '.' + port)
                    node['values'][port] = ref
            if contract:
                declared = set(contract.get('inputs', {}))
                used = {p for _, p in edges} | {a.get('Name') for a in element.findall('Asset')}
                if native_call: used |= set(constants)
                if used - declared: self.issue(name, 'Contract missing inputs: ' + ', '.join(sorted(used - declared)))

    def _known_ref(self, ref):
        if ref in self.input_types or ref in self.event_ports or ref in self.aliases: return True
        if ref in self.relay_outputs: return True
        if ref in self.dialogue_outputs: return True
        name, _, port = ref.rpartition('.')
        if name not in self.nodes: return False
        kind = self.nodes[name]['kind']
        if name in self.contracts: return port in self.contracts[name].get('outputs', {})
        for spec in ('object_spec', 'value_spec'):
            if self.nodes[name].get(spec): return port in self.nodes[name][spec]['outputs']
        pure = operation(self.nodes[name]['element'])
        if pure: return port == pure[0]
        if kind == 'State':
            options = [('True' if v else 'False') if type(v) is bool else str(v) for v in self.enum_values.get(name, [])]
            return port in ('State','OnExec','OnChanged', *options, *('On'+v for v in options))
        return (kind == 'Function' and port == 'bool'
            or kind == 'AIConceptSignalTrigger' and port == 'OnNotification'
            or kind == 'ItemDescriptorTrigger' and port in ('OnAcquire', 'OnTargetAmountAcquire')
            or kind in ('If','IfFunction') and port in ('True','False')
            or kind == 'Timer' and port == 'OnFinished'
            or kind == 'TriggerSequence' and port in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' and len(port) == 1
            or kind == 'HasteTrigger' and port == 'OnTrigger'
            or kind == 'InstantSendMessage' and port == 'OnExec'
            or kind == 'GameStart' and port == 'OnStart')

    def _audit(self):
        for ref in set(self.routes) | set(self.aliases.values()) | {
                v for n in self.nodes.values() for v in n['values'].values()}:
            if not self._known_ref(ref): self.issue(ref, 'Unresolved or unsupported source port')
        allowed = {'State': {'DefaultValue','Value'}, 'If': {'Condition'},
            'AIConceptSignalTrigger': {'IsActive'},
            'SetGameContext': {'Context', 'IsActive'},
            'ItemDescriptorTrigger': {'Inventory', 'ItemDescriptor', 'IsActive'},
            'IfFunction': {'ValueA','ValueB','Op','A','B','Value'}, 'Function': {'A','B','Value'},
            'Timer': {'Duration','TimeType'}, 'Trace': {'Value'},
            'TriggerSequence': set(), 'HasteTrigger': set(), 'GameStart': set()}
        for name, node in self.nodes.items():
            if node['kind'] == 'SetGameContext':
                if not isinstance(node['constants'].get('Context'), str) or not node['constants']['Context']:
                    self.issue(name, 'Game context requires an explicit constant identity')
                elif sum(n['kind'] == 'SetGameContext' and n['constants'].get('Context') == node['constants']['Context']
                         for n in self.nodes.values()) != 1:
                    self.issue(name, 'Multiple game-context writers require native arbitration semantics')
                if set(node['constants']) | set(node['values']) != {'Context', 'IsActive'}:
                    self.issue(name, 'Game context requires Context and IsActive only')
            if node['kind'] == 'AIConceptSignalTrigger':
                if not node['element'].get('NotificationName'):
                    self.issue(name, 'AI signal listener requires NotificationName')
                if set(node['constants']) | set(node['values']) != {'IsActive'}:
                    self.issue(name, 'AI signal listener requires IsActive only')
            if name in self.contracts or node['journal']: continue
            if name in self.native_functions: continue  # Interface remains explicitly unsupported without a contract.
            if node['kind'] == 'InstantSendMessage':
                ports = set(node['values']) | set(node['constants']) | {
                    a.get('Name') for a in node['element'].findall('Asset')}
                for port in ports:
                    if not isinstance(port, str) or not (port == 'Receiver' or
                            port.startswith('Content_') and len(port) > len('Content_')):
                        self.issue(name, 'Unsupported message input: ' + str(port))
                if 'Receiver' not in ports: self.issue(name, 'Message receiver is not bound')
            pure = operation(node['element'])
            spec = node.get('value_spec') or node.get('object_spec')
            permitted = spec['inputs'] if spec else pure[1] if pure else allowed.get(node['kind'])
            bound = set(node['values']) | set(node['constants']) | {a.get('Name') for a in node['element'].findall('Asset')}
            if node['kind'] == 'ItemDescriptorTrigger' and bound != allowed['ItemDescriptorTrigger']:
                self.issue(name, 'Item tracking requires explicit Inventory, ItemDescriptor and IsActive')
            for port in bound:
                if permitted is not None and port not in permitted:
                    self.issue(name, 'Unsupported input: ' + port)
            if spec:
                for port in spec.get('required', spec['inputs']) - bound:
                    self.issue(name, 'Missing explicit value input: ' + port)
        for actions in self.routes.values():
            for kind, payload in actions:
                if kind != 'deliver': continue
                name, port = payload
                if self.nodes[name]['kind'] == 'State' and port != 'Exec':
                    value = {'True':True,'False':False}.get(port[3:], port[3:])
                    if value not in self.enum_values.get(name, []):
                        self.issue(name, 'Unsupported state setter: ' + port)

    def _control(self, name, port):
        node = self.nodes[name]
        kind = node['kind']
        if name in self.contracts: return self.contracts[name].get('inputs', {}).get(port) == 'trigger'
        if name in self.native_functions: return port == 'Exec'
        if kind == 'State': return port == 'Exec' or port.startswith('Set')
        if kind == 'Timer': return port in ('SetRunning', 'SetStopped')
        return kind in ('If', 'IfFunction', 'TriggerSequence', 'Trace', 'InstantSendMessage') and port == 'Exec'

    def _args(self, name, trail=()):
        node = self.nodes[name]
        result = dict(node['constants'])
        result.update({k: self.value(ref, trail) for k, ref in node['values'].items()})
        if name not in self.contracts or name in self.native_functions:
            for asset in node['element'].findall('Asset'):
                alias = asset.get('Alias')
                if alias not in self.assets: raise Unsupported('Unbound asset: ' + str(alias))
                result[asset.get('Name')] = copy.deepcopy(self.assets[alias])
        return result

    def _arg(self, name, port, trail=()):
        node = self.nodes[name]
        if port in node['values']: return self.value(node['values'][port], trail)
        if port in node['constants']: return copy.deepcopy(node['constants'][port])
        for asset in node['element'].findall('Asset'):
            if asset.get('Name') == port:
                alias = asset.get('Alias')
                if alias not in self.assets: raise Unsupported('Unbound asset: ' + str(alias))
                return copy.deepcopy(self.assets[alias])
        raise Unsupported('Unbound value input: ' + name + '.' + port)

    def value(self, ref, trail=()):
        if ref in trail: raise Unsupported('Cyclic data dependency: ' + ' -> '.join((*trail, ref)))
        trail = (*trail, ref)
        if ref in self.aliases: return self.value(self.aliases[ref], trail)
        if ref in self.inputs: return self.inputs[ref]
        if ref in self.external_values: return self.external_values[ref]
        if ref in self.asset_inputs:
            alias = self.asset_inputs[ref]
            if alias not in self.assets: raise Unsupported('Unbound asset: ' + str(alias))
            return copy.deepcopy(self.assets[alias])
        name, _, port = ref.rpartition('.')
        if name in self.nodes:
            node = self.nodes[name]
            if name not in self.contracts and node.get('object_spec'):
                self.tick(); self.visited[name] += 1
                return self.objects.read(name, node['object_spec'], port, self._args(name, trail))
            if node.get('value_spec'):
                self.tick(); self.visited[name] += 1
                return collection_nodes.evaluate(node['value_spec'], port, lambda p: self._arg(name, p, trail))
            pure = operation(self.nodes[name]['element'])
            if pure and port == pure[0]:
                self.visited[name] += 1
                return pure[2](self._args(name, trail))
        if name in self.states:
            if port == 'State': return self.states[name]
            options = self.enum_values.get(name, [])
            enum = {'True': True, 'False': False}.get(port, port)
            if enum in options: return self.states[name] == enum
        if name in self.nodes and self.nodes[name]['kind'] == 'Function':
            node = self.nodes[name]
            method = node['element'].get('MethodName')
            if method in FUNCTIONS and port == 'bool':
                self.visited[name] += 1
                return FUNCTIONS[method](self._args(name, trail))
        raise Unsupported('Unbound or unsupported value: ' + ref)

    def set_inputs(self, values):
        for ref, value in values.items():
            if ref.rpartition('.')[0] in self.native_functions:
                raise ValueError('Use native_result for call outputs: ' + ref)
            if ref in self.aliases: raise ValueError('Cannot override connected input: ' + ref)
            typ = self.input_types.get(ref)
            if not typ or typ == 'trigger': raise ValueError('Not a declared value input: ' + ref)
            if typ == 'bool': boolean(value)
            if typ == 'int' and type(value) is not int: raise ValueError('Expected integer: ' + ref)
            (self.external_values if ref.rpartition('.')[0] in self.contracts else self.inputs)[ref] = value
            self.record('input', port=ref, value=value)

    def emit(self, event):
        self.stack.append(('event', event))

    def game_context(self, context):
        """Read a single live context binding; native multi-writer arbitration is unknown."""
        matches = [name for name, node in self.nodes.items()
                   if node['kind'] == 'SetGameContext' and node['constants'].get('Context') == context]
        if not isinstance(context, str) or not context or len(matches) != 1:
            raise Unsupported('Missing or ambiguous game context: ' + str(context))
        return boolean(self._args(matches[0])['IsActive'])

    def signal(self, notification):
        """Deliver a named controller signal to active declared listeners.

        Stable XML order is emulator policy; native scheduling is not modeled.
        Missing listeners fail explicitly rather than pretending delivery.
        """
        listeners = [(name, node) for name, node in self.nodes.items()
                     if node['kind'] == 'AIConceptSignalTrigger'
                     and node['element'].get('NotificationName') == notification]
        if not isinstance(notification, str) or not notification or not listeners:
            raise Unsupported('Unbound AI concept signal: ' + str(notification))
        events = [name + '.OnNotification' for name, _ in listeners
                  if boolean(self._args(name)['IsActive'])]
        self.record('ai_concept_signal', notification=notification, listeners=events,
                    boundary='modeled_signal_delivery')
        for event in reversed(events): self.emit(event)
        self.drain()

    def fire(self, event):
        if event.rpartition('.')[0] in self.native_functions:
            raise ValueError('Use native_result for call completion: ' + event)
        if event not in self.event_ports:
            raise ValueError('Not an exposed trigger input or contracted engine event: ' + event)
        self.record('stimulus', port=event, boundary='engine_contract' if event.rpartition('.')[0] in self.contracts else 'graph_input')
        self.emit(event)
        self.drain()

    def _state(self, name, value):
        options = self.enum_values.get(name)
        if options is not None and value not in options: raise Unsupported('Invalid state enumeration: ' + name)
        before = self.states[name]
        self.states[name] = value
        self.record('state', node=name, before=before, value=value)
        self.observe_contracts()
        events = [name + '.OnExec']
        if before != value:
            label = str(value) if type(value) is not bool else ('True' if value else 'False')
            events = [name + '.On' + label, name + '.OnChanged', *events]
        for event in reversed(events): self.emit(event)

    def _deliver(self, name, port):
        self.visited[name] += 1
        node = self.nodes[name]
        kind = node['kind']
        if name in self.native_functions:
            self.native.request(name)
            return
        if name in self.contracts:
            self.record('external_request', node=name, port=port, inputs=self._args(name))
            return
        if name in self.dialogues:
            self.dialogues[name].request(port)
            return
        if kind == 'State':
            if port == 'Exec': value = self._args(name)['Value']
            elif port.startswith('Set'):
                suffix = port[3:]
                value = {'True': True, 'False': False}.get(suffix, suffix)
            else: raise Unsupported('State operation: ' + port)
            self._state(name, value)
        elif kind in ('If', 'IfFunction'):
            args = self._args(name)
            if kind == 'If': outcome = boolean(args['Condition'])
            else:
                method = node['element'].get('MethodName')
                if method in FUNCTIONS:
                    outcome = FUNCTIONS[method](args)
                elif comparison(node['element']):
                    outcome = compare(args, node['element'].get('TypeT'))
                else:
                    raise Unsupported('Unsupported comparison: ' + name)
            self.record('condition', node=name, inputs=args, result=outcome)
            self.emit(name + ('.True' if outcome else '.False'))
        elif kind == 'TriggerSequence':
            for letter in reversed('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
                if name + '.' + letter in self.routes: self.emit(name + '.' + letter)
        elif kind == 'Timer':
            args = self._args(name)
            if args.get('TimeType') != 'GameTime': raise Unsupported('Timer clock: ' + name)
            generation = self.generations.get(name, 0) + 1
            self.generations[name] = generation
            if port == 'SetRunning':
                self.serial += 1
                deadline = self.now + duration(args['Duration'])
                heapq.heappush(self.timers, (deadline, self.serial, name, generation))
                self.record('timer_start', node=name, deadline_us=deadline)
            else: self.record('timer_stop', node=name)
        elif kind == 'Trace': self.record('trace', node=name, inputs=self._args(name))
        elif kind == 'InstantSendMessage':
            values = self._args(name)
            typ, receiver = node['element'].get('MessageType'), values.get('Receiver')
            if not typ or not isinstance(receiver, str) or not receiver:
                raise Unsupported('Message needs an explicit type and a nonempty receiver identity: ' + name)
            if any(k != 'Receiver' and (not k.startswith('Content_') or len(k) <= 8) for k in values):
                raise Unsupported('Unknown message payload input: ' + name)
            self.message_serial += 1
            message = dict(id=self.message_serial, node=name, message_type=typ, receiver=receiver,
                           content={k[8:]:copy.deepcopy(v) for k,v in values.items() if k.startswith('Content_')})
            self.messages.append(message)
            self.record('message_queued', **copy.deepcopy(message))
            # Dispatch completion is not a receiver acknowledgement. Delivery
            # and a consumer callback are separate, observable operations.
            self.emit(name + '.OnExec')
        else: raise Unsupported('Unsupported execution: ' + kind + '.' + port)

    def drain(self):
        while self.stack:
            self.tick()
            kind, payload = self.stack.pop()
            if kind == 'deliver': self._deliver(*payload)
            elif kind in ('event', 'relay'):
                self.events[payload] += 1
                self.record('event', port=payload)
                self.stack.extend(reversed(self.routes.get(payload, [])))
            elif kind == 'unsupported': raise Unsupported('Native input wiring is not modeled: ' + payload)
            else: raise Unsupported('Unknown execution frame')

    def observe_contracts(self):
        """Record engine inputs such as IsActive without faking engine results."""
        for name, contract in self.contracts.items():
            if name in self.native_functions: continue  # Evaluate arguments only when the call executes.
            values = self._args(name)
            values.update({a.get('Name'): {'asset_alias':a.get('Alias')}
                           for a in self.nodes[name]['element'].findall('Asset')})
            observed = {p:values[p] for p, typ in contract.get('inputs', {}).items()
                        if typ != 'trigger' and p in values}
            missing = [p for p, typ in contract.get('inputs', {}).items() if typ != 'trigger' and p not in values]
            if missing: raise Unsupported('Unbound engine inputs: ' + name + '.' + ','.join(missing))
            if observed != self.external_last.get(name):
                self.record('external_inputs', node=name, values=observed)
                self.external_last[name] = copy.deepcopy(observed)

    def next_deadline(self):
        while self.timers and self.generations.get(self.timers[0][2]) != self.timers[0][3]: heapq.heappop(self.timers)
        return self.timers[0][0] if self.timers else None

    def advance_to(self, target):
        if target < self.now: raise ValueError('Clock cannot move backwards')
        while self.next_deadline() is not None and self.next_deadline() <= target:
            self.now, _, name, _ = heapq.heappop(self.timers)
            self.record('timer_finish', node=name)
            self.emit(name + '.OnFinished'); self.drain()
        self.now = target

    def snapshot(self):
        if self.stack: raise ValueError('Snapshots require a settled graph')
        return copy.deepcopy(dict(fingerprint=self.fingerprint, now=self.now, serial=self.serial,
            states=self.states, inputs=self.inputs, external_values=self.external_values,
            timers=self.timers, generations=self.generations,
            messages=self.messages, message_serial=self.message_serial,
            dialogues={n:d.snapshot() for n,d in self.dialogues.items()}, native=self.native.snapshot(),
            objects=self.objects.records))

    def restore(self, snapshot):
        if snapshot.get('fingerprint') != self.fingerprint: raise ValueError('Snapshot graph/contract mismatch')
        for key in ('now', 'serial', 'states', 'inputs', 'external_values', 'timers', 'generations', 'messages', 'message_serial'):
            setattr(self, key, copy.deepcopy(snapshot[key]))
        self.timers = [tuple(t) for t in self.timers]; heapq.heapify(self.timers)
        for name, state in snapshot['dialogues'].items(): self.dialogues[name].state = copy.deepcopy(state)
        self.native.restore(snapshot['native'])
        self.objects.records = copy.deepcopy(snapshot['objects'])
        self.record('restore', fingerprint=self.fingerprint)

    def report(self):
        return dict(entry=self.entry, fingerprint=self.fingerprint,
            files={p: self.source.hashes[p] for p in sorted(self.used_files)},
            nodes=len(self.nodes), visited=sorted(self.visited),
            unvisited=sorted(set(self.nodes) - set(self.visited)),
            unsupported=self.unsupported, external_contracts=self.contracts,
            states=self.states, time_us=self.now, pending_messages=self.messages,
            pending_native_calls=self.native.pending,
            object_snapshots=self.objects.records,
            unresolved_native_bindings=[dict(node=name, port=port, sources=refs)
                for name, node in self.nodes.items() for port, refs in node.get('unresolved', {}).items()],
            dialogues={n:d.snapshot() for n,d in self.dialogues.items()})
