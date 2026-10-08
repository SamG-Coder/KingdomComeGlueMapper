"""Discover retail startup and preserve campaign links for native translation.

The result is a link model, not an executable quest package. Source expressions
and branch ancestry remain intact; initialization is never flattened into grants.
"""
import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_sources import retail_sources
from quest_import import ast, literal, parse_behavior
from upgrade_map import read


def discover_entry(data):
    root = ET.fromstring(data)
    if root.tag != 'Graph': raise ValueError('Expected a New Game action graph')
    targets = []
    for node in root.findall('./Nodes/Node'):
        inputs = node.find('Inputs')
        if node.get('Class') != 'System:ExecuteScript' or inputs is None: continue
        code = inputs.get('Script', '')
        # A bounded recognizer for the observed retail dispatch idiom. Preserve
        # the whole script; arbitrary Lua is not evaluated during conversion.
        entities = re.findall(r'System\.GetEntityByName\(\s*[\'"]([^\'"]+)[\'"]\s*\)', code)
        messages = re.findall(r'SendMessageToEntity\([^,]+,\s*[\'"]([^\'"]+)[\'"]', code)
        if len(entities) == len(messages) == 1:
            targets.append({'entity_name': entities[0], 'message': messages[0], 'node': node.get('Id'), 'script': code})
    if len(targets) != 1: raise ValueError('Ambiguous or unsupported New Game dispatch; no controller guessed')
    return {'dispatch': targets[0], 'graph': ast(root)}


def entity_index(documents):
    records = {}; indexes = {k: defaultdict(list) for k in ('EntityId', 'EntityGuid', 'Name')}
    for entry, data in sorted(documents.items()):
        for index, entity in enumerate(ET.fromstring(data).iter('Entity')):
            record_id = f'{entry}#{index}'
            records[record_id] = {'source': entry, 'entity': ast(entity), 'attributes': dict(entity.attrib)}
            for field in indexes:
                value = entity.get(field)
                if value and value != '0': indexes[field][value].append(record_id)
    return records, indexes


def link_entities(documents, controller):
    records, indexes = entity_index(documents)
    starts = indexes['Name'].get(controller, [])
    if len(starts) != 1: raise ValueError('Startup controller is missing or ambiguous in source level')
    queue = deque(starts); selected = {}; links = []; unresolved = []
    while queue:
        identity = queue.popleft()
        if identity in selected: continue
        record = records[identity]; selected[identity] = record
        for container in record['entity']['children']:
            if container['op'] != 'EntityLinks': continue
            for link in container['children']:
                attrs = link['attributes']; guid, eid = attrs.get('TargetGuid'), attrs.get('TargetId')
                candidates = indexes['EntityGuid'].get(guid, []) if guid not in (None, '', '0') else indexes['EntityId'].get(eid, [])
                result = {'from': identity, 'source_link': attrs, 'candidates': list(candidates)}
                if len(candidates) == 1:
                    # An explicit GUID and ID disagreement is not a successful link.
                    dest = records[candidates[0]]['attributes']
                    if eid not in (None, '', '0') and dest.get('EntityId') != eid:
                        result['reason'] = 'GUID and entity ID disagree'; unresolved.append(result); continue
                    result['to'] = candidates[0]; links.append(result); queue.extend(candidates)
                else:
                    result['reason'] = 'missing target' if not candidates else 'layer variants require profile selection'
                    unresolved.append(result)
    return {'controller': starts[0], 'entities': selected, 'links': links, 'unresolved': unresolved}


# These are source semantic families, not claims of implemented KCD2 adapters.
FAMILIES = {
    'CreateItem': 'inventory', 'SetInventory': 'inventory', 'EquipItem': 'equipment',
    'SetClothing': 'equipment', 'SetQuest': 'quest', 'SetQuestObjective': 'objective',
    'EnableProfile': 'world_profile', 'AreaPresence': 'trigger', 'DistanceGate': 'trigger',
    'ProcessMessage': 'subscription', 'InstantSendMessageToNPC': 'actor_message',
    'SetOwner': 'ownership', 'AddBuff': 'player_or_actor_state',
    'ExecuteLua': 'lua', 'LuaGate': 'lua_condition',
}


