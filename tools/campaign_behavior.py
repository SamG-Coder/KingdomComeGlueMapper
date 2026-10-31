"""Translate complete source behavior trees, with explicit native contracts.

This stage never turns a list of actions into a startup script. Each node keeps
its parent, child order, variables and include scope. A tree with an unsupported
node or missing dependency is withheld as a whole, including its callers.
Native examples establish format compatibility, not a retail execution result.
"""
from collections import Counter
import copy
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_quest_bridge import OPERATIONS, QuestBridge, UnsupportedOperation, controller_scopes, scoped_operation
from campaign_profiles import ProfileBridge
from campaign_quest_graph import identifier, xml
from campaign_sources import key
from campaign_rpg_script import upgrade as upgrade_rpg_script
from campaign_time import TimeBridge
from campaign_behavior_contracts import WORK_CONTRACTS
from campaign_link_types import data_expression, link_list, assignment_expression
from quest_import import behavior_path, literal
from upgrade_map import read
from campaign_speech_messages import MESSAGE as MONOLOGUE_MESSAGE, validate_request, native_consumer
from campaign_behavior_requests import dependencies as behavior_requests, resolve as resolve_behavior_request
from campaign_schedule_requests import dependencies as schedule_requests, MESSAGE as SCHEDULE_MESSAGE
from campaign_combat_automation import OPERATIONS as COMBAT_AUTOMATIONS


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
    'CreateItem': {'ItemGUID': 'string_expression', 'Amount': 'expression',
                   'CreatedItem': 'reference', 'Target': 'reference', 'NotifyUI': 'value'},
    'EquipItem': {'item': 'reference', 'Target': 'reference'},
    'LinkDataExpression': {'From': 'reference', 'To': 'reference', 'Expresssion': 'expression'},
    'AddBuff': {'BuffGUID': 'string_expression', 'SoulWUID': 'reference'},
    'RemoveBuffs': {'BuffGUID': 'string_expression', 'SoulWUID': 'reference', 'BuffTagId': 'value'},
    'SetExternalLock': {'LockManagerType': 'value', 'Locked': 'value', 'LockName': 'string_expression'},
    # The native horse-wait, animal-state and cart trees retain these ABIs.
    # Keep gates and loops structural; never extract their children as startup
    # actions or evaluate their source indexes during conversion.
    'ExternalLock': {'atomic': 'value', 'OutsideQueuePosVariable': 'reference',
                     'InsidePosVariable': 'reference', 'TimerType': 'value',
                     'OutsideTimerValue': 'string_expression', 'LockManagerType': 'value',
                     'SemaphoreName': 'string_expression', 'RunLogic': 'value'},
    'For': {'startIndex': 'expression', 'endIndex': 'expression', 'step': 'expression',
            'array': 'reference', 'iterator': 'reference', 'value': 'reference', 'break': 'expression'},
    'GetTime': {'TimeType': 'value', 'OutVar': 'reference', 'Delta': 'expression'},
    'GetSpatialInfo': {'In': 'reference', 'Out': 'reference', 'What': 'value'},
    'SetSpatialInfo': {'In': 'reference', 'Val': 'reference', 'What': 'value'},
    'SetVisibility': {'ItemWUID': 'reference', 'Visibility': 'value'},
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
    # Retail AI/_jakub/jumpfromcliff.xml and AI/crime/resolveCrimeDialogue.xml
    # retain this traversal/filter ABI. These resolve authored world links;
    # replacing the lookup with a named actor would change quest semantics.
    'GraphSearch': {'Origin': 'reference', 'Borders': 'reference',
                    'depth': 'expression', 'selection': 'expression',
                    'SearchPattern': 'value', 'EdgePruning': 'link_list',
                    'AllowedEdges': 'link_list', 'SubGraph': 'reference',
                    'AllowSubtraph': 'value', 'includeOrigin': 'value',
                    'excludeOrigin': 'value', 'failOnEmpty': 'value',
                    'SetOperationChoice': 'value', 'errorOnEmpty': 'value',
                    'shortCircuit': 'value', 'skipTraversed': 'value',
                    'id': 'reference'},
    'LinkTagFilter': {'tag': 'string_expression', 'prune': 'value',
                      'negprune': 'value', 'Parent': 'reference',
                      'Child': 'reference', 'Data': 'reference'},
    'FaderBarrier': {'Fader': 'string_expression', 'MuteSounds': 'value'},
    'GameModeCondition': {'GameMode': 'value', 'FailSubtMissing': 'value', 'saveVersion': 'value'},
}
TYPE_ADAPTERS = {name: name for name in ('_bool', '_int', '_int64', '_float', '_string', '_wuid')}
TYPE_ADAPTERS['common:wuid'] = '_wuid'
# Message readers write engine-owned sender records. KCD1's serialized
# common:senderInfo has id/name and nested senderInfoBehavior.behavior;
# KCD2 exposes those records without the common namespace (retail
# AI/battle/battleLadderController.xml and
# AI/quests/lukostreleckaSoutez/shootingRange.xml). Never emit a script
# structure in their place: the native message reader must own the value.
from campaign_ai_types import ENGINE_TYPE_ADAPTERS
CONTRACTS.update(WORK_CONTRACTS)

