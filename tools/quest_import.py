"""Import retail quest logic into a lossless, non-executable migration model.

No target behavior is silently invented. Native emission is a separate gate.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_sources import MissingRetailSource, key, retail_sources


def literal(value):
    """Remove the behavior format's outer quotes, preserving expression syntax."""
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def behavior_path(value):
    path = PurePosixPath(literal(value).replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in str(path) or path.suffix.lower() != '.xml':
        raise ValueError(f'Invalid behavior include: {value}')
    return 'Libs/AI/' + str(path)


def ast(element):
    return {'op': element.tag, 'attributes': dict(element.attrib),
            'children': [ast(child) for child in element], 'text': element.text or ''}


def parse_behavior(data, entry):
    root = ET.fromstring(data)
    if root.tag != 'BehaviorTrees': raise ValueError(f'Unsupported behavior document: {entry}')
    trees = {}
    for tree in root.findall('BehaviorTree'):
        name = tree.get('name')
        if not name or name in trees: raise ValueError(f'Duplicate/missing behavior name in {entry}')
        roots = tree.findall('Root')
        if len(roots) != 1: raise ValueError(f'Expected one runtime Root: {entry}:{name}')
        # EditorData repeats runtime nodes. ForestContainer holds disconnected
        # authoring nodes; preserve it separately, never execute it as Root.
        trees[name] = {'variables': [dict(v.attrib) for v in tree.findall('./Variables/Variable')],
                       'root': ast(roots[0]),
                       'disconnected': [ast(e) for e in tree.findall('./ForestContainer/*')]}
    return trees


def walk(node):
    yield node
    for child in node['children']: yield from walk(child)


def parse_graph(data, quest):
    root = ET.fromstring(data)
    if root.tag != 'Graph': raise ValueError('Expected a retail quest Graph')
    nodes = {}
    for node in root.findall('./Nodes/Node'):
        identity = node.get('Id')
        if not identity or identity in nodes: raise ValueError('Duplicate/missing quest node ID')
        nodes[identity] = {'attributes': dict(node.attrib), 'payload': [ast(c) for c in node]}
    begins = [n for n in nodes.values() if n['attributes'].get('Class') == 'Quest:Begin']
    if len(begins) != 1: raise ValueError('Expected one quest begin node')
    inputs = next((c['attributes'] for c in begins[0]['payload'] if c['op'] == 'Inputs'), {})
    if inputs.get('Name') != quest: raise ValueError('Quest identity does not match graph')
    edges = [dict(e.attrib) for e in root.findall('./Edges/Edge')]
    unresolved = [e for e in edges if e.get('nodeIn') not in nodes or e.get('nodeOut') not in nodes]
    return {'attributes': dict(root.attrib), 'nodes': nodes, 'edges': edges,
            'unresolved_edges': unresolved}


def select_quest_tables(sources, quest):
    tables = {}
    for item in sources.values():
        root = ET.fromstring(item['data'])
        table = root.find('table')
        if table is None or not table.get('name'): raise ValueError('Invalid retail quest table')
        name = table.get('name')
        if name in tables: raise ValueError(f'Duplicate table {name}')
        tables[name] = {'rows': [dict(row.attrib) for row in table.findall('./rows/row')],
                        'columns': [dict(c.attrib) for c in table.findall('./header/column')],
                        'provenance': item['candidates']}
    matches = [r for r in tables['quest']['rows'] if r.get('quest_name') == quest]
    if len(matches) != 1: raise ValueError('Expected one retail quest database identity')
    identity = matches[0]['quest_id']
    for table in tables.values(): table['rows'] = [r for r in table['rows'] if r.get('quest_id') == identity]
    return identity, tables


def resolve_null_tracking_edges(graph, tables):
    """Recognize the exporter's null asset only with a matching database row.

    Preserve the original edge: map-only tracking has no counted quest asset.
    Zero is not globally a valid node and must never be rewritten to MapAsset.
    """
    resolved, unresolved = [], []
    for edge in graph['unresolved_edges']:
        node = graph['nodes'].get(edge.get('nodeIn'), {})
        inputs = next((c['attributes'] for c in node.get('payload', []) if c['op'] == 'Inputs'), {})
        objective_edges = [e for e in graph['edges'] if e.get('nodeOut') == edge.get('nodeIn')
                           and e.get('portOut') == 'Objective' and e.get('portIn') == 'Tracked'
                           and e.get('enabled') == '1']
        matches = []
        if (edge.get('nodeOut') == '0' and edge.get('portIn') == 'Asset' and edge.get('portOut') == 'Out'
                and node.get('attributes', {}).get('Class') == 'Quest:AssetTracked'
                and len(objective_edges) == 1 and inputs.get('MapAsset') in graph['nodes']):
            matches = [r for r in tables.get('quest_tracked_asset', {}).get('rows', [])
                       if r.get('objective_id') == objective_edges[0]['nodeIn']
                       and r.get('quest_asset_id') == '' and r.get('map_asset_id') == inputs['MapAsset']
                       and r.get('count') == inputs.get('TrackCnt')
                       and r.get('user_id') == inputs.get('User')
                       and (r.get('on_map') == 'True') == (inputs.get('OnMap') == '1')]
        if len(matches) == 1:
            resolved.append({'edge': edge, 'meaning': 'null counted asset with separate map marker',
                             'table': 'quest_tracked_asset', 'row': matches[0]})
        else: unresolved.append(edge)
    graph['null_tracking_edges'] = resolved
    graph['unresolved_edges'] = unresolved


def import_quest(game, quest, bootstrap=(), source_reader=None, require_behavior=True):
    if not re.fullmatch(r'[A-Za-z0-9_]+', quest): raise ValueError('Invalid quest name')
    resolve = source_reader or retail_sources
    requested = {'graph': ('GameData.pak', f'Libs/quests/flowgraphs/{quest}.xml')}
    for index, entry in enumerate(bootstrap):
        requested[f'bootstrap_{index}'] = ('Scripts.pak', behavior_path(entry))
    sources = resolve(game, requested)
    behavior_entry = f'Libs/AI/quests/{quest}.xml'
    try:
        sources.update(resolve(game, {'behavior': ('Scripts.pak', behavior_entry)}))
    except MissingRetailSource:
        if require_behavior:
            raise
    graph = parse_graph(sources['graph']['data'], quest)
    with zipfile.ZipFile(Path(game) / 'Data/Tables.pak') as archive:
        names = [n for n in archive.namelist() if key(n).startswith('libs/tables/quest/') and n.lower().endswith('.xml')]
    table_sources = resolve(game, {n: ('Tables.pak', n) for n in names})
    identity, tables = select_quest_tables(table_sources, quest)
    graph_objectives = {node_id: next(c['attributes'].get('Name') for c in node['payload'] if c['op'] == 'Inputs')
                        for node_id, node in graph['nodes'].items() if node['attributes'].get('Class') == 'Quest:Objective'}
    table_objectives = {r['objective_id']: r['objective_name'] for r in tables['quest_objective']['rows']}
    if len(table_objectives) != len(tables['quest_objective']['rows']): raise ValueError('Duplicate database objective ID')
    renamed = {i: (name, table_objectives[i]) for i, name in graph_objectives.items()
               if i in table_objectives and name != table_objectives[i]}
    if renamed: raise ValueError('Effective quest graph and database objectives disagree')
    # Shipped editor graphs can predate runtime table additions/removals. Keep
    # both inputs, but register every runtime objective, including table-only
    # DLC/tutorial nodes. Never discard a row to fit an older authoring graph.
    graph['objective_table_differences'] = dict(
        graph_only={i: n for i, n in graph_objectives.items() if i not in table_objectives},
        table_only={i: n for i, n in table_objectives.items() if i not in graph_objectives})
    resolve_null_tracking_edges(graph, tables)
    documents = {}
    pending = [item for label, item in sources.items() if label != 'graph']
    while pending:
        source = pending.pop(0); entry = key(source['entry'])
        if entry in documents: continue
        trees = parse_behavior(source['data'], entry)
        documents[entry] = {'source': source, 'trees': trees}
        includes = set()
        for tree in trees.values():
            for node in walk(tree['root']):
                if node['op'] == 'IncludeTree':
                    value = literal(node['attributes']['File'])
                    if not value.startswith('$'): includes.add(behavior_path(value))
        for include in sorted(includes):
            if key(include) not in documents and not any(key(s['entry']) == key(include) for s in pending):
                pending.append(resolve(game, {'include': ('Scripts.pak', include)})['include'])
    operations = Counter(); references = []; unresolved = []; lua = []; variables = []
    for entry, doc in documents.items():
        for name, tree in doc['trees'].items():
            for variable in tree['variables']:
                if variable.get('isPersistent') == '1':
                    variables.append({'document': entry, 'tree': name, **variable})
            for index, node in enumerate(walk(tree['root'])):
                op, attrs = node['op'], node['attributes']; operations[op] += 1
                site = {'document': entry, 'tree': name, 'preorder_index': index}
                if op == 'IncludeTree':
                    if literal(attrs['File']).startswith('$') or literal(attrs['Name']).startswith('$'):
                        ref = dict(site, dynamic=True, file_expression=literal(attrs['File']),
                                   name_expression=literal(attrs['Name']))
                        references.append(ref); unresolved.append(ref)
                        continue
                    ref = dict(site, target=key(behavior_path(attrs['File'])), name=literal(attrs['Name']))
                    references.append(ref)
                    if ref['name'] not in documents[ref['target']]['trees']: unresolved.append(ref)
                if op in ('ExecuteLua', 'LuaGate'):
                    lua.append(dict(site, op=op, code=literal(attrs.get('code', ''))))
    model = {'schema': 1, 'quest': quest, 'quest_id': identity, 'tables': tables,
             'behavior_entry': key(behavior_entry) if 'behavior' in sources else None,
             'behavior_status': 'source_program' if 'behavior' in sources else 'no_same_named_retail_program',
             'executable': False, 'graph': graph,
             'behavior_documents': {entry: {'trees': doc['trees'], 'provenance': doc['source']['candidates']}
                                    for entry, doc in documents.items()},
             'persistent_variables': variables, 'includes': references, 'lua_sites': lua,
             'operations': dict(sorted(operations.items())), 'unresolved_includes': unresolved,
             'graph_provenance': sources['graph']['candidates'],
             'blockers': ['Native Skald adapters and project registration are not implemented',
                          'Actor, item, profile, dialogue and trigger dependency closure is incomplete',
                          'Native startup and save/load have not been validated']}
    if graph['unresolved_edges']: model['blockers'].append('Source graph contains unresolved edge endpoints')
    if unresolved: model['blockers'].append('Behavior includes require runtime resolution or reference missing trees')
    raw = {sources['graph']['entry']: sources['graph']['data']}
    raw.update({doc['source']['entry']: doc['source']['data'] for doc in documents.values()})
    raw.update({item['entry']: item['data'] for item in table_sources.values()})
    return model, raw


def target_catalog(game):
    """Read native node examples, recording evidence without claiming equivalence."""
    result = {}
    with zipfile.ZipFile(Path(game) / 'Data/Scripts.pak') as archive:
        for entry in archive.namelist():
            if not entry.startswith('Quests/Final/') or not entry.endswith('.xml'): continue
            data = archive.read(entry)
            root = ET.fromstring(data)
            for node in root.findall('.//Nodes/*'):
                record = result.setdefault(node.tag, {'count': 0, 'example': entry,
                    'sha256': hashlib.sha256(data).hexdigest(), 'attributes': dict(node.attrib),
                    'input_ports': set()})
                record['count'] += 1
                record['input_ports'].update(c.get('To') for c in node.findall('Edge') if c.get('To'))
                record['input_ports'].update(c.get('Name') for c in node.findall('Constant') if c.get('Name'))
    for record in result.values(): record['input_ports'] = sorted(record['input_ports'])
    return result


def write_import(game, target, output, quest, bootstrap=()):
    output = Path(output)
    if output.exists(): raise ValueError('Choose a new output directory')
    model, raw = import_quest(game, quest, bootstrap)
    native = target_catalog(target)
    output.mkdir(parents=True)
    (output / 'quest-ir.json').write_text(json.dumps(model, indent=2), encoding='utf-8')
    (output / 'kcd2-node-catalog.json').write_text(json.dumps(native, indent=2), encoding='utf-8')
    with zipfile.ZipFile(output / 'retail-sources.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        for entry, data in sorted(raw.items()): archive.writestr(entry, data)
    summary = {'quest': quest, 'source_documents': len(raw),
               'behavior_trees': sum(len(d['trees']) for d in model['behavior_documents'].values()),
               'objective_count': sum(n['attributes'].get('Class') == 'Quest:Objective' for n in model['graph']['nodes'].values()),
               'runtime_operation_types': len(model['operations']), 'lua_sites': len(model['lua_sites']),
               'persistent_variables': len(model['persistent_variables']),
               'unresolved_includes': len(model['unresolved_includes']),
               'unresolved_graph_edges': len(model['graph']['unresolved_edges']),
               'database_resolved_null_edges': len(model['graph']['null_tracking_edges']),
               'quest_id': model['quest_id'],
               'database_rows': {name: len(table['rows']) for name, table in model['tables'].items() if table['rows']},
               'target_node_tags_observed': len(native), 'executable': False, 'blockers': model['blockers']}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kcd1', type=Path, required=True)
    parser.add_argument('--kcd2', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--quest', required=True)
    parser.add_argument('--bootstrap', action='append', default=[], help='Optional source behavior path relative to Libs/AI')
    args = parser.parse_args()
    print(json.dumps(write_import(args.kcd1, args.kcd2, args.output, args.quest, args.bootstrap), indent=2))


if __name__ == '__main__': main()
