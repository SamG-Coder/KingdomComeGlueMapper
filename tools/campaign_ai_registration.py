"""Import retail AI inboxes and brains with their complete dependencies.

The source database chooses the controller brain and its entry trees. A brain
is published only when every referenced tree, mailbox and variable type has
been converted. No empty brain or unrelated native NPC brain is substituted.
"""
import copy
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET
import zipfile

from campaign_dependencies import ImportPlan
from campaign_quest_graph import identifier, xml
from campaign_sources import key
from quest_import import behavior_path
from upgrade_map import read


TABLES = ('brain', 'brain2mailbox', 'brain2subbrain', 'brain_variable',
          'mailbox', 'mailbox_filter', 'subbrain', 'subbrain_behaviour_tree',
          'subbrain_smart_object', 'mailbox_action_type', 'subbrain_type',
          'ai_variable_form', 'ai_variable_sync')


def rows_for(tables, table, field, value):
    return [dict(row) for row in tables.get('ai/' + table)['rows']
            if row.get(field, '').lower() == value.lower()]


def require_fields(row, allowed, description):
    unknown = set(row) - set(allowed)
    if unknown:
        raise ValueError('Unconverted ' + description + ' fields: ' + ', '.join(sorted(unknown)))


def bool_value(value):
    if value.lower() in ('true', '1'): return 'true'
    if value.lower() in ('false', '0'): return 'false'
    raise ValueError('Invalid source boolean: ' + value)


class NativeAIRegistry:
    """Read table containers and enumeration identities from the target game."""
    def __init__(self, tables, namespace):
        self.namespace = identifier(namespace).lower()
        self.tables = tables

    @classmethod
    def load(cls, game, namespace):
        tables = {}
        with zipfile.ZipFile(Path(game) / 'Data/Tables.pak') as archive:
            for name in TABLES:
                entry = 'Libs/Tables/ai/' + name + '.xml'
                root = ET.fromstring(read(archive, entry))
                if root.tag != 'database' or len(root) != 1:
                    raise ValueError('Unsupported native AI registration table: ' + name)
                tables[name] = dict(container=root[0].tag, attributes=dict(root[0].attrib),
                                   row_tag=name, rows=[dict(r.attrib) for r in root[0]],
                                   source=dict(archive=str(archive.filename), entry=entry))
        return cls(tables, namespace)

    def guid(self, table, identity):
        return str(uuid.uuid5(uuid.NAMESPACE_URL,
            'kingdomcomegluemapper/' + self.namespace + '/' + table + '/' + identity.lower()))

    def enum(self, source, table, value):
        field, label = table + '_id', table + '_name'
        old = source.row('ai/' + table, field, value)
        matches = [row[field] for row in self.tables[table]['rows'] if row[label] == old[label]]
        if len(matches) != 1:
            raise ValueError('Source AI enumeration has no native equivalent: ' + table + ':' + old[label])
        return matches[0]

    def emit(self, table, identity, rows):
        if not rows:
            return {}
        info = self.tables[table]
        known = set().union(*(set(r) for r in info['rows']))
        root = ET.Element('database', name='barbora')
        container = ET.SubElement(root, info['container'], info['attributes'])
        for row in rows:
            require_fields(row, known, 'native ' + table)
            ET.SubElement(container, info['row_tag'], row)
        path = 'Libs/Tables/ai/' + table + '__' + self.namespace + '_' + self.guid(table, identity) + '.xml'
        return {path: xml(root)}