# Native AI/_lukas/test_lukas_helper.xml supplies both movement ABIs. Destination
# fields are variable references, not quoted strings or guessed coordinates.
_MOVEMENT = dict(destinationSpecification='reference', destinationSpecification2='reference',
    destinationSpecification3='reference', speed='value', additionalParams='reference',
    pathFindingParams='reference', staminaPolicy='reference', pathInfo='reference',
    AnimationToPlay='value', AnimationContext='value')
CONTRACTS['Move'] = dict(_MOVEMENT, stopWithinDistance='value', stopDistanceVariation='value',
                         rayCasteFlee='value', successDistance='value')
CONTRACTS['ExactMove'] = dict(_MOVEMENT, directionType='value', directionSpecification='reference',
                              animationTriggerDist='value', precise='value')
CONTRACTS['ExistPath'] = {k: v for k, v in CONTRACTS['GraphSearch'].items() if k != 'id'}


def native_catalog(game):
    """Read evidence from runtime Roots only, excluding editor duplicates."""
    nodes, types, enum_types = {}, set(), set()
    entity_classes = set()
    item_properties = {'GetItemProperty': set(), 'SetItemProperty': set()}
    lua_contracts = {}
    combat_automation = {}
    policy_values = {'area_labels': set(), 'behavior_states': set(),
                     'perceivable_states': set(), 'item_filter_parameters': set()}
    speech_consumer = False
    path_parameter_fields = set()
    numerical_operations = set()
    behavior_map = False
    script_property_reader = False
    misc_property_reader = False
    with zipfile.ZipFile(Path(game) / 'Data/Scripts.pak') as archive:
        try:
            utility = read(archive, 'Scripts/Utils/EntityUtils.lua').decode('utf-8')
            script_property_reader = all(part in utility for part in (
                'function EntityUtils.GetScriptProperty(entity, propertyName)',
                'properties[propertyName] or misc[propertyName]', 'tonumber(result) or result'))
            misc_property_reader = all(part in utility for part in (
                'function EntityUtils.GetMiscProperty(entityId, propertyName)',
                'EntityModule.GetEntityScriptMisc(entityId)', 'tonumber(property) or property'))
        except KeyError:
            pass
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
            if key(entry) == 'ai/speech/sb_dialog.xml':
                speech_consumer = native_consumer(document)
            for tree in document.findall('BehaviorTree'):
                from campaign_path_parameters import native_fields
                path_parameter_fields.update(native_fields(tree))
                types.update(v.get('type') for v in tree.findall('./Variables/Variable'))
                root = tree.find('Root')
                if root is None:
                    continue
                for node in root.iter():
                    from campaign_combat_automation import observe as observe_combat_automation
                    observe_combat_automation(node, combat_automation)
                    if any(re.search(r"\$__behaviors\['[A-Za-z_]\w*'\]\.count", value)
                           for value in node.attrib.values()):
                        behavior_map = True
                    if node.tag == 'NumericalOperation' and node.get('Operation'):
                        numerical_operations.add(node.get('Operation'))
                    if node.tag == 'HasPerceivableState' and node.get('PerceivableState'):
                        policy_values['perceivable_states'].add(node.get('PerceivableState'))
                    if node.tag == 'ItemParamFilter' and node.get('Param'):
                        policy_values['item_filter_parameters'].add(node.get('Param'))
                    if node.tag in ('AddAreaLabel', 'RemoveAreaLabel') and node.get('Label'):
                        policy_values['area_labels'].add(node.get('Label'))
                    if node.tag == 'SetBehaviorState' and node.get('state'):
                        policy_values['behavior_states'].add(node.get('state'))
                    for field in {'ExecuteLua': ('code',), 'LuaGate': ('code',),
                                  'LuaWrapper': ('onEnter', 'onExit')}.get(node.tag, ()):
                        if field in node.attrib:
                            lua_contracts.setdefault(node.tag, {}).setdefault(field, set()).add(
                                node.get(field).replace('\r\n', '\n').strip())
                    if node.tag in item_properties and node.get('property'):
                        item_properties[node.tag].add(node.get('property'))
                    if node.tag == 'EntityClassFilter' and node.get('Class'):
                        entity_classes.add(node.get('Class'))
                    record = nodes.setdefault(node.tag, dict(attributes=set(), children=set(),
                        example=entry, sha256=hashlib.sha256(data).hexdigest()))
                    record['attributes'].update(node.attrib)
                    record['children'].update(c.tag for c in node)
                    for value in node.attrib.values():
                        enum_types.update(re.findall(r'\$enum:([A-Za-z_]\w*)\.', value))
    # Example coverage is incomplete. Additional contracts must carry native
    # validation evidence bound to the tested engine, not a permissive fallback.
    from campaign_native_contracts import supplement
    supplement(game, nodes)
    from campaign_work_animations import fragment_catalog
    with zipfile.ZipFile(Path(game) / 'Data/Tables.pak') as archive:
        metaroles = {row.get('metarole_name') for row in ET.fromstring(
            read(archive, 'Libs/Tables/rpg/metarole.xml')).iter('metarole')}
        melee_categories = ['melee_weapon.'+row.get('name')+'.*' for row in ET.fromstring(
            read(archive, 'Libs/Tables/item/weapon_class.xml')).iter('MeleeWeaponClass')
            if row.get('name') and row.get('name') != 'undefined']
    return dict(nodes={n: dict(r, attributes=sorted(r['attributes']), children=sorted(r['children']))
                       for n, r in nodes.items()}, variable_types=sorted(types), enum_types=sorted(enum_types),
                native_monologue_consumer=speech_consumer, work_animations=fragment_catalog(game),
                melee_categories=melee_categories,
                combat_automation={op: {field: sorted(values) for field, values in fields.items()}
                                   for op, fields in combat_automation.items()},
                entity_classes=sorted(entity_classes),
                lua_contracts={op:{field:sorted(values) for field,values in fields.items()}
                               for op,fields in lua_contracts.items()},
                item_properties={k:sorted(v) for k,v in item_properties.items()},
                policy_values={k:sorted(v) for k,v in policy_values.items()},
                native_script_property_reader=script_property_reader,
                native_misc_property_reader=misc_property_reader,
                metaroles=sorted(metaroles),
                numerical_operations=sorted(numerical_operations),
                native_behavior_map=behavior_map,
                path_parameter_fields=sorted(path_parameter_fields))


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
    # Runtime-selected array/map elements occur in both engines, including
    # participant maps keyed by a message sender's id. Retain the lookup;
    # it cannot be resolved to a fixed actor during import.
    # Native shootingRange uses $scoreTable[$participants[$participantsIndex]].
    # Parse balanced indexes rather than bounding nesting with a flat regex.
    def variable(pos, depth=0):
        if depth > 64:
            return -1
        match = re.match(r'\$?[A-Za-z_]\w*', value[pos:])
        if not match:
            return -1
        pos += match.end()
        while pos < len(value):
            if value[pos] == '.':
                match = re.match(r'\.[A-Za-z_]\w*', value[pos:])
                if not match:
                    return -1
                pos += match.end()
            elif value[pos] == '[':
                pos += 1
                match = re.match(r"'[^']+'|\d+", value[pos:])
                if match:
                    pos += match.end()
                elif pos < len(value) and value[pos] == '$':
                    pos = variable(pos, depth + 1)
                else:
                    return -1
                if pos < 0 or pos >= len(value) or value[pos] != ']':
                    return -1
                pos += 1
            else:
                break
        return pos
    if variable(0) != len(value):
        raise UnsupportedOperation('Unsupported source variable reference: ' + value)
    return '$' + value


