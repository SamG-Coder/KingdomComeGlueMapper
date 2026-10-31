"""Resumable interpreter for a bounded subset of emitted XGen behavior XML.

This executes controller logic and availability changes. It does not approximate
KCD2's scheduler arbitration, navmesh, animation or perception. Those boundaries
must be supplied as explicit scenario outcomes.
"""
import copy
import hashlib
import json
import xml.etree.ElementTree as E

from . import Unsupported
from .expressions import assignments, boolean, duration, evaluate, literal


WRAPPERS = {'Sequence', 'Then', 'Else', 'Behavior', 'OnInit', 'Child', 'DefaultBranch'}
SUPPORTED = WRAPPERS | {'Root', 'Success', 'Fail', 'Expression', 'IfCondition',
    'IfElseCondition', 'Switch', 'Selector', 'While', 'Wait', 'ProcessMessage',
    'SmartObjSetBehaviorState', 'Callback_empty'}


class Behavior:
    def __init__(self, name, blob, graph, variables=None, contracts=None, callbacks=None):
        self.name, self.graph = name, graph
        self.contracts, self.callbacks = copy.deepcopy(contracts or {}), dict(callbacks or {})
        self.fingerprint = hashlib.sha256(blob + json.dumps([self.contracts, self.callbacks], sort_keys=True).encode()).hexdigest()
        self.root = E.fromstring(blob)
        self.nodes, self.trees = {}, {}
        self.variables = copy.deepcopy(variables or {})
        self.availability, self.tasks, self.inboxes = {}, {}, {}
        self.unsupported, self.visited = [], set()
        self.serial = 0
        trees = [self.root] if self.root.tag == 'BehaviorTree' else self.root.findall('BehaviorTree')
        if not trees: raise ValueError('No behavior trees: ' + name)
        for tree in trees:
            key = tree.get('name')
            if not key or key in self.trees: raise ValueError('Duplicate or missing behavior tree name')
            root = tree.find('Root')
            if root is None: raise ValueError('Behavior tree has no Root: ' + key)
            self.trees[key] = key
            self._index(root, key)
        for tag, contract in self.contracts.items():
            if tag in SUPPORTED or tag.startswith('SendAIConceptSignal_'):
                raise ValueError('Cannot stub interpreted behavior: ' + tag)
            if not contract.get('reason'): raise ValueError('Behavior contract requires a reason')

    def _index(self, node, path):
        self.nodes[path] = node
        signal = node.tag.startswith('SendAIConceptSignal_') and len(node.tag) > len('SendAIConceptSignal_')
        if signal and (len(node) or node.attrib):
            self.unsupported.append(dict(node=path, reason='AI concept signal must be an argument-free leaf'))
        if node.tag not in SUPPORTED and node.tag not in self.contracts and not signal:
            self.unsupported.append(dict(node=path, reason='Unsupported behavior: ' + node.tag))
        if node.tag in self.contracts and len(node):
            self.unsupported.append(dict(node=path, reason='Engine contract cannot replace a subtree'))
        if node.tag == 'Wait' and node.get('skipInLOD', 'false') != 'false':
            self.unsupported.append(dict(node=path, reason='Native LOD-dependent wait skipping is not emulated'))
        for index, child in enumerate(node): self._index(child, path + '/' + str(index))

    def record(self, kind, **values):
        self.graph.record(kind, controller=self.name, **values)

    def start(self, tree, task=None):
        if tree not in self.trees: raise ValueError('Unknown behavior tree: ' + tree)
        self.serial += 1
        task = task or tree + ':' + str(self.serial)
        if task in self.tasks: raise ValueError('Duplicate task: ' + task)
        # Local variables cannot silently share controller globals across tasks.
        # Native declaration defaults are not invented: inputs/OnInit must bind them.
        self.tasks[task] = dict(frames=[dict(path=self.trees[tree], index=0)], status='running')
        self.record('behavior_start', task=task, tree=tree)
        self.pump()
        return task

    def set_variables(self, values):
        self.variables.update(copy.deepcopy(values))
        self.record('behavior_inputs', values=values)

    def message(self, inbox, value, sender):
        self.inboxes.setdefault(inbox, []).append(dict(value=copy.deepcopy(value), sender=copy.deepcopy(sender)))
        self.record('message', inbox=inbox, value=value, sender=sender)
        self.pump()

    def resolve(self, task_name, outcome):
        task = self.tasks[task_name]
        if task['status'] != 'external' or outcome not in ('success', 'fail'):
            raise ValueError('Outcome requires an outstanding external task and success/fail')
        self.record('behavior_outcome', task=task_name, node=task['frames'][-1]['path'], outcome=outcome)
        task['status'] = 'running'
        self._finish(task_name, outcome == 'success')
        self.pump()

    def _finish(self, name, result):
        task = self.tasks[name]
        frame = task['frames'].pop()
        self.record('behavior_return', task=name, node=frame['path'], result=result)
        if task['frames']: task['frames'][-1]['child_result'] = result
        else: task['status'] = 'success' if result else 'fail'

    def _push(self, task, frame, index):
        task['frames'].append(dict(path=frame['path'] + '/' + str(index), index=0))

    def _step(self, name):
        task = self.tasks[name]
        frame = task['frames'][-1]
        path = frame['path']; node = self.nodes[path]; tag = node.tag
        self.visited.add(path)
        self.graph.tick()
        def expr(text): return evaluate(text, self.variables)
        def finish(result=True): self._finish(name, result)
        if tag.startswith('SendAIConceptSignal_'):
            if len(node) or node.attrib:
                raise Unsupported('AI concept signal must be an argument-free leaf: ' + path)
            self.graph.signal(tag.removeprefix('SendAIConceptSignal_')); finish()
        elif tag in self.contracts:
            if len(node): raise Unsupported('Engine contract cannot replace a subtree: ' + path)
            task['status'] = 'external'
            self.record('behavior_request', task=name, node=path, operation=tag, attributes=dict(node.attrib))
        elif tag in WRAPPERS or tag == 'Root':
            if tag == 'Root' and any(c.tag not in ('OnInit', 'Behavior') for c in node):
                raise Unsupported('Root exit handlers need an unwind adapter: ' + path)
            if frame.pop('child_result', True) is False: finish(False); return
            index = frame['index']
            if index == len(node): finish(); return
            frame['index'] += 1
            if tag == 'Root':
                # OnInit is a lifecycle hook, not an XML-order behavior child.
                order = [i for i,c in enumerate(node) if c.tag == 'OnInit']
                order.extend(i for i,c in enumerate(node) if c.tag == 'Behavior')
                index = order[index]
            self._push(task, frame, index)
        elif tag in ('Success', 'Fail'): finish(tag == 'Success')
        elif tag == 'Expression':
            changes = assignments(node.get('expressions', ''), self.variables)
            self.record('assignment', task=name, node=path, changes=changes); finish()
        elif tag == 'SmartObjSetBehaviorState':
            behavior = expr(node.get('behaviors', ''))
            state = node.get('state')
            if not isinstance(behavior, str) or state not in ('Enabled','Disabled','NoRequests'):
                raise Unsupported('Unknown behavior availability: ' + path)
            self.availability[behavior] = state
            self.record('availability', task=name, behavior=behavior, state=state); finish()
        elif tag in ('IfCondition', 'IfElseCondition'):
            if 'child_result' in frame: finish(frame.pop('child_result')); return
            result = boolean(expr(node.get('condition', '')))
            self.record('behavior_condition', task=name, node=path, expression=node.get('condition'), result=result)
            if tag == 'IfCondition' and not result:
                flag = node.get('failOnCondition')
                if flag not in ('true','false'): raise Unsupported('Unknown failOnCondition: ' + path)
                finish(flag != 'true'); return
            index = 0 if result else 1
            if index >= len(node): raise Unsupported('Missing conditional branch: ' + path)
            self._push(task, frame, index)
        elif tag == 'Selector':
            if frame.pop('child_result', False): finish(); return
            index = frame['index']
            if index == len(node): finish(False); return
            frame['index'] += 1; self._push(task, frame, index)
        elif tag == 'Switch':
            if 'child_result' in frame: finish(frame.pop('child_result')); return
            for index, child in enumerate(node):
                if child.tag == 'DefaultBranch' or (child.tag == 'IfCondition' and boolean(expr(child.get('condition','')))):
                    self._push(task, frame, index); return
                if child.tag != 'IfCondition': raise Unsupported('Unsupported Switch branch: ' + child.tag)
            finish()
        elif tag == 'While':
            result = frame.pop('child_result', True)
            if not result and node.get('propagateChildFail') != 'false': finish(False); return
            if not boolean(expr(node.get('condition', ''))):
                if node.get('doFail') not in ('true','false'): raise Unsupported('Unknown While doFail')
                finish(node.get('doFail') == 'false'); return
            if len(node) != 1: raise Unsupported('While needs one child')
            self._push(task, frame, 0)
        elif tag == 'Wait':
            if literal(node.get('timeType','')) != 'GameTime' or node.get('variation',''):
                raise Unsupported('Wait requires GameTime and no variation: ' + path)
            if node.get('doFail') not in ('true','false'): raise Unsupported('Wait needs explicit doFail')
            if 'deadline' not in frame:
                text = str(literal(node.get('duration','')))
                frame['deadline'] = None if text == '-1' else self.graph.now + duration(text)
                self.record('behavior_wait', task=name, node=path, deadline_us=frame['deadline'])
            if frame['deadline'] is not None and frame['deadline'] <= self.graph.now:
                finish(node.get('doFail') == 'false')
            else: task['status'] = 'waiting'
        elif tag == 'ProcessMessage':
            if literal(node.get('timeout','')) not in (-1, '-1') or node.get('condition',''):
                raise Unsupported('Only unfiltered infinite mailbox waits are supported: ' + path)
            if 'message' not in frame:
                inbox = expr(node.get('inbox',''))
                messages = self.inboxes.get(inbox, [])
                if not messages: task['status'] = 'message'; return
                frame['message'] = messages.pop(0)
                for attr, key in (('variable','value'),('senderInfo','sender')):
                    variable = node.get(attr,'').removeprefix('$')
                    if not variable.isidentifier(): raise Unsupported('Invalid mailbox variable')
                    self.variables[variable] = frame['message'][key]
            if frame.pop('child_result', True) is False: finish(False); return
            index = frame['index']
            if index == len(node):
                answer = node.get('answerVar','').removeprefix('$')
                self.record('message_processed', task=name, answer=self.variables.get(answer)); finish(); return
            frame['index'] += 1; self._push(task, frame, index)
        elif tag == 'Callback_empty':
            event = literal(node.get('EventName',''))
            if event not in self.callbacks: raise Unsupported('Unbound behavior callback: ' + str(event))
            self.graph.fire(self.callbacks[event]); finish()
        else: raise Unsupported('Unsupported behavior operation: ' + tag + ' at ' + path)

    def pump(self):
        # Stable task creation order is the emulator policy. Native arbitration
        # and concurrent mailbox locking have not been replicated.
        for name, task in self.tasks.items():
            if task['status'] in ('waiting', 'message'): task['status'] = 'running'
            while task['status'] == 'running': self._step(name)

    def next_deadline(self):
        return min((t['frames'][-1]['deadline'] for t in self.tasks.values()
                    if t['status'] == 'waiting' and t['frames'][-1].get('deadline') is not None), default=None)

    def snapshot(self):
        return copy.deepcopy(dict(fingerprint=self.fingerprint, variables=self.variables,
            availability=self.availability, tasks=self.tasks, inboxes=self.inboxes, serial=self.serial))

    def restore(self, data):
        if data.get('fingerprint') != self.fingerprint: raise ValueError('Behavior snapshot mismatch')
        for key in ('variables','availability','tasks','inboxes','serial'):
            setattr(self, key, copy.deepcopy(data[key]))

    def report(self):
        return dict(fingerprint=self.fingerprint, variables=self.variables, availability=self.availability,
            tasks={k: v['status'] for k,v in self.tasks.items()}, visited=sorted(self.visited),
            unvisited=sorted(set(self.nodes)-self.visited), unsupported=self.unsupported,
            external_contracts=self.contracts)
