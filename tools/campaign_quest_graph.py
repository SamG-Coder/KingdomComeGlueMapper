"""Emit native, persistent quest/objective states from retail quest records.

This is the state/event boundary for behavior conversion, not a replacement
story. All source objectives are retained, including hidden ones. Actions enter
through explicit ports; unsupported behavior, rewards and conditions stay in
the conversion report. Diagnostic inputs are never emitted in production mode.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from skald_state_probe import project_entities


IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
PROGRESS = ('Unchanged', 'Started', 'Completed', 'Failed', 'Canceled')
OBJECTIVE_LOG = dict(Unchanged='None', Started='Started', Completed='Completed',
                     Failed='Canceled', Canceled='Canceled')


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('Invalid native identifier: ' + repr(value))
    return value


def objective_identifier(value):
    """Keep source names in logs; encode only names illegal in native ports."""
    if not isinstance(value, str) or not value:
        raise ValueError('Empty source objective name')
    if IDENTIFIER.fullmatch(value):
        return value
    readable = re.sub(r'[^A-Za-z0-9_]', '_', value)[:48]
    return 'source_' + readable + '_' + hashlib.sha256(value.encode()).hexdigest()[:12]


def xml(element):
    ET.indent(element)
    return ET.tostring(element, encoding='utf-8', xml_declaration=True)


def edge(node, source, target):
    return ET.SubElement(node, 'Edge', From=source, To=target)


def constant(node, name, value):
    return ET.SubElement(node, 'Constant', Name=name, Value=str(value))


def definition(kind, name):
    root = ET.Element('Database', Name='brambora')
    return root, ET.SubElement(ET.SubElement(root, 'Skald'), kind, Name=name)


def validate_model(model):
    name = identifier(model['quest'])
    rows = model['tables']['quest']['rows']
    if len(rows) != 1 or rows[0]['quest_id'] != model['quest_id'] or rows[0]['quest_name'] != name:
        raise ValueError('Quest graph/table identity mismatch')
    objectives = model['tables']['quest_objective']['rows']
    by_id, names = {}, set()
    for row in objectives:
        n, i = objective_identifier(row['objective_name']), row['objective_id']
        if row['quest_id'] != model['quest_id'] or i in by_id or n in names:
            raise ValueError('Duplicate or mismatched objective identity')
        by_id[i] = row
        names.add(n)
    graph = model['graph']
    declared = {}
    begin, end = [], []
    for i, node in graph['nodes'].items():
        cls = node['attributes'].get('Class')
        if cls == 'Quest:Objective':
            inputs = next(p['attributes'] for p in node['payload'] if p['op'] == 'Inputs')
            declared[i] = inputs['Name']
            if i in by_id:
                for a, b in [('IsHidden', 'is_hidden'), ('IsExclusive', 'is_exclusive')]:
                    if (inputs[a] == '1') != (by_id[i][b] == 'True'):
                        raise ValueError('Objective graph/table flags disagree')
        elif cls == 'Quest:Begin': begin.append(i)
        elif cls == 'Quest:End': end.append(i)
    if any(i in by_id and by_id[i]['objective_name'] != n for i, n in declared.items()) or len(begin) != 1 or len(end) != 1:
        raise ValueError('Objective graph/table declarations disagree')
    if graph.get('unresolved_edges'):
        raise ValueError('Unresolved source graph edges')
    transitions = model['tables']['quest_transition']['rows']
    pairs = set()
    for r in transitions:
        pair = (r['from_objective_id'], r['to_objective_id'])
        if (r['quest_id'] != model['quest_id'] or pair in pairs
                or pair[0] not in set(by_id) | set(begin) or pair[1] not in set(by_id) | set(end)):
            raise ValueError('Invalid or duplicate quest transition')
        pairs.add(pair)
    return by_id, begin[0], end[0]


def all_of(nodes, name, values):
    values = list(values)
    if not values: raise ValueError('Empty conjunction')
    if len(values) == 1: return values[0]
    # Use the native A/B naming convention and fold larger prerequisite sets.
    result = values[0]
    for i, value in enumerate(values[1:]):
        node_name = name + '_' + str(i)
        node = ET.SubElement(nodes, 'Function', Name=node_name,
                             MethodName='math::boolean::And', DeclaringType='math::boolean')
        edge(node, result, 'A'); edge(node, value, 'B')
        result = node_name + '.bool'
    return result


def emit_quest(model, progress_type, localization=None, diagnostics=False):
    by_id, begin, end = validate_model(model)
    localization = localization or {}
    root, quest = definition('Quest', model['quest'])
    ports = ET.SubElement(quest, 'Ports')
    nodes = ET.SubElement(quest, 'Nodes')
    objectives = ET.SubElement(quest, 'Objectives')
    output = ET.SubElement(nodes, 'Output', Name='Output')
    report = dict(quest=model['quest'], source_quest_id=model['quest_id'],
                  objectives=[], transitions=model['tables']['quest_transition']['rows'],
                  behavior_execution=False, unsupported=[])
    # The shipped runtime database is authoritative. The retail editor graph
    # can predate a table change (Skalitz has one such IPL checkpoint edge).
    # Keep the disagreement and both provenances visible instead of silently
    # deleting a runtime transition to make it match an authoring document.
    graph_edges = {(e['nodeOut'], e['nodeIn'], e['enabled'] == '1') for e in model['graph']['edges']
                   if e['portIn'] == 'In' and e['portOut'] == 'Out'}
    table_edges = {(r['from_objective_id'], r['to_objective_id'], r['enabled'] == 'True') for r in report['transitions']}
    report['transition_source'] = 'effective retail quest_transition table'
    report['transition_provenance'] = model['tables']['quest_transition'].get('provenance', [])
    report['graph_transition_differences'] = dict(table_only=sorted(table_edges - graph_edges),
                                                graph_only=sorted(graph_edges - table_edges))
    report['objective_table_differences'] = model['graph'].get('objective_table_differences', {})

    def port(name, direction='In', typ='trigger'):
        ET.SubElement(ports, 'Port', Name=name, Direction=direction, Type=typ)
        if diagnostics and direction == 'In':
            ET.SubElement(nodes, 'HasteTrigger', Name='test_' + name)
        return name

    def input_edge(node, name, target):
        edge(node, name, target)
        if diagnostics: edge(node, 'test_' + name + '.OnTrigger', target)

    progress = ET.SubElement(nodes, 'State', Name='quest_progress', TypeT='wh::questmodule::QuestProgress')
    constant(progress, 'DefaultValue', 'None')
    available = ET.SubElement(nodes, 'State', Name='quest_available', TypeT='bool')
    constant(available, 'DefaultValue', model['tables']['quest']['rows'][0]['is_activated'].lower())
    for name, value in [('activate', 'True'), ('deactivate', 'False')]:
        input_edge(available, port(name), 'Set' + value)
    for name, value in [('start', 'Active'), ('complete', 'Done'), ('fail', 'Failed')]:
        input_edge(progress, port(name), 'Set' + value)
    edge(output, 'quest_progress.State', 'Progress')
    port('available', 'Out', 'bool'); edge(output, 'quest_available.State', 'available')
    for name, value in [('started', 'Active'), ('completed', 'Done'), ('failed', 'Failed'), ('unchanged', 'None')]:
        port(name, 'Out', 'bool'); edge(output, 'quest_progress.' + value, name)
    ET.SubElement(quest, 'QuestName', Text=localization.get('name', model['quest']))
    if localization.get('description'): ET.SubElement(quest, 'Text', Text=localization['description'])
    states = {}
    for i, row in sorted(by_id.items(), key=lambda pair: int(pair[0])):
        name = objective_identifier(row['objective_name'])
        state_name = 'objective_' + i
        state = ET.SubElement(nodes, 'State', Name=state_name, TypeT=progress_type)
        constant(state, 'DefaultValue', 'Unchanged')
        for verb, value in [('start', 'Started'), ('complete', 'Completed'), ('fail', 'Failed'), ('cancel', 'Canceled')]:
            input_edge(state, port(verb + '_' + name), 'Set' + value)
        for value in PROGRESS:
            out_port = name + '_' + value
            port(out_port, 'Out', 'bool'); edge(output, state_name + '.' + value, out_port)
        states[i] = state
        hidden = row['is_hidden'] == 'True'
        if not hidden:
            title = localization.get('objectives', {}).get(i, {})
            decl = ET.SubElement(objectives, 'Objective', Name=name, TypeT=progress_type)
            ET.SubElement(decl, 'LocalizedName', Text=title.get('name', row['objective_name']))
            logs = ET.SubElement(decl, 'Logs')
            for value, status in OBJECTIVE_LOG.items():
                log = ET.SubElement(logs, 'EnumLog', Name=value, Type=status)
                text = title.get('log_' + value.lower())
                if text: ET.SubElement(log, 'Log', Text=text)
            binding = ET.SubElement(nodes, name, Name='journal_' + i)
            edge(binding, state_name + '.State', 'Progress')
        if diagnostics:
            trace = ET.SubElement(nodes, 'Trace', Name='trace_' + i, TypeT=progress_type)
            edge(trace, state_name + '.State', 'Value')
            edge(trace, state_name + '.OnExec', 'Exec')
        report['objectives'].append(dict(source=row, native_state=state_name, journal_visible=not hidden))
        if row.get('condition') or row.get('autocomplete_timeout_str') not in ('', '-1', None) or row.get('expiration_timeout_str') not in ('', '-1', None):
            report['unsupported'].append(dict(kind='completion_policy', objective=name,
                                              condition=row.get('condition'),
                                              autocomplete=row.get('autocomplete_timeout_str'),
                                              expiration=row.get('expiration_timeout_str')))
    # Preserve the original directed graph with explicit all-predecessor gates.
    # A disabled transition can never activate an objective. Completed/canceled
    # states are not reset by a repeated predecessor notification.
    incoming = defaultdict(list)
    for r in report['transitions']:
        if r['enabled'] == 'True': incoming[r['to_objective_id']].append(r['from_objective_id'])
    for target, predecessors in incoming.items():
        if target == end:
            report['unsupported'].append(dict(kind='quest_end_policy', predecessors=predecessors))
            continue
        row = by_id[target]
        if row['is_exclusive'] == 'True':
            report['unsupported'].append(dict(kind='exclusive_transition', objective=row['objective_name']))
            continue
        terms = [('quest_progress.Active' if p == begin else 'objective_' + p + '.Completed') for p in predecessors]
        terms.append('objective_' + target + '.Unchanged')
        condition = all_of(nodes, 'ready_' + target, terms)
        gate = ET.SubElement(nodes, 'If', Name='advance_' + target)
        edge(gate, condition, 'Condition')
        for p in predecessors:
            edge(gate, 'quest_progress.OnActive' if p == begin else 'objective_' + p + '.OnCompleted', 'Exec')
        edge(states[target], 'advance_' + target + '.True', 'SetStarted')
    for table, payload in model['tables'].items():
        if table.startswith('quest_reward') and payload['rows']:
            report['unsupported'].append(dict(kind='rewards', table=table, rows=payload['rows']))
    return xml(root), report


def emit_project(models, project, startup_quest=None, localization=None, diagnostics=False):
    identifier(project)
    if not models or len({m['quest'] for m in models}) != len(models):
        raise ValueError('Expected unique source quests')
    if startup_quest is not None and startup_quest not in {m['quest'] for m in models}:
        raise ValueError('Startup quest is not part of the compiled set')
    root, host = definition('Project', project)
    definitions = ET.SubElement(host, 'Definitions')
    nodes = ET.SubElement(host, 'Nodes')
    types = ET.SubElement(host, 'Types')
    progress_type = project + 'ObjectiveProgress'
    enum = ET.SubElement(types, 'Type', TypeName=progress_type)
    for value, status in OBJECTIVE_LOG.items(): ET.SubElement(enum, 'StateTypeEnumeration', Name=value, ObjectiveValueType=status)
    log_types = ET.SubElement(host, 'ObjectiveValueTypes')
    # Native LogType has no Failed value. Keep Failed as a distinct persistent
    # source state, with the native canceled journal presentation.
    for status in ('None', 'Started', 'Completed', 'Canceled'):
        ET.SubElement(log_types, 'ObjectiveValueType', Type=status,
                      IsPast=str(status in ('Completed', 'Failed', 'Canceled')).lower(),
                      Icon='Check' if status == 'Completed' else 'Exclamation')
    files, reports = {}, []
    for model in models:
        name = model['quest']
        blob, report = emit_quest(model, progress_type, (localization or {}).get(name), diagnostics)
        files[project + '/' + name + '.xml'] = blob
        reports.append(report)
        ET.SubElement(definitions, 'Definition', File=project + '/' + name + '.xml')
        ET.SubElement(nodes, name, Name=name)
    if startup_quest:
        # This explicitly selected startup is a development gate until the
        # original master-controller/profile/actor initialization is compiled.
        ET.SubElement(nodes, 'GameStart', Name='new_game')
        latch = ET.SubElement(nodes, 'State', Name='started', TypeT='bool')
        constant(latch, 'DefaultValue', 'false')
        not_started = ET.SubElement(nodes, 'Function', Name='not_started',
                                   MethodName='math::boolean::Not', DeclaringType='math::boolean')
        edge(not_started, 'started.State', 'Value')
        gate = ET.SubElement(nodes, 'If', Name='first_start')
        edge(gate, 'new_game.OnStart', 'Exec'); edge(gate, 'not_started.bool', 'Condition')
        sequence = ET.SubElement(nodes, 'TriggerSequence', Name='initialize')
        edge(sequence, 'first_start.True', 'Exec')
        edge(latch, 'initialize.A', 'SetTrue')
        q = nodes.find(startup_quest)
        edge(q, 'initialize.B', 'activate'); edge(q, 'initialize.C', 'start')
    if diagnostics:
        ET.SubElement(nodes, 'HasteTrigger', Name='test_save')
        save = ET.SubElement(nodes, 'SaveGameWithNotification', Name='save')
        edge(save, 'test_save.OnTrigger', 'EnqueueSave')
        trace = ET.SubElement(nodes, 'Trace', Name='save_finished', TypeT='string')
        constant(trace, 'Value', 'native save completed'); edge(trace, 'save.OnDone', 'Exec')
    files[project + '.xml'] = xml(root)
    report = dict(schema=1, project=project, quests=reports,
                  startup_quest=startup_quest, startup_mode='diagnostic' if startup_quest else 'external native event required',
                  source_behavior_executed=False, diagnostics=diagnostics, campaign_ready=False,
                  save_round_trip_validated=False,
                  files={n: hashlib.sha256(b).hexdigest() for n, b in files.items()})
    return files, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ir', type=Path, action='append', required=True)
    p.add_argument('--project', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--diagnostic-start-quest')
    p.add_argument('--diagnostics', action='store_true')
    p.add_argument('--kcd1', type=Path, help='Retail source installation for original journal text')
    p.add_argument('--locale', default='English')
    a = p.parse_args()
    models = [json.loads(f.read_text(encoding='utf-8')) for f in a.ir]
    localization, text_report = None, None
    if a.kcd1:
        from campaign_quest_text import load
        localization, text_report = load(a.kcd1, models, a.locale)
    files, report = emit_project(models, a.project, a.diagnostic_start_quest,
                                localization=localization, diagnostics=a.diagnostics)
    report['localization'] = text_report
    if a.output.exists(): raise FileExistsError('Choose a new output directory')
    for name, payload in files.items():
        path = a.output / 'Data/Quests' / name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload)
    (a.output / 'project-entities.xml').write_bytes(project_entities(a.project, 'Quests/' + a.project + '.xml'))
    (a.output / 'conversion.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(dict(project=a.project, objectives=sum(len(q['objectives']) for q in report['quests']),
                          campaign_ready=False)))


if __name__ == '__main__': main()
