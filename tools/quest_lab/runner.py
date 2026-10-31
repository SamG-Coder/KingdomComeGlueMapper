"""Scenario orchestration, bounded clocks, assertions and replay snapshots."""
import copy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as E

from . import BudgetExceeded, Unsupported, VERSION
from .behavior import Behavior
from .expressions import duration
from .graph import Graph
from .source import Source


class ScenarioTimeout(AssertionError):
    pass


class Runner:
    def __init__(self, scenario, base=Path('.'), source=None):
        if scenario.get('schema') != VERSION: raise ValueError('Unsupported scenario schema')
        allowed = {'schema','name','description','source','entry','entry_file','inputs','contracts','behaviors','steps','budget',
                   'assets','message_routes','objects'}
        if set(scenario) - allowed: raise ValueError('Unknown scenario keys: ' + ', '.join(sorted(set(scenario)-allowed)))
        if not scenario.get('steps'): raise ValueError('Scenario must contain steps and assertions')
        budget = scenario.get('budget', 100000)
        if type(budget) is not int or not 1 <= budget <= 1000000: raise ValueError('Budget must be 1..1000000')
        self.scenario = copy.deepcopy(scenario)
        self.base = Path(base)
        self.source = source or Source(self.base / scenario['source'])
        self.graph = Graph(self.source, scenario['entry'], scenario.get('contracts'), budget, scenario.get('entry_file'),
                           scenario.get('assets'), scenario.get('objects'))
        self.behaviors, self.saved, self.assertions = {}, {}, []
        self.error = None
        for item in scenario.get('behaviors', []):
            if set(item) - {'id','file','path','variables','contracts','callbacks'}: raise ValueError('Unknown behavior configuration')
            if ('file' in item) == ('path' in item): raise ValueError('Behavior needs exactly one package file or external fixture path')
            name = item['id']
            if name in self.behaviors: raise ValueError('Duplicate behavior controller')
            if 'path' in item:
                blob = (self.base / item['path']).read_bytes()
            else:
                key = item['file'].replace('\\','/').lower()
                root = self.source.files.get(key)
                if root is None: raise ValueError('Missing behavior XML: ' + key)
                blob = E.tostring(root)
            self.behaviors[name] = Behavior(name, blob, self.graph,
                item.get('variables'), item.get('contracts'), item.get('callbacks'))
        self.message_routes = {}
        for route in scenario.get('message_routes', []):
            required = {'receiver','message_type','controller','inbox','sender'}
            if set(route) != required: raise ValueError('Message route needs receiver, message_type, controller, inbox and sender')
            if any(not isinstance(route[k], str) or not route[k] for k in required - {'sender'}):
                raise ValueError('Message routing identities must be nonempty strings')
            if route['controller'] not in self.behaviors: raise ValueError('Unknown message receiver controller: ' + route['controller'])
            if not isinstance(route['sender'], dict): raise ValueError('Message sender must be an explicit object')
            identity = (route['receiver'], route['message_type'])
            if identity in self.message_routes: raise ValueError('Ambiguous message route: ' + repr(identity))
            self.message_routes[identity] = copy.deepcopy(route)
        self.routing_fingerprint = hashlib.sha256(json.dumps(scenario.get('message_routes', []), sort_keys=True).encode()).hexdigest()
        self.initialized = False

    def settle(self):
        while True:
            self.graph.drain()
            for behavior in self.behaviors.values(): behavior.pump()
            self.graph.advance_to(self.graph.now)
            self.graph.observe_contracts()
            index = next((i for i,m in enumerate(self.graph.messages)
                          if (m['receiver'], m['message_type']) in self.message_routes), None)
            if index is None: break
            self.graph.tick()
            message = self.graph.messages.pop(index)
            route = self.message_routes[(message['receiver'], message['message_type'])]
            self.graph.record('message_delivered', **copy.deepcopy(message), controller=route['controller'], inbox=route['inbox'])
            self.behaviors[route['controller']].message(route['inbox'], message['content'], route['sender'])

    def advance(self, amount, mode='normal', until=None):
        if mode not in ('normal', 'skip'): raise ValueError('Unknown clock mode')
        target = self.graph.now + duration(amount)
        self.graph.record('advance', mode=mode, target_us=target,
            policy='All modeled GameTime deadlines are processed in both modes; native skip/LOD is not emulated')
        self.settle()
        while True:
            if until and until(): return True
            times = [x for x in [self.graph.next_deadline(), *(b.next_deadline() for b in self.behaviors.values())] if x is not None]
            if not times or min(times) > target: break
            self.graph.advance_to(min(times)); self.settle()
        self.graph.advance_to(target); self.settle()
        return bool(until and until())

    def check(self, label, actual, expected):
        passed = actual == expected and (type(actual) is type(expected) or isinstance(actual, (dict, list)))
        result = dict(label=label, time_us=self.graph.now, expected=expected, actual=copy.deepcopy(actual), passed=passed)
        self.assertions.append(result)
        self.graph.record('assertion', **{k:v for k,v in result.items() if k != 'time_us'})
        if not passed: raise AssertionError(label + ': expected ' + repr(expected) + ', got ' + repr(actual))

    def snapshot(self):
        return dict(graph=self.graph.snapshot(), behaviors={n:b.snapshot() for n,b in self.behaviors.items()},
                    routing_fingerprint=self.routing_fingerprint)

    def restore(self, snapshot):
        if set(snapshot['behaviors']) != set(self.behaviors): raise ValueError('Snapshot controller mismatch')
        if snapshot.get('routing_fingerprint') != self.routing_fingerprint: raise ValueError('Snapshot message routing mismatch')
        # Validate all identities before mutating any live state.
        if snapshot['graph']['fingerprint'] != self.graph.fingerprint: raise ValueError('Snapshot graph mismatch')
        for name, behavior in self.behaviors.items():
            if snapshot['behaviors'][name]['fingerprint'] != behavior.fingerprint:
                raise ValueError('Snapshot behavior mismatch')
        self.graph.restore(snapshot['graph'])
        for name, behavior in self.behaviors.items(): behavior.restore(snapshot['behaviors'][name])

    def step(self, step):
        action = step['action']
        fields = {
            'fire': {'port'}, 'set': {'values'}, 'advance': {'duration','mode'},
            'assert': {'values','states','events','label'}, 'await': {'port','count','timeout'},
            'save': {'name'}, 'load': {'name'}, 'behavior_start': {'controller','tree','task'},
            'behavior_set': {'controller','values'}, 'message': {'controller','inbox','value','sender'},
            'outcome': {'controller','task','result'},
            'assert_behavior': {'controller','variables','availability','tasks','label'},
            'assert_messages': {'pending','count','label'},
            'dialogue_start': {'dialogue','request'}, 'dialogue_choose': {'dialogue','sequence'},
            'dialogue_end': {'dialogue'}, 'assert_dialogue': {'dialogue','choices','state','label'},
            'native_result': {'id','node','result','values','events'},
            'assert_native': {'pending','count','label'},
            'object_snapshot': {'identity','facets'},
            'item_acquired': {'inventory', 'item', 'amount'},
        }
        if action not in fields or set(step) - (fields[action] | {'action'}):
            raise ValueError('Unknown scenario action/fields: ' + action)
        if action == 'fire': self.graph.fire(step['port'])
        elif action == 'item_acquired':
            from .inventory import acquire
            acquire(self.graph, step['inventory'], step['item'], step['amount'])
        elif action == 'set': self.graph.set_inputs(step['values'])
        elif action == 'object_snapshot': self.graph.objects.replace(step['identity'], step['facets'])
        elif action == 'advance': self.advance(step['duration'], step.get('mode','normal'))
        elif action == 'assert':
            count = 0
            for field in ('values','states','events'):
                for key, expected in step.get(field, {}).items():
                    count += 1
                    if field == 'values': actual = self.graph.value(key)
                    elif field == 'states': actual = self.graph.states[key]
                    else:
                        if key not in self.graph.output_ports and key not in self.graph.routes and key not in self.graph.event_ports:
                            raise ValueError('Unknown asserted event: ' + key)
                        actual = self.graph.events[key]
                    self.check(step.get('label',field + ':' + key), actual, expected)
            if not count: raise ValueError('Empty assertion')
        elif action == 'await':
            port = step['port']; count = step.get('count', 1)
            if port not in self.graph.output_ports and port not in self.graph.event_ports:
                raise ValueError('Unknown await port: ' + port)
            if type(count) is not int or count < 1: raise ValueError('Await count must be positive')
            reached = self.advance(step['timeout'], until=lambda: self.graph.events[port] >= count)
            self.assertions.append(dict(label='await:'+port, time_us=self.graph.now, expected=count,
                actual=self.graph.events[port], passed=reached))
            if not reached: raise ScenarioTimeout('Timed out waiting for ' + port)
        elif action == 'save':
            self.saved[step['name']] = self.snapshot()
            self.graph.record('save', name=step['name'])
        elif action == 'load': self.restore(self.saved[step['name']])
        elif action == 'behavior_start': self.behaviors[step['controller']].start(step['tree'], step.get('task'))
        elif action == 'behavior_set': self.behaviors[step['controller']].set_variables(step['values'])
        elif action == 'message': self.behaviors[step['controller']].message(step['inbox'], step['value'], step['sender'])
        elif action == 'outcome': self.behaviors[step['controller']].resolve(step['task'], step['result'])
        elif action == 'native_result':
            self.graph.native.resolve(step['id'], step['node'], step['result'], step.get('values'), step.get('events'))
        elif action == 'assert_native':
            if 'count' not in step and 'pending' not in step: raise ValueError('Empty native call assertion')
            if 'count' in step: self.check(step.get('label', 'pending native calls'), len(self.graph.native.pending), step['count'])
            if 'pending' in step: self.check(step.get('label', 'native requests'), self.graph.native.pending, step['pending'])
        elif action == 'dialogue_start': self.graph.dialogues[step['dialogue']].start(step.get('request', False))
        elif action == 'dialogue_choose': self.graph.dialogues[step['dialogue']].choose(step['sequence'])
        elif action == 'dialogue_end': self.graph.dialogues[step['dialogue']].end()
        elif action == 'assert_dialogue':
            dialogue = self.graph.dialogues[step['dialogue']]
            if 'choices' not in step and not step.get('state'): raise ValueError('Empty dialogue assertion')
            if 'choices' in step:
                self.check(step.get('label', step['dialogue'] + ':choices'), dialogue.choices(), step['choices'])
            for key, expected in step.get('state', {}).items():
                self.check(step.get('label', step['dialogue'] + ':' + key), dialogue.state[key], expected)
        elif action == 'assert_messages':
            if 'count' not in step and 'pending' not in step: raise ValueError('Empty message assertion')
            if 'count' in step:
                self.check(step.get('label', 'pending message count'), len(self.graph.messages), step['count'])
            if 'pending' in step:
                pending = [{k:copy.deepcopy(m[k]) for k in ('message_type','receiver','content')} for m in self.graph.messages]
                self.check(step.get('label', 'pending messages'), pending, step['pending'])
        elif action == 'assert_behavior':
            b = self.behaviors[step['controller']]
            mapping = dict(variables=b.variables, availability=b.availability,
                           tasks={n:t['status'] for n,t in b.tasks.items()})
            count = 0
            for field in mapping:
                for key, expected in step.get(field, {}).items():
                    count += 1
                    self.check(step.get('label', step['controller']+':'+field+':'+key), mapping[field][key], expected)
            if not count: raise ValueError('Empty behavior assertion')
        self.settle()

    def run(self):
        index = None
        status = 'PASS'
        try:
            self.graph.set_inputs(self.scenario.get('inputs', {}))
            self.initialized = True
            for index, step in enumerate(self.scenario['steps']):
                self.graph.record('step', index=index, action=step.get('action'))
                self.step(step)
            if not self.assertions: raise ValueError('No assertions; execution alone is not a passing test')
            if self.graph.unsupported or any(b.unsupported for b in self.behaviors.values()):
                status = 'UNSUPPORTED'
                self.error = 'Loaded program has unsupported operations or unresolved bindings; inspect coverage'
            if self.graph.messages:
                status = 'UNSUPPORTED'
                self.error = 'Messages have no configured receiver route; delivery/handling is not verified'
            if self.graph.native.pending:
                status = 'UNSUPPORTED'
                self.error = 'Native calls are awaiting explicit results; engine effects are not verified'
        except ScenarioTimeout as exc: status = 'TIMEOUT'; self.error = str(exc)
        except BudgetExceeded as exc: status = 'TIMEOUT'; self.error = str(exc)
        except Unsupported as exc: status = 'UNSUPPORTED'; self.error = str(exc)
        except AssertionError as exc: status = 'FAIL'; self.error = str(exc)
        except (ValueError, KeyError, TypeError, ZeroDivisionError, OverflowError) as exc:
            status = 'ERROR'; self.error = str(exc)
        return dict(schema=VERSION, name=self.scenario.get('name','Unnamed scenario'), status=status,
            error=self.error, stopped_at_step=index, native_runtime_verified=False,
            scope='Emulated converted control flow with explicitly supplied engine outcomes',
            scenario_sha256=hashlib.sha256(json.dumps(self.scenario, sort_keys=True).encode()).hexdigest(),
            graph=self.graph.report(), behaviors={n:b.report() for n,b in self.behaviors.items()},
            assertions=self.assertions, timeline=self.graph.trace, snapshots=self.saved)


def run_file(path):
    path = Path(path)
    scenario = {}
    try:
        scenario = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(scenario, dict):
            scenario = {}
            raise ValueError('Scenario must be a JSON object')
        return Runner(scenario, path.parent).run()
    except (ValueError, KeyError, OSError, E.ParseError) as exc:
        return dict(schema=VERSION, name=scenario.get('name',path.stem),
            status='UNSUPPORTED' if isinstance(exc, Unsupported) else 'ERROR', error=str(exc),
            native_runtime_verified=False, assertions=[], timeline=[])