class MailboxAdapter:
    def __init__(self, tables, native):
        self.tables, self.native = tables, native

    def source_row(self, identity):
        rows = self.tables.get('ai/mailbox')['rows']
        matches = [r for r in rows if identity.lower() in
                   (r['mailbox_id'].lower(), r['mailbox_name'].lower())]
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous retail mailbox: ' + identity)
        return dict(matches[0])

    def name(self, identity):
        row = self.source_row(identity)
        return self.native.namespace + '_' + row['mailbox_name']

    def plan(self, identity):
        row = self.source_row(identity)
        require_fields(row, ('mailbox_id', 'mailbox_name', 'message_limit', 'on_accept'), 'mailbox')
        filters = rows_for(self.tables, 'mailbox_filter', 'mailbox_id', row['mailbox_id'])
        rules = [r for r in self.tables.get('ai/mailbox_rule')['rows']
                 if r['mailbox_filter_id'] in {f['mailbox_filter_id'] for f in filters}]
        for rule in rules:
            require_fields(rule, ('mailbox_rule_id', 'mailbox_filter_id', 'priority', 'condition', 'custom_data_type'), 'mailbox rule')
            # KDC2 accepts the entire matching type. Only the unconditional
            # legacy rule is equivalent; old expression filters need lowering.
            if rule['condition'] != '*' or rule.get('custom_data_type'):
                raise ValueError('Conditional source mailbox rule needs conversion: ' + rule['mailbox_rule_id'])
        if int(row['message_limit']) < -1:
            raise ValueError('Invalid source mailbox message limit')
        accept = self.native.enum(self.tables, 'mailbox_action_type', row['on_accept'])
        for f in filters:
            require_fields(f, ('mailbox_filter_id', 'mailbox_id', 'type'), 'mailbox filter')
        return ImportPlan([('ai_types', f['type']) for f in filters], dict(
            row=row, filters=filters, rules=rules, on_accept=accept,
            provenance={n: self.tables.get('ai/' + n)['provenance']
                        for n in ('mailbox', 'mailbox_filter', 'mailbox_rule')}))

    def convert(self, identity, plan, registered):
        source = plan.source
        row = dict(source['row'])
        target = self.native.guid('mailbox', row['mailbox_id'])
        row.update(mailbox_id=target, mailbox_name=self.name(identity), on_accept=source['on_accept'])
        filters = [dict(f, mailbox_id=target,
                        mailbox_filter_id=self.native.guid('mailbox_filter', f['mailbox_filter_id']),
                        type=registered['ai_types'][f['type'].lower()]) for f in source['filters']]
        files = self.native.emit('mailbox', target, [row])
        files.update(self.native.emit('mailbox_filter', target, filters))
        return target, files, dict(name=row['mailbox_name'], filters=filters,
            message_limit=row['message_limit'], on_accept=row['on_accept'], runtime_validated=False)


def tree_identity(owner, file, name):
    identifier(owner)
    if not name or '|' in name:
        raise ValueError('Invalid source entry tree name')
    # The dependency registry folds identities to lowercase, but authored tree
    # names are case-sensitive (retail contains both beggar_Work/beggar_work).
    return owner + '|' + key(behavior_path(file)) + '|' + name.encode('utf-8').hex()


class ConvertedTreeAdapter:
    """Expose only whole trees whose transitive includes passed conversion."""
    def __init__(self, report, files):
        self.namespace, self.files = report['namespace'], files
        self.records = {}
        for record in report['trees']:
            identity = tree_identity(record['owner'], record['document'].removeprefix('libs/ai/'), record['tree']).lower()
            if identity in self.records:
                raise ValueError('Ambiguous converted behavior identity: ' + identity)
            self.records[identity] = record

    def plan(self, identity):
        record = self.records.get(identity)
        if not record:
            raise ValueError('Source brain entry tree was not discovered: ' + identity)
        if not record['native_emitted']:
            reasons = [r['reason'] for r in record['unresolved'][:3]]
            if record.get('blocked_includes'): reasons.append('unconverted include dependencies')
            raise ValueError('Source brain entry tree is not converted: ' + identity + '; ' + '; '.join(reasons))
        return ImportPlan(source=copy.deepcopy(record))

    def convert(self, identity, plan, registered):
        record = plan.source
        path = (self.namespace.lower() + '/' + record['owner'].lower() + '/' +
                record['document'].removeprefix('libs/ai/'))
        data = self.files['AI/' + path]
        return path + '|' + record['tree'], {'AI/' + path: data}, dict(
            file=path, tree=record['tree'], runtime_validated=False)