def associative_indexes(value, variables, native_behavior_map=False):
    """Quote source literal keys only for declared string-keyed maps.

    Source arrays, custom-key maps and keys that name variables need a separate
    typed adapter. Never turn a possible runtime index into a constant.
    """
    def replace(match):
        base, index = match[1], match[2]
        declaration = variables.get(base.lstrip('$'), {})
        if base.lstrip('$') == '__behaviors' and not declaration and native_behavior_map:
            # The engine owns this string-keyed map. Its entries are source
            # behavior names, not actor-specific special cases. A source
            # declaration still takes precedence over the implicit ABI.
            declaration = {'form': 'associative'}
        if declaration.get('form') != 'associative' or index in variables:
            raise UnsupportedOperation('Source index requires type resolution: ' + match[0])
        return base + "['" + index + "']"
    parts = re.split(r'''('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")''', value)
    return ''.join(part if i % 2 else re.sub(r'(\$?[A-Za-z_]\w*)\[([A-Za-z_]\w*)\]', replace, part)
                   for i, part in enumerate(parts))


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
    def __init__(self, models, namespace, catalog, registered=None, importer=None, profile_streaming=None, schedule_adapter=None, faction_adapter=None):
        self.namespace = identifier(namespace)
        self.catalog = catalog
        from campaign_lua_contracts import NativeLuaContracts
        self.lua_contracts = NativeLuaContracts(catalog)
        self.registered = registered or {}
        self.importer = importer
        self.bridge = QuestBridge(models, namespace)
        self.profile_bridge = ProfileBridge(namespace, importer)
        self.time_bridge = TimeBridge(namespace)
        self.profile_streaming = profile_streaming
        self.schedule_adapter = schedule_adapter
        self.faction_adapter = faction_adapter
        if faction_adapter is not None and faction_adapter.namespace != self.namespace:
            raise ValueError('Faction bridge must share the compiler namespace')
        self.models = {m['quest']: m for m in models}
        self.records = {}
        self.converted = Counter()
        self.variables = {}
        self.object_scopes = {}
        for owner, model in self.models.items():
            for (document, tree), scope in controller_scopes(model).items():
                self.object_scopes[(owner, document, tree)] = scope

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
            # Both engines' Move consumes this engine-owned parameter record.
            # KCD1 TypeDefinitions_SaveGame declares exactly reversePath and
            # approachFirstNode; native AI/move/moveUtils.xml uses the same
            # type and fields. A namespaced script copy is not the native ABI.
            if part == 'predefinedPathParams' and part in self.catalog['variable_types']:
                typ = part
            if part == 'pathFindingParams' and part in self.catalog['variable_types']:
                typ = part
            native_type = ENGINE_TYPE_ADAPTERS.get(part)
            if native_type in self.catalog['variable_types']:
                typ = native_type
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
        if parts == ['predefinedPathParams'] and not source.get('values'):
            # Preserve the authored KCD1 defaults explicitly, independent of
            # the target engine's default constructor.
            target['values'] = 'reversePath(false),approachFirstNode(true)'
        if 'pathFindingParams' in parts:
            if parts != ['pathFindingParams'] or form != 'single':
                raise UnsupportedOperation('Route-policy collections require per-element initialization')
            from campaign_path_parameters import convert
            try:
                target['values'] = convert(source.get('values', ''), self.catalog.get('path_parameter_fields', []))
            except ValueError as error:
                raise UnsupportedOperation(str(error)) from error
        if source.get('values') and any(part.startswith('enum:') for part in parts):
            target['values'] = self.typed_expression(source['values'])
        # Variable values have their own typed serialization in both games.
        # They are not operation-attribute string expressions.
        return ET.Element('Variable', target)

    def typed_expression(self, value):
        value = associative_indexes(literal(value), self.variables, self.catalog.get('native_behavior_map', False))
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

    def lower_node(self, source, owner, document, tree, path, record, animation_controllers=None):
        op, attrs = source['op'], source['attributes']
        site = dict(owner=owner, document=document, tree=tree, path=path, operation=op, arguments=attrs)
        # Legacy serialized node identities are unquoted decimal numbers;
        # operation arguments (including GraphSearch's output `id`) are quoted
        # source values. Native trees acquire their own serialization identity.
        # Keeping a source save-node number as an operation parameter prevents
        # otherwise ordinary Sequence/ReadMessage trees from being emitted.
        # Preserve it for diagnostics, never mutate the lossless source IR.
        if re.fullmatch(r'[0-9]+', attrs.get('id', '')):
            record.setdefault('source_serialization_ids', []).append(
                dict(path=path, operation=op, id=attrs['id']))
            attrs = {k:v for k,v in attrs.items() if k != 'id'}
            source = dict(source, attributes=attrs)
        if op == 'GetItemProperty' and 'targetVar' in attrs and 'result' not in attrs:
            attrs = dict(attrs)
            attrs['result'] = attrs.pop('targetVar')
            source = dict(source, attributes=attrs)
        try:
            from campaign_temporary_factions import OPTION as FACTION_OPTION, OPERATIONS as FACTION_OPERATIONS
            children_lowered = False
            if self.faction_adapter is not None and op in FACTION_OPERATIONS:
                node = self.faction_adapter.lower(source, site)
            elif (self.faction_adapter is not None and op == 'IfCondition'
                  and FACTION_OPTION in literal(attrs.get('condition', ''))):
                node = self.faction_adapter.combat_guard(source, site)
                if node is None:
                    raise UnsupportedOperation('Unconverted temporary faction context read')
                children_lowered = True
            elif (self.faction_adapter is not None and op == 'ExecuteLua'
                  and FACTION_OPTION in literal(attrs.get('code', ''))):
                if set(attrs) != {'code'} or source['children']:
                    raise UnsupportedOperation('Unsupported faction context statement metadata')
                node = self.faction_adapter.lower_context(literal(attrs['code']), site)
            elif any(FACTION_OPTION in literal(v) for v in attrs.values()):
                raise UnsupportedOperation('Unconverted temporary faction context use')
            elif (self.schedule_adapter is not None and op in ('InstantSendMessageToNPC', 'SendMessageToNPC')
                    and literal(attrs.get('type', '')) == SCHEDULE_MESSAGE):
                node = self.schedule_adapter.lower(source, owner, document, tree, path,
                    (owner, record.get('source_document', document), tree) in self.object_scopes,
                    reference(associative_indexes(literal(attrs['target']), self.variables,
                                                 self.catalog.get('native_behavior_map', False))))
            elif op == 'ExecuteLua':
                if set(attrs) != {'code'} or op not in self.catalog['nodes']:
                    raise UnsupportedOperation('Native ExecuteLua contract is missing or has unsupported fields')
                script = literal(attrs['code'])
                if 'forwardTime' in script or 'Utils.SetWorldTimePaused' in script:
                    node = self.time_bridge.lower(script, site)
                else:
                    if self.lua_contracts.accepts(op, 'code', script):
                        node = ET.Element('ExecuteLua', code=script)
                    else:
                        from campaign_script_properties import upgrade_reads
                        upgraded = upgrade_reads(script, self.catalog.get('native_script_property_reader', False),
                                                  self.catalog.get('native_misc_property_reader', False))
                        if upgraded is None:
                            from campaign_work_scripts import upgrade as upgrade_work_script
                            upgraded = upgrade_work_script(script, self.catalog)
                        node = ET.Element('ExecuteLua', code=upgraded if upgraded is not None else upgrade_rpg_script(script))
            elif op == 'PlayAnimation':
                from campaign_work_animations import lower as lower_work_animation
                node = lower_work_animation(source, self.catalog, animation_controllers)
            elif op in COMBAT_AUTOMATIONS:
                from campaign_combat_automation import lower as lower_combat_automation
                node = lower_combat_automation(source, self.catalog)
            elif op == 'EnableProfile':
                if self.profile_streaming is None:
                    raise UnsupportedOperation('Installed native profile streaming helper is required')
                node = self.profile_bridge.lower(source, site)
            elif op in ('SmartAreaBehaviorTag', 'SmartObjectBehaviorTag'):
                from campaign_work_requests import lower as lower_work_request
                node = lower_work_request(source, self.catalog)
            elif op in OPERATIONS:
                scoped = scoped_operation(source, self.object_scopes.get((owner, record.get('source_document', document), tree)))
                converted = self.bridge.lower(scoped, owner)
                if converted['op'] == 'GameContextBarrier' and converted['op'] not in self.catalog['nodes']:
                    raise UnsupportedOperation('Native continuous quest-state barrier is unavailable')
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
                    if kind == 'human_gender':
                        if value not in ('Male', 'Female'):
                            raise UnsupportedOperation('Unverified native human gender: ' + value)
                        values[name] = value
                        continue
                    if kind in ('area_label', 'behavior_state', 'perceivable_state', 'item_filter_parameter'):
                        output = quote(value) if kind in ('area_label', 'perceivable_state') else value
                        group = {'area_label': 'area_labels', 'behavior_state': 'behavior_states',
                                 'perceivable_state': 'perceivable_states',
                                 'item_filter_parameter': 'item_filter_parameters'}[kind]
                        if output not in self.catalog.get('policy_values', {}).get(group, []):
                            raise UnsupportedOperation('Unverified native ' + kind + ': ' + value)
                        values[name] = output
                        continue
                    if kind == 'native_lua':
                        if value.strip() and not self.lua_contracts.accepts(op, name, value):
                            from campaign_work_scripts import upgrade as upgrade_work_script
                            converted_script = upgrade_work_script(value, self.catalog)
                            if converted_script is None:
                                raise UnsupportedOperation('Source Lua requires a semantic adapter')
                            value = converted_script
                        values[name] = value
                        continue
                    if kind == 'item_property':
                        if value not in self.catalog.get('item_properties', {}).get(op, []):
                            raise UnsupportedOperation('Unverified native item property: ' + value)
                        values[name] = value
                        continue
                    if kind == 'entity_class':
                        # These are AI graph classes, not CryEntity class
                        # names. Native Slot/SmartObject filters still use
                        # those names although placements use ItemSlot and
                        # SmartObjectHolder. Never rename by string similarity.
                        if value not in self.catalog.get('entity_classes', []):
                            raise UnsupportedOperation('Unverified native graph entity class: ' + value)
                    elif kind == 'reference':
                        value = reference(associative_indexes(literal(raw), self.variables,
                                          self.catalog.get('native_behavior_map', False)))
                    elif kind == 'expression':
                        value = self.typed_expression(raw)
                    elif kind == 'string_expression':
                        value = self.typed_expression(raw) if value.startswith('$') else (quote(value) if value else '')
                    elif kind == 'literal_pattern':
                        # Concatenation parses variable markers inside a string
                        # pattern; a leading $ does not make it an expression.
                        value = quote(value) if value else ''
                    elif kind == 'include_file':
                        if value.startswith('$'):
                            raise UnsupportedOperation('Dynamic behavior include needs runtime dispatch')
                        target_doc = key(behavior_path(raw))
                        value = quote(self.destination(self.include_owner(owner, target_doc), target_doc))
                    elif kind == 'link_list':
                        # Without script edges, legacy unit/API callers still
                        # retain the shared engine edge vocabulary.
                        if value and not self.importer and any(t.strip().lower() not in ('parent', 'inventory') for t in value.split(',')):
                            raise UnsupportedOperation('Graph edge mask requires a link-type importer')
                        try:
                            value = link_list(value, self.importer, site)
                        except ValueError as error:
                            raise UnsupportedOperation(str(error)) from error
                        value = quote(value) if value else ''
                    elif kind == 'empty_script':
                        if value:
                            raise UnsupportedOperation('SetVariable script requires a semantic adapter')
                    elif kind == 'empty_category':
                        if value:
                            if (op != 'ItemClassFilter' or literal(attrs.get('ClassId',''))
                                    or value not in self.catalog.get('melee_categories', [])):
                                raise UnsupportedOperation('Source item category filter requires category conversion')
                            value = quote(value)
                    elif kind == 'item_class':
                        if (not value and op == 'ItemClassFilter' and
                                literal(attrs.get('Category','')) in self.catalog.get('melee_categories', [])):
                            values[name] = ''
                            continue
                        record['item_dependencies'].append(dict(item=value, site=site))
                        if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
                            raise UnsupportedOperation('Dynamic or missing job item class requires a runtime identity map')
                        target = self.importer.ensure('items', value, site) if self.importer else None
                        if target is None:
                            group = self.registered.get('items', {})
                            target = next((group[i] if isinstance(group, dict) else i for i in group
                                           if i.lower() == value.lower()), None)
                        if target is None:
                            raise UnsupportedOperation('Job item class conversion did not complete: ' + value)
                        value = quote(target)
                    elif kind == 'behavior_request_event':
                        # Native BehaviorTagCatch still registers these exact
                        # event names, including the original BehSucces spelling.
                        if value not in ('NotFound', 'MaxInstance', 'NotEnabled', 'GeneralRQFail',
                                         'RequestOK', 'BehSucces', 'BehFail', 'MissingSource'):
                            raise UnsupportedOperation('Unknown behavior request event: ' + value)
                    elif kind == 'numeric_operand':
                        # Native numerical inputs accept a literal or a bare
                        # variable path; unlike OutVariableName these are not
                        # serialized with the expression-reference sigil.
                        if value and not re.fullmatch(r'-?\d+(?:\.\d+)?', value):
                            value = reference(value).removeprefix('$')
                            if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', value):
                                raise UnsupportedOperation('Unsupported numerical operand: ' + value)
                    elif kind == 'numeric_operation':
                        # These operations retain their input/output ABI in
                        # native crime, movement and arrow-hit controllers.
                        # Presence in this installation is checked as well as
                        # the reviewed operation vocabulary.
                        compatible = {'round', 'float2int', 'distance2dSq'}
                        if value != 'randRange' and not (value in compatible and
                                value in self.catalog.get('numerical_operations', ())):
                            raise UnsupportedOperation('Numerical operation requires a validated contract: ' + value)
                    elif kind == 'link_type':
                        if not value or value.startswith('$'):
                            raise UnsupportedOperation('Dynamic link data type requires runtime dispatch')
                        target = self.importer.ensure('link_types', value, site) if self.importer else None
                        if target is None:
                            raise UnsupportedOperation('Referenced AI link type conversion did not complete: ' + value)
                        value = quote(target)
                    elif kind in ('mailbox', 'message_type', 'optional_message_type'):
                        if kind == 'message_type' and value == SCHEDULE_MESSAGE:
                            raise UnsupportedOperation('Source daycycle change requires a native scheduled-activity lifecycle; registering its message type does not install a consumer')
                        if kind == 'message_type' and value == MONOLOGUE_MESSAGE and op in ('SendMessageToNPC', 'InstantSendMessageToNPC'):
                            values[name] = quote(validate_request(literal(attrs.get('values', '')), self.catalog))
                            continue
                        if kind == 'optional_message_type' and not value:
                            values[name] = ''
                            continue
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
                if op == 'LinkDataExpression':
                    try:
                        values['Expresssion'] = assignment_expression(values['Expresssion'], self.importer, site)
                    except ValueError as error:
                        raise UnsupportedOperation(str(error)) from error
                if op == 'LinkDataFilter':
                    source_tag = literal(attrs['tag'])
                    record_type = self.importer.jobs[('link_types', source_tag.lower())]
                    row = record_type['source']['row']
                    symbol = row.get('variableName') or row['name']
                    values['condition'] = data_expression(values.get('condition', ''), symbol, record_type['target'])
                if op == 'LinkTagFilter' and self.importer is not None and 'link_types' in self.importer.adapters:
                    source_tag = literal(attrs.get('tag', ''))
                    if not source_tag or source_tag.startswith('$'):
                        raise UnsupportedOperation('Dynamic source link tag needs a runtime identity map')
                    target = self.importer.ensure('link_types', source_tag, site)
                    if target is None:
                        raise UnsupportedOperation('Referenced AI link type conversion did not complete: ' + source_tag)
                    values['tag'] = quote(target)
                if op == 'IncludeTree':
                    target_tree = literal(attrs.get('Name', ''))
                    if not target_tree or target_tree.startswith('$'):
                        raise UnsupportedOperation('Dynamic behavior name needs runtime dispatch')
                    target_doc = key(behavior_path(attrs['File']))
                    record['includes'].append((self.include_owner(owner, target_doc), target_doc, target_tree))
                if (op == 'CallBehavior' and literal(attrs.get('Target', '')).lstrip('$') == '__object.id'
                        and (owner, record.get('source_document', document), tree) in self.object_scopes):
                    resolved = resolve_behavior_request(self.models[owner], dict(
                        source='$__object.id', tree=literal(attrs.get('BehaviorName', ''))))
                    record['includes'].append((owner, resolved['document'], resolved['tree']))
                if op == 'CreateItem' or (op == 'RemoveItem' and literal(attrs.get('ItemGUID', ''))):
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
                if op in ('AddBuff', 'RemoveBuffs'):
                    buff = literal(attrs.get('BuffGUID', ''))
                    record['buff_dependencies'].append(dict(buff=buff, site=site))
                    # Native explicit-GUID removals serialize the no-tag sentinel -1.
                    # A real tag selection still requires its own identity adapter.
                    if not buff or buff.startswith('$') or literal(attrs.get('BuffTagId', '')) not in ('', '-1'):
                        raise UnsupportedOperation('Dynamic, empty or tag-selected buffs require a runtime buff map')
                    target = self.importer.ensure('buffs', buff, site) if self.importer else None
                    if target is None:
                        raise UnsupportedOperation('Referenced buff conversion did not complete: ' + buff)
                    values['BuffGUID'] = quote(target)
                node = ET.Element(op, values)
            self.converted[op] += 1
            if children_lowered:
                return node
        except (UnsupportedOperation, KeyError) as error:
            record['unresolved'].append(dict(site=site, reason=str(error)))
            node = None
        # Traverse every child even when its parent is unsupported. Report all
        # missing adapters, but never emit those children without that parent.
        for index, child in enumerate(source['children']):
            from campaign_work_animations import child_controllers
            controllers = child_controllers(source, child, animation_controllers) if node is not None else None
            lowered = self.lower_node(child, owner, document, tree, path + '/' + str(index), record, controllers)
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
                    self.variables = {v['name']: v for v in source['variables']}
                    identity = (owner, document, name)
                    record = dict(owner=owner, document=document, tree=name, includes=[],
                                  item_dependencies=[], buff_dependencies=[], unresolved=[], native_emitted=False,
                                  provenance=payload.get('provenance', []))
                    if source.get('animation_controllers'):
                        record['animation_controllers'] = source['animation_controllers']
                        record['source_document'] = source['source_document']
                    # Message-dispatched behaviors are executable dependencies
                    # too. Previously only IncludeTree participated in closure,
                    # allowing a request to target a withheld/missing tree.
                    record['behavior_requests'] = behavior_requests(source['root'])
                    record['schedule_requests'] = schedule_requests(model, source['root'])
                    for request in record['schedule_requests']:
                        if 'callback' in request:
                            callback = request['callback']
                            record['includes'].append((owner, callback['document'], callback['tree']))
                        if 'error' in request:
                            record['unresolved'].append(dict(request=request, reason=request['error']))
                    for request in record['behavior_requests']:
                        if 'error' in request:
                            record['unresolved'].append(dict(request=request, reason=request['error']))
                            continue
                        record['unresolved'].append(dict(request=request, reason=
                            'Custom combat dispatch requires a native behavior-consumer adapter'))
                        if request['source'] not in ('$this.id', '$__object.id', 'this.id', '__object.id'):
                            record['unresolved'].append(dict(request=request, reason=
                                'Dynamic behavior source requires source object resolution'))
                            continue
                        try:
                            resolved = resolve_behavior_request(model, request)
                            record['includes'].append((owner, resolved['document'], resolved['tree']))
                        except UnsupportedOperation as error:
                            record['unresolved'].append(dict(request=request, reason=str(error)))
                            # Keep an unresolved dependency visible for older
                            # IR snapshots without controller registration.
                            record['includes'].append((owner, document, request['tree']))
                    self.records[identity] = record
                    tree = ET.Element('BehaviorTree', name=name)
                    variables = ET.SubElement(tree, 'Variables')
                    for variable in source['variables']:
                        try:
                            variables.append(self.variable(variable))
                        except UnsupportedOperation as error:
                            record['unresolved'].append(dict(variable=variable, reason=str(error)))
                    root = self.lower_node(source['root'], owner, document, name, 'Root', record,
                                           tuple(source['animation_controllers']) if source.get('animation_controllers') else None)
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
        profile_modules = []
        auxiliary_modules = []
        if self.faction_adapter is not None and self.faction_adapter.actions:
            bridge_files.update(self.faction_adapter.emit())
            auxiliary_modules.append(dict(file='Quests/' + self.faction_adapter.stem + '.xml',
                name=self.faction_adapter.stem, instance='source_temporary_factions'))
        if self.time_bridge.actions:
            bridge_files.update(self.time_bridge.emit())
            auxiliary_modules.append(dict(file='Quests/' + self.namespace + '_time_bridge.xml',
                name=self.namespace + '_time_bridge', instance='source_time_bridge'))
        if self.profile_bridge.actions:
            bridge_files.update(self.profile_bridge.emit(self.profile_streaming))
            profile_modules.append(dict(file='Quests/' + self.namespace + '_profile_bridge.xml',
                name=self.namespace + '_profile_bridge', instance='source_profile_bridge'))
        return files, bridge_files, dict(schema=1, namespace=self.namespace, trees=list(self.records.values()),
            converted_operations=dict(self.converted), whole_trees_emitted=sum(r['native_emitted'] for r in self.records.values()),
            blocked_trees=len(blocked), wiring=wiring, profile_modules=profile_modules,
            auxiliary_modules=auxiliary_modules, time_actions=self.time_bridge.actions,
            temporary_factions=self.faction_adapter.report() if self.faction_adapter else None,
            schedule_consumer_required=self.schedule_adapter.stem if self.schedule_adapter else None,
            executable=False, runtime_validated=False,
            requires=['Native brain/mailbox registration', 'Source message and link type conversion',
                      'World profiles, actor, item, dialogue and audio registration', 'Retail New Game execution and save/load'])


def convert_behaviors(models, namespace, catalog, registered=None, importer=None, profile_streaming=None, schedule_adapter=None, faction_adapter=None):
    from campaign_animation_specialization import specialize
    models, specializations = specialize(models)
    files, bridge, report = BehaviorCompiler(models, namespace, catalog, registered, importer, profile_streaming, schedule_adapter, faction_adapter).compile()
    report['animation_specializations'] = specializations
    report['source_trees_emitted'] = sum(r['native_emitted'] for r in report['trees'] if 'source_document' not in r)
    report['specialized_trees_emitted'] = sum(r['native_emitted'] for r in report['trees'] if 'source_document' in r)
    return files, bridge, report
