"""Translate complete source behavior trees, with explicit native contracts.

This stage never turns a list of actions into a startup script. Each node keeps
its parent, child order, variables and include scope. A tree with an unsupported
node or missing dependency is withheld as a whole, including its callers.
Native examples establish format compatibility, not a retail execution result.
"""
from collections import Counter
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_quest_bridge import OPERATIONS, QuestBridge, UnsupportedOperation
from campaign_quest_graph import identifier, xml
from campaign_sources import key
from quest_import import behavior_path, literal
from upgrade_map import read


# Attribute kinds come from the two shipped AI serializations. This is an
# operation ABI adapter, never a list of particular quests, actors or items.
CONTRACTS = {
    'Root': {'OneTimeOnly': 'value', 'FailState': 'value', 'saveVersion': 'value'},
    'Behavior': {'canSkip': 'value'}, 'OnInit': {'canSkip': 'value'},
    'Then': {'canSkip': 'value'}, 'Else': {'canSkip': 'value'},
    'Child': {'canSkip': 'value'}, 'OnSuccess': {'canSkip': 'value'},
    'OnFail': {'canSkip': 'value'},
    'Sequence': {}, 'Selector': {}, 'AtomicDecorator': {},
    'Switch': {}, 'DefaultBranch': {},
    'Success': {}, 'Fail': {}, 'Invertor': {}, 'SuppressFailure': {},
    'Parallel': {'successMode': 'value', 'failureMode': 'value'},
    'FuseBox': {'StatusPropagation': 'value', 'OneCleanup': 'value', 'saveVersion': 'value'},
    'IfCondition': {'failOnCondition': 'value', 'condition': 'expression'},
    'IfElseCondition': {'failOnCondition': 'value', 'condition': 'expression', 'saveVersion': 'value'},
    'While': {'doFail': 'value', 'propagateChildFail': 'value', 'condition': 'expression'},
    'Loop': {'count': 'expression'},
    'Expression': {'expressions': 'expression'},
    'VarOperation': {'varName': 'reference', 'targetVarName': 'reference',
                     'operation': 'value', 'argument': 'expression'},
    'VariableExistsGate': {'VarToTest': 'reference', 'VarIndexMode': 'value',
                           'FailSubtMissing': 'value', 'saveVersion': 'value'},
    'Wait': {'duration': 'string_expression', 'timeType': 'value', 'doFail': 'value',
             'variation': 'string_expression', 'skipInLOD': 'value'},
    'ProfileLoadedGate': {'LayerName': 'profile', 'NegateTo': 'value', 'RunLogic': 'value'},
    'IsLoadedGate': {'saveVersion': 'value'},
    'ForEach': {'startIndex': 'expression', 'step': 'expression', 'array': 'reference',
                'iterator': 'reference', 'value': 'reference', 'break': 'reference'},
    'GraphSearch': {'Origin': 'reference', 'Borders': 'reference', 'depth': 'expression',
                    'selection': 'value', 'SearchPattern': 'value', 'EdgePruning': 'string_expression',
                    'AllowedEdges': 'string_expression', 'SubGraph': 'string_expression',
                    'AllowSubtraph': 'value', 'includeOrigin': 'value', 'excludeOrigin': 'value',
                    'failOnEmpty': 'expression', 'SetOperationChoice': 'value', 'errorOnEmpty': 'value',
                    'shortCircuit': 'value', 'skipTraversed': 'value', 'id': 'value'},
    'LinkTagFilter': {'tag': 'string_expression', 'prune': 'value', 'negprune': 'value',
                      'Parent': 'reference', 'Child': 'reference', 'Data': 'reference'},
    'EntityClassFilter': {'Class': 'value', 'Source': 'value', 'prune': 'value', 'negprune': 'value',
                          'Parent': 'reference', 'Child': 'reference', 'id': 'value'},
    'Nodalyzer': {'Quantifiers': 'value', 'Parent': 'reference', 'Child': 'reference',
                  'saveVersion': 'value', 'id': 'value'},
    'GetSpatialInfo': {'In': 'reference', 'Out': 'reference', 'What': 'value'},
    'SetSpatialInfo': {'In': 'reference', 'Val': 'reference', 'What': 'value'},
    'Move': {'stopWithinDistance': 'expression', 'stopDistanceVariation': 'expression',
              'rayCasteFlee': 'expression', 'successDistance': 'expression',
              'destinationSpecification': 'reference', 'destinationSpecification2': 'reference',
              'destinationSpecification3': 'reference', 'speed': 'expression',
              'additionalParams': 'reference', 'pathFindingParams': 'reference',
              'staminaPolicy': 'reference', 'pathInfo': 'string_expression',
              'AnimationToPlay': 'string_expression', 'AnimationContext': 'string_expression'},
    'CreateItem': {'ItemGUID': 'string_expression', 'Amount': 'expression',
                   'CreatedItem': 'reference', 'Target': 'reference', 'NotifyUI': 'value'},
    'EquipItem': {'item': 'reference', 'Target': 'reference'},
    'UnEquipItem': {'item': 'reference', 'Target': 'reference'},
    'IncludeTree': {'File': 'include_file', 'Name': 'string_expression', 'nodeLabel': 'value'},
    'ProcessMessage': {'Atomic': 'value', 'timeout': 'string_expression', 'timeType': 'value',
                       'variable': 'reference', 'senderInfo': 'reference', 'inbox': 'mailbox',
                       'condition': 'expression', 'answerVar': 'reference'},
    'InstantSendMessageToNPC': {'target': 'reference', 'variable': 'reference',
                               'type': 'message_type', 'values': 'expression'},
    'SendMessageToNPC': {'target': 'reference', 'variable': 'reference',
                        'type': 'message_type', 'values': 'expression',
                        'timeType': 'value', 'timeoutType': 'value',
                        'timeout': 'string_expression', 'answer': 'reference'},
}
TYPE_ADAPTERS = {name: name for name in ('_bool', '_int', '_int64', '_float', '_string', '_wuid')}
TYPE_ADAPTERS['common:wuid'] = '_wuid'