class SubbrainAdapter:
    def __init__(self, tables, native):
        self.tables, self.native = tables, native

    def plan(self, identity):
        owner, guid = identity.split('|')
        row = dict(self.tables.row('ai/subbrain', 'subbrain_id', guid))
        require_fields(row, ('always_active', 'subbrain_id', 'subbrain_name', 'subbrain_type', 'timeout'), 'subbrain')
        typ = self.native.enum(self.tables, 'subbrain_type', row['subbrain_type'])
        type_name = self.tables.row('ai/subbrain_type', 'subbrain_type_id', row['subbrain_type'])['subbrain_type_name']
        if type_name == 'SmartObject':
            table, tree_fields = 'subbrain_smart_object', ('on_update_tree', 'on_request_tree', 'on_release_tree')
        elif type_name == 'BehaviorTree':
            table, tree_fields = 'subbrain_behaviour_tree', ('tree_name',)
        else:
            raise ValueError('Subbrain runtime requires conversion: ' + type_name)
        behavior = dict(self.tables.row('ai/' + table, 'subbrain_id', guid))
        require_fields(behavior, ('subbrain_id', 'file_name', 'explicit_initialization', 'system_variables', *tree_fields), 'subbrain behavior')
        if behavior.get('system_variables') or behavior.get('explicit_initialization') not in (None, '', 'False', 'false', '0'):
            raise ValueError('Subbrain system-variable/initialization policy needs conversion')
        references = {field: tree_identity(owner, behavior['file_name'], behavior[field])
                      for field in tree_fields if behavior.get(field)}
        if not references:
            raise ValueError('Source subbrain has no entry trees')
        return ImportPlan([('behavior_trees', r) for r in references.values()], dict(
            row=row, behavior=behavior, table=table, references=references, native_type=typ,
            provenance=self.tables.get('ai/' + table)['provenance']))

    def convert(self, identity, plan, registered):
        source = plan.source
        target = self.native.guid('subbrain', identity)
        row = {k: v for k, v in source['row'].items() if v != ''}
        row.update(subbrain_id=target, subbrain_name=self.native.namespace + '_' + row['subbrain_name'],
                   subbrain_type=source['native_type'])
        if 'always_active' in row: row['always_active'] = bool_value(row['always_active'])
        behavior = dict(subbrain_id=target)
        paths = set()
        for field, ref in source['references'].items():
            path, name = registered['behavior_trees'][ref.lower()].split('|')
            behavior[field] = name
            paths.add(path)
        if len(paths) != 1:
            raise ValueError('Subbrain entry trees were emitted into different documents')
        behavior['file_name'] = paths.pop()
        files = self.native.emit('subbrain', target, [row])
        files.update(self.native.emit(source['table'], target, [behavior]))
        return target, files, dict(entry_points=behavior, runtime_validated=False)


class BrainAdapter:
    def __init__(self, tables, native, types):
        self.tables, self.native, self.types = tables, native, types

    def plan(self, identity):
        owner, guid = identity.split('|')
        identifier(owner)
        row = dict(self.tables.row('ai/brain', 'brain_id', guid))
        require_fields(row, ('brain_id', 'brain_name'), 'brain')
        mailboxes = rows_for(self.tables, 'brain2mailbox', 'brain_id', guid)
        subbrains = rows_for(self.tables, 'brain2subbrain', 'brain_id', guid)
        variables = rows_for(self.tables, 'brain_variable', 'brain_id', guid)
        dependencies = [('mailboxes', m['mailbox_id']) for m in mailboxes]
        dependencies += [('subbrains', owner + '|' + s['subbrain_id']) for s in subbrains]
        for link in mailboxes: require_fields(link, ('brain_id', 'mailbox_id', 'priority'), 'brain mailbox link')
        for link in subbrains: require_fields(link, ('brain_id', 'subbrain_id', 'priority'), 'brain subbrain link')
        for var in variables:
            require_fields(var, ('ai_variable_form_id', 'ai_variable_sync_id', 'brain_id',
                'brain_variable_name', 'init_value', 'is_persistent', 'type'), 'brain variable')
            form = self.tables.row('ai/ai_variable_form', 'ai_variable_form_id', var['ai_variable_form_id'])['ai_variable_form_name']
            parts = [p.strip() for p in var['type'].split(',')]
            if len(parts) != (2 if form == 'custom_associative' else 1) or not all(parts):
                raise ValueError('Brain variable form/type arity mismatch: ' + var['brain_variable_name'])
            dependencies.extend(('ai_types', p) for p in parts)
            var['native_form'] = self.native.enum(self.tables, 'ai_variable_form', var['ai_variable_form_id'])
            var['native_sync'] = self.native.enum(self.tables, 'ai_variable_sync', var['ai_variable_sync_id'])
        return ImportPlan(dependencies, dict(owner=owner, row=row, mailboxes=mailboxes,
                                            subbrains=subbrains, variables=variables))

    def convert(self, identity, plan, registered):
        source = plan.source
        target = self.native.guid('brain', identity)
        row = dict(brain_id=target, brain_name=self.native.namespace + '_' + source['row']['brain_name'])
        files = self.native.emit('brain', target, [row])
        mailboxes = [dict(m, brain_id=target, mailbox_id=registered['mailboxes'][m['mailbox_id'].lower()])
                     for m in source['mailboxes']]
        subbrains = [dict(s, brain_id=target, subbrain_id=registered['subbrains'][(source['owner'] + '|' + s['subbrain_id']).lower()])
                     for s in source['subbrains']]
        variables = []
        for original in source['variables']:
            var = {k: v for k, v in original.items() if v != '' and k not in ('native_form', 'native_sync')}
            var.update(brain_id=target, ai_variable_form_id=original['native_form'], ai_variable_sync_id=original['native_sync'])
            var['type'] = ','.join(registered['ai_types'][p.strip().lower()] for p in original['type'].split(','))
            if 'is_persistent' in var: var['is_persistent'] = bool_value(var['is_persistent'])
            if 'init_value' in var: var['init_value'] = self.types.enum_expression(var['init_value'], registered)
            variables.append(var)
        for table, rows in (('brain2mailbox', mailboxes), ('brain2subbrain', subbrains), ('brain_variable', variables)):
            files.update(self.native.emit(table, target, rows))
        return target, files, dict(mailboxes=len(mailboxes), subbrains=len(subbrains),
            variables=len(variables), all_entry_trees_converted=True, runtime_validated=False)


