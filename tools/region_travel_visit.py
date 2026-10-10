"""Native persistent arrival objective, completed by speaking to its person."""
import xml.etree.ElementTree as ET
import re
from upgrade_map import xml

QUEST = 'gmtravel_visit_rattay'
TYPE = 'GMTravelVisitProgress'

# Stable defaults keep the existing Rattay save-state paths unchanged.
RATTAY = dict(quest=QUEST, progress_type=TYPE, file='visit_rattay',
    objective='visit_inn', dialogue='rattay_innkeeper', marker='rattay_innkeeper',
    gate='spoke_to_innkeeper', prefix='gmtravel_visit', title='A visit to Rattay',
    objective_text='Talk to the Rattay innkeeper.',
    active_text='Talk to the innkeeper at the inn outside Rattay\u2019s upper gate.',
    done_text='I spoke to the Rattay innkeeper.', quest_type=None)


def ensure(parent, tag):
    node = parent.find(tag)
    return node if node is not None else ET.SubElement(parent, tag)



def edge(node, source, target):
    ET.SubElement(node, 'Edge', From=source, To=target)


def quest_graph(spec=None):
    spec = spec or RATTAY
    quest_name, progress_type = spec['quest'], spec['progress_type']
    root = ET.Element('Database', Name='brambora')
    quest = ET.SubElement(ET.SubElement(root, 'Skald'), 'Quest', Name=quest_name)
    ports = ET.SubElement(quest, 'Ports')
    ET.SubElement(ports, 'Port', Name='arrive', Direction='In', Type='trigger')
    ET.SubElement(ports, 'Port', Name='talked', Direction='In', Type='trigger')
    nodes = ET.SubElement(quest, 'Nodes')
    progress = ET.SubElement(nodes, 'State', Name='progress', TypeT='wh::questmodule::QuestProgress')
    ET.SubElement(progress, 'Constant', Name='DefaultValue', Value='None')
    first = ET.SubElement(nodes, 'If', Name='first_arrival')
    edge(first, 'arrive', 'Exec'); edge(first, 'progress.None', 'Condition')
    edge(progress, 'first_arrival.True', 'SetActive')
    spoken = ET.SubElement(nodes, 'If', Name=spec['gate'])
    edge(spoken, 'talked', 'Exec'); edge(spoken, 'progress.Active', 'Condition')
    edge(progress, spec['gate'] + '.True', 'SetDone')
    edge(ET.SubElement(nodes, 'Output', Name='Output'), 'progress.State', 'Progress')
    state = ET.SubElement(nodes, 'State', Name='objective', TypeT=progress_type)
    ET.SubElement(state, 'Constant', Name='DefaultValue', Value='None')
    edge(state, 'progress.OnActive', 'SetActive'); edge(state, 'progress.OnDone', 'SetDone')
    # Event-only diagnostics distinguish a missing dialogue event from a saved
    # quest that is already done or has not become active. No polling or timers.
    for name, event in [('talk_received', 'talked'), ('quest_completed', 'progress.OnDone')]:
        trace = ET.SubElement(nodes, 'Trace', Name=name, TypeT='wh::questmodule::QuestProgress')
        edge(trace, event, 'Exec'); edge(trace, 'progress.State', 'Value')
    edge(ET.SubElement(nodes, spec['objective'], Name='journal'), 'objective.State', 'Progress')
    ET.SubElement(quest, 'QuestName', StringName=spec['prefix'] + '_title', Text=spec['title'])
    obj = ET.SubElement(ET.SubElement(quest, 'Objectives'), 'Objective', Name=spec['objective'], TypeT=progress_type)
    ET.SubElement(obj, 'LocalizedName', StringName=spec['prefix'] + '_objective', Text=spec['objective_text'])
    logs = ET.SubElement(obj, 'Logs')
    for value, kind, text in [('None', 'None', ''), ('Active', 'Started',
        spec['active_text']), ('Done', 'Completed', spec['done_text'])]:
        log = ET.SubElement(logs, 'EnumLog', Name=value, Type=kind)
        if value == 'Active': log.set('Marker', spec['marker'])
        if text:
            ET.SubElement(log, 'Log', StringName=spec['prefix'] + '_' + value.lower(), Text=text)
    return xml(root)