def operation_records(trees):
    result = []
    def visit(node, tree, path, ancestry):
        if node['op'] in FAMILIES:
            result.append({'tree': tree, 'path': path, 'family': FAMILIES[node['op']],
                           'operation': node['op'], 'arguments': node['attributes'],
                           'ancestry': ancestry, 'adapter_status': 'unimplemented'})
        for index, child in enumerate(node['children']):
            visit(child, tree, f'{path}/{index}', ancestry + [{'op': node['op'], 'attributes': node['attributes']}])
    for name, tree in trees.items(): visit(tree['root'], name, 'Root', [])
    return result


def build_links(game, level, action='Libs/UI/UIActions/MM_NewGame.xml'):
    if not re.fullmatch(r'[A-Za-z0-9_]+', level): raise ValueError('Invalid source level')
    source = retail_sources(game, {'entry': ('GameData.pak', action)})['entry']
    entry = discover_entry(source['data'])
    archive_path = Path(game) / 'Data/Levels' / level / 'level.pak'
    with zipfile.ZipFile(archive_path) as archive:
        documents = {n: read(archive, n) for n in archive.namelist()
                     if n.lower() == 'objects_mission0.xml' or (n.lower().startswith('layers/') and n.lower().endswith('.xml'))}
    world = link_entities(documents, entry['dispatch']['entity_name'])
    # Match QuestObject names against retail quest rows, rather than constructing
    # a quest from the link label (labels also describe actors and places).
    table = retail_sources(game, {'quests': ('Tables.pak', 'Libs/Tables/quest/quest.xml')})['quests']
    rows = ET.fromstring(table['data']).findall('./table/rows/row')
    quest_records = []; scripts = {}; issues = []
    for identity, record in world['entities'].items():
        if record['attributes'].get('EntityClass') != 'QuestObject': continue
        name = record['attributes'].get('Name')
        matches = [r for r in rows if r.get('smart_object') == name]
        if len(matches) != 1:
            issues.append({'entity': identity, 'reason': 'quest database identity missing or ambiguous'}); continue
        row = dict(matches[0].attrib); quest = row['quest_name']
        if not re.fullmatch(r'[A-Za-z0-9_]+', quest): raise ValueError('Invalid database quest name')
        item = retail_sources(game, {'script': ('Scripts.pak', f'Libs/AI/quests/{quest}.xml')})['script']
        trees = parse_behavior(item['data'], item['entry'])
        scripts[quest] = {'source': item['entry'], 'provenance': item['candidates'],
                         'trees': trees, 'operations': operation_records(trees)}
        quest_records.append({'entity': identity, 'database_row': row, 'behavior': item['entry']})
    return {'schema': 1, 'source_level': level, 'entry': entry, 'entry_provenance': source['candidates'],
            'quest_table_provenance': table['candidates'], 'world': world, 'quests': quest_records,
            'scripts': scripts, 'issues': issues,
            'level_documents': {n: hashlib.sha256(b).hexdigest() for n, b in documents.items()},
            'lifecycle': {'new_game': 'native adapter required', 'restore': 'native restore required; do not replay initialization'},
            'executable': False,
            'limitations': ['Source level archive patch precedence is not yet resolved',
                            'Profile-dependent entity variants remain unresolved',
                            'Inventory/clothing operations retain source arguments; native adapters and item conversion are required',
                            'Native quest registration, triggers and save/load are not implemented']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kcd1', type=Path, required=True)
    parser.add_argument('--level', required=True)
    parser.add_argument('--entry-action', default='Libs/UI/UIActions/MM_NewGame.xml')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('Choose a new output file')
    model = build_links(args.kcd1, args.level, args.entry_action)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(model, indent=2), encoding='utf-8')
    print(json.dumps({'controller': model['entry']['dispatch']['entity_name'],
                      'quests': [r['database_row']['quest_name'] for r in model['quests']],
                      'entities': len(model['world']['entities']), 'links': len(model['world']['links']),
                      'unresolved': len(model['world']['unresolved']), 'executable': False}, indent=2))


if __name__ == '__main__': main()