def native_catalog(game):
    """Read evidence from runtime Roots only, excluding editor duplicates."""
    nodes, types, enum_types = {}, set(), set()
    with zipfile.ZipFile(Path(game) / 'Data/Scripts.pak') as archive:
        for entry in archive.namelist():
            if not key(entry).startswith('ai/') or not key(entry).endswith('.xml'):
                continue
            data = read(archive, entry)
            try:
                document = ET.fromstring(data)
            except ET.ParseError:
                continue
            if document.tag != 'BehaviorTrees':
                continue
            for tree in document.findall('BehaviorTree'):
                types.update(v.get('type') for v in tree.findall('./Variables/Variable'))
                root = tree.find('Root')
                if root is None:
                    continue
                for node in root.iter():
                    record = nodes.setdefault(node.tag, dict(attributes=set(), children=set(),
                        example=entry, sha256=hashlib.sha256(data).hexdigest()))
                    record['attributes'].update(node.attrib)
                    record['children'].update(c.tag for c in node)
                    for value in node.attrib.values():
                        enum_types.update(re.findall(r'\$enum:([A-Za-z_]\w*)\.', value))
    return dict(nodes={n: dict(r, attributes=sorted(r['attributes']), children=sorted(r['children']))
                       for n, r in nodes.items()}, variable_types=sorted(types), enum_types=sorted(enum_types))


def quote(value):
    # Native string-expression literals use apostrophes. Avoid inventing an
    # escape convention for a value not yet observed in this format.
    if "'" in value or '\\' in value or '\n' in value or '\r' in value:
        raise UnsupportedOperation('Native string escaping needs an adapter')
    return "'" + value + "'"