def attach(graphs, graph_path, level, player_soul, target_soul, *, spec=None):
    spec = spec or RATTAY
    for field in ('quest', 'progress_type', 'file', 'objective', 'dialogue', 'marker', 'gate', 'prefix'):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', spec[field]):
            raise ValueError('Invalid visit quest identifier: ' + field)
    quest_name, progress_type = spec['quest'], spec['progress_type']
    filename = spec['file']
    result = dict(graphs)
    parent = graph_path.rsplit('/', 1)[0]
    entry_path = parent + '/entry/' + level + '.xml'
    entry = ET.fromstring(result[entry_path])
    host = entry.find('./Skald/Level')
    if host.find("Ports/Port[@Name='arrived']") is None:
        ET.SubElement(host.find('Ports'), 'Port', Name='arrived', Direction='Out', Type='trigger')
        output = host.find("Nodes/Output[@Name='Output']")
        edge(output, 'OnWake', 'arrived'); edge(output, 'OnLevelSwitched', 'arrived')
    root = ET.fromstring(result[graph_path]); project = root.find('./Skald/Project')
    if (project.find(f"Nodes/*[@Name='{quest_name}']") is not None
            or project.find(f"Types/Type[@TypeName='{progress_type}']") is not None
            or parent + '/' + filename + '.xml' in result
            or project.find(f"Assets/*[@Name='{spec['marker']}']") is not None):
        raise ValueError('Visit quest already registered or namespace collides: ' + quest_name)
    ET.SubElement(project.find('Definitions'), 'Definition', File=filename + '.xml')
    node = ET.SubElement(project.find('Nodes'), quest_name, Name=quest_name)
    edge(node, level + '.arrived', 'arrive')
    edge(node, spec['dialogue'] + '.dialog_started', 'talked')
    types = ensure(project, 'Types')
    typ = ET.SubElement(types, 'Type', TypeName=progress_type)
    logs = ensure(project, 'ObjectiveValueTypes')
    for value, kind in [('None', 'None'), ('Active', 'Started'), ('Done', 'Completed')]:
        ET.SubElement(typ, 'StateTypeEnumeration', Name=value, ObjectiveValueType=kind)
        if logs.find(f"ObjectiveValueType[@Type='{kind}']") is None:
            ET.SubElement(logs, 'ObjectiveValueType', Type=kind, IsPast=str(value == 'Done').lower(),
                          Icon='Check' if value == 'Done' else 'Exclamation')
    assets = project.find('Assets')
    if assets is None: assets = ET.SubElement(project, 'Assets')
    if assets.find("SoulAsset[@Name='travel_player']") is None:
        ET.SubElement(assets, 'SoulAsset', Name='travel_player', SharedSoulGuids=player_soul)
    ET.SubElement(assets, 'SoulAsset', Name=spec['marker'], SharedSoulGuids=target_soul)
    # Keep the saved State nodes at their original fully-qualified paths. The
    # journal itself must be a child of Level, as in the shipped campaign;
    # a Quest directly under Project has no region and says "Different region".
    state_root = ET.fromstring(quest_graph(spec))
    state = state_root.find('./Skald/Quest')
    state.tag = 'Module'
    # Quest defaults HasteNamespace=true; Module defaults false in Skald.Core.
    # Retain the old namespace explicitly when changing the wrapper type.
    state.set('HasteNamespace', 'true')
    journal_root = ET.Element('Database', Name='brambora')
    journal = ET.SubElement(ET.SubElement(journal_root, 'Skald'), 'Quest', Name=quest_name, HasteNamespace='false')
    if spec['quest_type']:
        journal.set('Type', spec['quest_type'])
    journal_ports = ET.SubElement(journal, 'Ports')
    journal_nodes = ET.SubElement(journal, 'Nodes')
    for child in list(state):
        if child.tag in ('QuestName', 'Objectives'):
            state.remove(child); journal.append(child)
    state.find('Nodes').remove(state.find("Nodes/" + spec['objective'] + "[@Name='journal']"))
    state_output = state.find("Nodes/Output[@Name='Output']")
    for child in list(state_output): state_output.remove(child)
    outputs = [('quest_progress','wh::questmodule::QuestProgress','progress.State'),
               ('objective_progress',progress_type,'objective.State')]
    definitions = host.find('Definitions')
    if definitions is None: definitions = ET.SubElement(host, 'Definitions')
    ET.SubElement(definitions, 'Definition', File='../' + filename + '_journal.xml')
    regional = ET.SubElement(host.find('Nodes'), quest_name, Name=quest_name)
    level_node = project.find('Nodes/'+level)
    for port, typ, source in outputs:
        level_port = port if spec == RATTAY else filename + '_' + port
        ET.SubElement(state.find('Ports'), 'Port', Name=port, Direction='Out', Type=typ)
        edge(state_output, source, port)
        ET.SubElement(host.find('Ports'), 'Port', Name=level_port, Direction='In', Type=typ)
        edge(level_node, quest_name+'.'+port, level_port)
        edge(regional, level_port, port)
        ET.SubElement(journal_ports, 'Port', Name=port, Direction='In', Type=typ)
    edge(ET.SubElement(journal_nodes, 'Output', Name='Output'), 'quest_progress', 'Progress')
    edge(ET.SubElement(journal_nodes, spec['objective'], Name='journal'), 'objective_progress', 'Progress')
    result[entry_path] = xml(entry)
    result[graph_path] = xml(root)
    result[parent + '/' + filename + '.xml'] = xml(state_root)
    result[parent + '/' + filename + '_journal.xml'] = xml(journal_root)
    strings = ET.Element('Table')
    for element in journal_root.iter():
        if element.get('StringName'):
            row = ET.SubElement(strings, 'Row')
            for value in [element.get('StringName'), element.get('Text'), element.get('Text')]:
                ET.SubElement(row, 'Cell').text = value
    return result, xml(strings)