def controller_definition(tables, quest_row):
    """Discover database callbacks as well as the main brain's entry points."""
    name = quest_row.get('smart_object')
    if not name:
        return None
    smart = dict(tables.row('ai/so_smart_object', 'so_smart_object_name', name))
    entries, unresolved = [], []
    for link in rows_for(tables, 'brain2subbrain', 'brain_id', smart['brain_id']):
        sub = tables.row('ai/subbrain', 'subbrain_id', link['subbrain_id'])
        typ = tables.row('ai/subbrain_type', 'subbrain_type_id', sub['subbrain_type'])['subbrain_type_name']
        if typ == 'SmartObject':
            table, fields = 'subbrain_smart_object', ('on_update_tree', 'on_request_tree', 'on_release_tree')
        elif typ == 'BehaviorTree':
            table, fields = 'subbrain_behaviour_tree', ('tree_name',)
        else:
            unresolved.append(dict(subbrain=sub, reason='Entry discovery needs ' + typ + ' adapter'))
            continue
        row = tables.row('ai/' + table, 'subbrain_id', sub['subbrain_id'])
        for field in fields:
            if row.get(field): entries.append(dict(file=row['file_name'], tree=row[field], kind=field, table=table))
    tags = []
    for link in rows_for(tables, 'so_smart_object2so_behaviour_tag', 'so_smart_object_id', smart['so_smart_object_id']):
        row = dict(tables.row('ai/so_behaviour_tag', 'so_behaviour_tag_id', link['so_behaviour_tag_id']))
        tags.append(dict(row=row, link=link))
        entries.append(dict(file=row['tree_file'], tree=row['tree_name'], kind='behavior_callback',
                            name=row['so_behaviour_tag_name'], table='so_behaviour_tag'))
    return dict(smart_object=smart, entry_points=entries, behaviors=tags, unresolved=unresolved)