def reference(value):
    value = literal(value)
    if not value or value.startswith('$'):
        return value
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\['[^']+'\])*(?:\[\d+\])?", value):
        raise UnsupportedOperation('Unsupported source variable reference: ' + value)
    return '$' + value


def expression(value, catalog):
    value = literal(value)
    code = re.sub(r'''('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")''', '', value)
    missing = set(re.findall(r'\$enum:([A-Za-z_]\w*)\.', code)) - set(catalog['enum_types'])
    if missing:
        raise UnsupportedOperation('Unconverted expression enum types: ' + ', '.join(sorted(missing)))
    # Old bare string indexes need source type resolution. They must not become
    # a different variable lookup in the new expression parser.
    if re.search(r'\[[A-Za-z_]\w*\]', code):
        raise UnsupportedOperation('Bare source array index needs type resolution')
    return value


class BehaviorCompiler:
    def __init__(self, models, namespace, catalog, registered=None, importer=None):
        self.namespace = identifier(namespace)
        self.catalog = catalog
        self.registered = registered or {}
        self.importer = importer
        self.bridge = QuestBridge(models, namespace)
        from campaign_profile_bridge import ProfileBridge
        self.profile_bridge = ProfileBridge(self.namespace, self.registered.get('profiles', {}))
        self.models = {m['quest']: m for m in models}
        self.records = {}
        self.converted = Counter()

    def destination(self, owner, document):
        document = key(document)
        if not document.startswith('libs/ai/'):
            raise ValueError('Behavior source is outside the source AI namespace')
        # Helpers are specialized by owner so an empty quest reference retains
        # its caller's quest scope. Original native AI files are never replaced.
        return self.namespace.lower() + '/' + owner.lower() + '/' + document.removeprefix('libs/ai/')

    def include_owner(self, owner, document):
        # A named quest program owns its own state even when called by another
        # quest. Shared helpers still inherit the caller's quest context.
        matches = [name for name in self.models
                   if document == 'libs/ai/quests/' + name.lower() + '.xml']
        return matches[0] if matches else owner

    def variable(self, source):
        if not isinstance(source.get('type'), str) or not source['type'].strip():
            raise UnsupportedOperation('Source variable has no explicit type: ' + str(source.get('name')))
        form = source.get('form', 'single')
        if form not in ('single', 'array', 'associative', 'custom_associative'):
            raise UnsupportedOperation('Unconverted variable form: ' + str(form))
        # Both retail formats serialize custom maps as keyType,valueType.
        # Importing that whole string as one type loses both registrations.
        parts = [part.strip() for part in source['type'].split(',')]
        if len(parts) != (2 if form == 'custom_associative' else 1) or not all(parts):
            raise UnsupportedOperation('Variable form/type arity mismatch: ' + source['name'])
        converted = []
        for part in parts:
            typ = TYPE_ADAPTERS.get(part)
            if typ is None and self.importer is not None:
                typ = self.importer.ensure('ai_types', part, dict(kind='behavior_variable', variable=source))
                if typ in ('bool','int','int64','float','string','wuid'):
                    typ = '_' + typ
            if typ is None:
                raise UnsupportedOperation('Unconverted variable type: ' + part)
            converted.append(typ)
        allowed = {'name', 'type', 'values', 'isPersistent', 'form'}
        if set(source) - allowed:
            raise UnsupportedOperation('Unconverted variable metadata: ' + ', '.join(sorted(set(source) - allowed)))
        target = dict(source, type=','.join(converted))
        if source.get('values') and any(part.startswith('enum:') for part in parts):
            target['values'] = self.typed_expression(source['values'])
        # Variable values have their own typed serialization in both games.
        # They are not operation-attribute string expressions.
        return ET.Element('Variable', target)

    def typed_expression(self, value):
        value = literal(value)
        def segment(text):
            def replace(match):
                original, member = match[1], match[2]
                if self.importer is None:
                    return match[0]
                target = self.importer.ensure('ai_enums', original, dict(kind='behavior_expression', expression=value))
                if target is None:
                    raise UnsupportedOperation('Referenced enum conversion did not complete: ' + original)
                declaration = self.importer.jobs.get(('ai_enums', original.lower()), {}).get('source', {})
                if declaration and member not in {v['name'] for v in declaration.get('values', [])}:
                    raise UnsupportedOperation('Unknown source enum value: ' + original + '.' + member)
                if target not in self.catalog['enum_types']:
                    self.catalog['enum_types'].append(target)
                return '$enum:' + target + '.' + member
            return re.sub(r'\$enum:([A-Za-z_]\w*)\.([A-Za-z_]\w*)', replace, text)
        # Do not rewrite literal text inside expression strings.
        pieces = re.split(r'''('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")''', value)
        return expression(''.join(part if index % 2 else segment(part) for index,part in enumerate(pieces)), self.catalog)

    def lower_node(self, source, owner, document, tree, path, record):
        op, attrs = source['op'], source['attributes']
        site = dict(owner=owner, document=document, tree=tree, path=path, operation=op, arguments=attrs)
        try:
            if op == 'EnableProfile':
                node = self.profile_bridge.lower(source)
            elif op in OPERATIONS:
                converted = self.bridge.lower(source, owner)
                node = ET.Element(converted['op'], converted['attributes'])
            else:
                if op not in CONTRACTS or op not in self.catalog['nodes']:
                    raise UnsupportedOperation('Behavior operation needs an adapter: ' + op)
                contract = CONTRACTS[op]
                if set(attrs) - set(contract):
                    raise UnsupportedOperation('Unconverted attributes on ' + op + ': ' + ', '.join(sorted(set(attrs) - set(contract))))
                if set(attrs) - set(self.catalog['nodes'][op]['attributes']):
                    raise UnsupportedOperation('Attributes absent from native examples for ' + op)
                values = {}
                for name, raw in attrs.items():
                    kind, value = contract[name], literal(raw)
                    if kind == 'reference':
                        value = reference(raw)
                    elif kind == 'expression':
                        value = self.typed_expression(raw)
                    elif kind == 'string_expression':
                        value = self.typed_expression(raw) if value.startswith('$') else (quote(value) if value else '')
                    elif kind == 'profile':
                        target = self.registered.get('profiles', {}).get(value)
                        if not target:
                            raise UnsupportedOperation('Profile dependencies need conversion: ' + value)
                        value = quote(target)
                    elif kind == 'include_file':
                        if value.startswith('$'):
                            raise UnsupportedOperation('Dynamic behavior include needs runtime dispatch')
                        target_doc = key(behavior_path(raw))
                        value = quote(self.destination(self.include_owner(owner, target_doc), target_doc))
                    elif kind in ('mailbox', 'message_type'):
                        if not value or value.startswith('$'):
                            raise UnsupportedOperation('Dynamic/empty source ' + kind + ' needs runtime resolution')
                        group = 'mailboxes' if kind == 'mailbox' else 'ai_types'
                        target = self.importer.ensure(group, value, site) if self.importer else None
                        if target is None:
                            raise UnsupportedOperation('Referenced ' + kind + ' conversion did not complete: ' + value)
                        if kind == 'mailbox':
                            target = self.importer.adapters[group].name(value)
                        value = quote(target)
                    values[name] = value
                if op == 'IncludeTree':
                    target_tree = literal(attrs.get('Name', ''))
                    if not target_tree or target_tree.startswith('$'):
                        raise UnsupportedOperation('Dynamic behavior name needs runtime dispatch')
                    target_doc = key(behavior_path(attrs['File']))
                    record['includes'].append((self.include_owner(owner, target_doc), target_doc, target_tree))
                if op == 'CreateItem':
                    item = literal(attrs.get('ItemGUID', ''))
                    record['item_dependencies'].append(dict(item=item, site=site))
                    if item.startswith('$'):
                        raise UnsupportedOperation('Dynamic item identity requires a runtime item map: ' + item)
                    target = self.importer.ensure('items', item, site) if self.importer else None
                    if target is None:
                        group = self.registered.get('items', {})
                        target = next((group[i] if isinstance(group, dict) else i for i in group if i.lower() == item.lower()), None)
                    if target is None:
                        raise UnsupportedOperation('Referenced item conversion did not complete: ' + item)
                    values['ItemGUID'] = quote(target)
                node = ET.Element(op, values)
            self.converted[op] += 1
        except (UnsupportedOperation, KeyError) as error:
            record['unresolved'].append(dict(site=site, reason=str(error)))
            node = None
        # Traverse every child even when its parent is unsupported. Report all
        # missing adapters, but never emit those children without that parent.
        for index, child in enumerate(source['children']):
            lowered = self.lower_node(child, owner, document, tree, path + '/' + str(index), record)
            if node is not None and lowered is not None:
                node.append(lowered)
        return node

    def compile(self):
        trees = {}
        for owner, model in self.models.items():
            for document, payload in model['behavior_documents'].items():
                # A referenced quest document has its own owner. Compiling it
                # as a helper of the caller would change empty-quest semantics.
                if document.startswith('libs/ai/quests/') and document != 'libs/ai/quests/' + owner.lower() + '.xml':
                    continue
                for name, source in payload['trees'].items():
                    identity = (owner, document, name)
                    record = dict(owner=owner, document=document, tree=name, includes=[],
                                  item_dependencies=[], unresolved=[], native_emitted=False,
                                  provenance=payload.get('provenance', []))
                    self.records[identity] = record
                    tree = ET.Element('BehaviorTree', name=name)
                    variables = ET.SubElement(tree, 'Variables')
                    for variable in source['variables']:
                        try:
                            variables.append(self.variable(variable))
                        except UnsupportedOperation as error:
                            record['unresolved'].append(dict(variable=variable, reason=str(error)))
                    root = self.lower_node(source['root'], owner, document, name, 'Root', record)
                    if root is not None:
                        tree.append(root)
                    trees[identity] = tree
        blocked = {i for i, r in self.records.items() if r['unresolved']}
        # Fixpoint closure also handles mutually recursive includes. A cycle
        # may compile only if every member and every outgoing edge is resolved.
        changed = True
        while changed:
            changed = False
            for identity, record in self.records.items():
                bad = [ref for ref in record['includes'] if ref not in self.records or ref in blocked]
                if bad and identity not in blocked:
                    blocked.add(identity)
                    changed = True
        documents = {}
        for identity, record in self.records.items():
            if identity in blocked:
                record['blocked_includes'] = [r for r in record['includes'] if r not in self.records or r in blocked]
                continue
            path = 'AI/' + self.destination(record['owner'], record['document'])
            documents.setdefault(path, ET.Element('BehaviorTrees')).append(trees[identity])
            record['native_emitted'] = True
        files = {name: xml(root) for name, root in documents.items()}
        bridge_files, wiring = self.bridge.emit()
        bridge_files = self.profile_bridge.attach(bridge_files)
        return files, bridge_files, dict(schema=1, namespace=self.namespace, trees=list(self.records.values()),
            converted_operations=dict(self.converted), whole_trees_emitted=sum(r['native_emitted'] for r in self.records.values()),
            blocked_trees=len(blocked), wiring=wiring, executable=False, runtime_validated=False,
            requires=['Native brain/mailbox registration', 'Source message and link type conversion',
                      'World profiles, actor, item, dialogue and audio registration', 'Retail New Game execution and save/load'])


def convert_behaviors(models, namespace, catalog, registered=None, importer=None):
    return BehaviorCompiler(models, namespace, catalog, registered, importer).compile()
