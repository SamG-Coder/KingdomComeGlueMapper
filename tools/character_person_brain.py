"""Upgrade source-only helper brains and their table/mailbox dependencies.

Ordinary people use native semantic adapters. Invisible helpers retain their
source tree, buffs and inbox, with native expression serialization. Explicit
empty Storm appearance components replace the old LODLock's render protection.
"""
import copy
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET
import zipfile

from campaign_behavior import CONTRACTS, native_catalog, quote, reference
from quest_import import literal
from upgrade_map import read, xml


HELPER_BRAINS = {'npc_invisible'}
NODE_CONTRACTS = dict(CONTRACTS, IgnoreEmittedInformations={'WUID': 'reference'},
    DisablePerception={'Perceptor': 'value', 'Perceptible': 'value', 'ToWhom': 'reference'},
    AddBuff={'SoulWUID': 'reference', 'BuffGUID': 'string_expression'},
    SetVisibility={'ItemWUID': 'reference', 'Visibility': 'value'})


def capture_brain(ai, tables, reader):
    brain = ai['source_brain']
    if brain['brain_name'] not in HELPER_BRAINS: return
    bindings = [r for r in tables.get('ai/brain2subbrain')['rows'] if r['brain_id'] == brain['brain_id']]
    subbrains = []
    for binding in bindings:
        identity = binding['subbrain_id']
        definition = tables.row('ai/subbrain', 'subbrain_id', identity)
        behavior = tables.row('ai/subbrain_behaviour_tree', 'subbrain_id', identity)
        entry = 'Libs/AI/' + behavior['file_name']
        source = reader(reader.game, {'behavior': ('Scripts.pak', entry)})['behavior']
        subbrains.append(dict(binding=binding, definition=definition, behavior=behavior,
                              xml=source['data'].decode('utf-8-sig'), provenance=source['candidates']))
    mailboxes = []
    for binding in tables.get('ai/brain2mailbox')['rows']:
        if binding['brain_id'] == brain['brain_id']:
            row = tables.row('ai/mailbox', 'mailbox_id', binding['mailbox_id'])
            mailboxes.append(dict(binding=binding, definition=row))
    ai.update(brain_dependencies=dict(subbrains=subbrains, mailboxes=mailboxes), invisible_helper=True)


def upgrade_helper_tree(blob, catalog):
    source = ET.fromstring(blob)
    output = ET.Element('BehaviorTrees')
    def node(old):
        if old.tag == 'LODLock':
            # No native LODLock exists. Renderless helper registration, plus
            # native SetVisibility, carries its hide-only purpose; retain all
            # children in their authored order.
            new = ET.Element('Sequence')
        else:
            contract = NODE_CONTRACTS.get(old.tag)
            native = catalog['nodes'].get(old.tag)
            if contract is None or native is None: raise ValueError('Helper node needs upgrade: ' + old.tag)
            new = ET.Element(old.tag)
            for name, value in old.attrib.items():
                if name not in contract or name not in native['attributes']:
                    raise ValueError('Helper attribute needs upgrade: ' + old.tag + '.' + name)
                kind = contract[name]
                value = literal(value)
                if kind == 'reference': value = reference(value)
                elif kind in ('string_expression', 'mailbox'): value = quote(value) if value else ''
                elif kind == 'expression' and value not in ('', 'true', 'false'):
                    raise ValueError('Helper expression needs upgrade: ' + value)
                new.set(name, value)
        for child in old: new.append(node(child))
        return new
    for tree in source.findall('BehaviorTree'):
        target = ET.SubElement(output, 'BehaviorTree', name=tree.get('name'), is_function='0')
        variables = ET.SubElement(target, 'Variables')
        for variable in tree.findall('Variables/Variable'):
            if variable.get('type') not in catalog['variable_types']:
                raise ValueError('Helper variable type needs upgrade: ' + variable.get('type'))
            converted = copy.deepcopy(variable); converted.set('isPersistent', variable.get('isPersistent', '0'))
            variables.append(converted)
        target.append(node(tree.find('Root')))
    return xml(output)


def register_brain(ai, target, resources, native_brains, cache):
    source = ai['source_brain']
    if source['brain_name'] not in HELPER_BRAINS: return native_brains
    dependencies = ai['brain_dependencies']
    prefix = 'gluemapper_' + source['brain_name']
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/brain/' + source['brain_id']))
    def add(table, container, name, rows):
        root = ET.Element('database', name='barbora')
        group = ET.SubElement(root, container, version='1')
        for row in rows: ET.SubElement(group, name, row)
        path = 'Libs/Tables/ai/' + table + '__' + prefix + '.xml'
        payload = xml(root)
        if path in resources and resources[path] != payload: raise ValueError('Conflicting helper brain dependency')
        resources[path] = payload
    add('brain', 'brains', 'brain', [dict(brain_id=identity, brain_name=prefix)])
    result = copy.deepcopy(native_brains)
    ET.SubElement(result.find('brains'), 'brain', brain_id=identity, brain_name=prefix)
    if 'helper_native_catalog' not in cache: cache['helper_native_catalog'] = native_catalog(target)
    if 'helper_mailboxes' not in cache:
        with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as z:
            cache['helper_mailboxes'] = ET.fromstring(read(z, 'Libs/Tables/ai/mailbox.xml'))
            cache['helper_buffs'] = ET.fromstring(read(z, 'Libs/Tables/rpg/buff.xml'))
    subbrains, behaviors, bindings = [], [], []
    for dependency in dependencies['subbrains']:
        original = dependency['definition']
        sid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/subbrain/' + original['subbrain_id']))
        definition = dict(original, subbrain_id=sid, subbrain_name=prefix + '_' + sid.replace('-', ''))
        definition['always_active'] = definition['always_active'].lower()
        subbrains.append(definition)
        binding = dict(dependency['binding'], brain_id=identity, subbrain_id=sid); bindings.append(binding)
        filename = 'gluemapper/' + sid + '.xml'
        behaviors.append(dict(dependency['behavior'], subbrain_id=sid, file_name=filename))
        blob = upgrade_helper_tree(dependency['xml'], cache['helper_native_catalog'])
        for buff in ET.fromstring(blob).iter('AddBuff'):
            guid = buff.get('BuffGUID').strip("'")
            if not any(r.get('buff_id') == guid for r in cache['helper_buffs'].iter('buff')):
                raise ValueError('Helper buff dependency needs conversion: ' + guid)
        resources['AI/' + filename] = blob
    add('subbrain', 'subbrains', 'subbrain', subbrains)
    add('subbrain_behaviour_tree', 'subbrain_behaviour_trees', 'subbrain_behaviour_tree', behaviors)
    add('brain2subbrain', 'brain2subbrains', 'brain2subbrain', bindings)
    mailboxes = []
    for dependency in dependencies['mailboxes']:
        name = dependency['definition']['mailbox_name']
        matches = [m for m in cache['helper_mailboxes'].iter('mailbox') if m.get('mailbox_name') == name]
        if len(matches) != 1: raise ValueError('Helper inbox needs conversion: ' + name)
        mailboxes.append(dict(dependency['binding'], brain_id=identity, mailbox_id=matches[0].get('mailbox_id')))
    if mailboxes: add('brain2mailbox', 'brain2mailboxs', 'brain2mailbox', mailboxes)
    return result