class SmartEntityAdapter:
    """Register source-requested callbacks in native SmartEntity templates."""
    def __init__(self, tables, native):
        self.tables, self.native = tables, native

    def plan(self, identity):
        owner, guid = identity.split('|')
        row = dict(self.tables.row('ai/so_smart_object', 'so_smart_object_id', guid))
        require_fields(row, ('so_smart_object_id', 'so_smart_object_name', 'body_id', 'brain_id', 'priority'), 'smart object')
        definition = controller_definition(self.tables, dict(smart_object=row['so_smart_object_name']))
        body = self.tables.row('ai/ai_body', 'ai_body_id', row['body_id'])
        require_fields(body, ('ai_body_id', 'ai_body_name'), 'AI body')
        for table in ('ai_body2brain_sensor', 'ai_body2npc_reference_point'):
            if rows_for(self.tables, table, 'ai_body_id', row['body_id']):
                raise ValueError('Source smart-object body has sensors/reference points requiring conversion')
        deps = [('brains', owner + '|' + row['brain_id'])]
        behaviors = []
        for item in definition['behaviors']:
            tag, link = dict(item['row']), item['link']
            require_fields(link, ('so_smart_object_id', 'so_behaviour_tag_id', 'priority'), 'smart-object behavior link')
            require_fields(tag, ('so_behaviour_tag_id', 'so_behaviour_tag_name', 'initial_state',
                'max_instances', 'on_fail_action', 'on_success_action', 'condition_id', 'tree_file', 'tree_name'), 'smart-object behavior')
            if link['priority'] != '0' or tag.get('condition_id'):
                raise ValueError('Source behavior priority/condition requires native conversion')
            for field in ('on_fail_action', 'on_success_action'):
                action = self.tables.row('ai/so_behaviour_action', 'so_behaviour_action_id', tag[field])
                if action['so_behaviour_action_name'] != 'drop':
                    raise ValueError('Source behavior retains its request after completion; lifecycle adapter required')
            state = self.tables.row('ai/so_behaviour_state', 'so_behaviour_state_id', tag['initial_state'])['so_behaviour_state_name']
            if state not in ('enabled', 'disabled'):
                raise ValueError('Unconverted smart-object behavior state: ' + state)
            ref = tree_identity(owner, tag['tree_file'], tag['tree_name'])
            deps.append(('behavior_trees', ref))
            mailboxes = rows_for(self.tables, 'so_behaviour_tag2mailbox', 'so_behaviour_tag_id', tag['so_behaviour_tag_id'])
            for mailbox in mailboxes:
                require_fields(mailbox, ('so_behaviour_tag_id', 'mailbox_id', 'priority'), 'behavior mailbox link')
                deps.append(('mailboxes', mailbox['mailbox_id']))
            behaviors.append(dict(row=tag, tree=ref, state=state.title(), mailboxes=mailboxes))
        return ImportPlan(deps, dict(owner=owner, row=row, behaviors=behaviors, body=body))

    def convert(self, identity, plan, registered):
        source = plan.source
        target = self.native.guid('smart_entity', identity)
        root = ET.Element('database', name='barbora')
        templates = ET.SubElement(root, 'SmartEntitys', version='1')
        entity = ET.SubElement(templates, 'SmartEntityTemplate', DatabaseId=target,
            Name=self.native.namespace + '_' + source['row']['so_smart_object_name'],
            BrainId=registered['brains'][(source['owner'] + '|' + source['row']['brain_id']).lower()],
            UpdatePriority=bool_value(source['row']['priority']))
        if source['behaviors']:
            behaviors = ET.SubElement(entity, 'BehaviorTemplates')
            for record in source['behaviors']:
                row = record['row']
                behavior = ET.SubElement(behaviors, 'SmartBehaviorTemplate',
                    InitialState=record['state'], MaxInstances=row['max_instances'], Name=row['so_behaviour_tag_name'])
                path, name = registered['behavior_trees'][record['tree'].lower()].split('|')
                ET.SubElement(behavior, 'TreeLocation', FileName=path, TreeName=name)
                if record['mailboxes']:
                    inboxes = ET.SubElement(behavior, 'Inboxes')
                    for m in record['mailboxes']:
                        ET.SubElement(inboxes, 'InboxTemplate', InboxId=registered['mailboxes'][m['mailbox_id'].lower()], Priority=m['priority'])
        path = 'Libs/Tables/ai/smartEntity/SmartEntity__' + self.native.namespace + '_' + target + '.xml'
        return target, {path: xml(root)}, dict(behavior_callbacks=len(source['behaviors']),
            brain=entity.get('BrainId'), runtime_validated=False)


def quest_controller_brains(models, tables, importer):
    """Use the database's controller reference, never a quest-name convention."""
    requests = []
    for model in models:
        row = model['tables']['quest']['rows'][0]
        name = row.get('smart_object')
        if not name:
            continue
        try:
            smart = tables.row('ai/so_smart_object', 'so_smart_object_name', name)
            brain = smart['brain_id']
            if not brain or brain == '00000000-0000-0000-0000-000000000000':
                raise ValueError('Source controller has no behavior brain')
            identity = model['quest'] + '|' + brain
            result = importer.ensure('brains', identity, dict(kind='quest_controller', quest=model['quest'], controller=name))
            record = dict(quest=model['quest'], controller=name, source=smart, target_brain=result)
            if 'smart_entities' in importer.adapters:
                record['target_smart_entity'] = importer.ensure('smart_entities', model['quest'] + '|' + smart['so_smart_object_id'],
                    dict(kind='quest_controller_callbacks', quest=model['quest'], controller=name))
            requests.append(record)
        except (ValueError, KeyError) as error:
            requests.append(dict(quest=model['quest'], controller=name, error=str(error), target_brain=None))
    return requests
