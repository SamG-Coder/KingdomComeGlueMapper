"""Native persistent arrival objective, completed by speaking to its person."""
import xml.etree.ElementTree as ET
from upgrade_map import xml

QUEST = 'gmtravel_visit_rattay'
TYPE = 'GMTravelVisitProgress'


def edge(node, source, target):
    ET.SubElement(node, 'Edge', From=source, To=target)


def quest_graph():
    root = ET.Element('Database', Name='brambora')
    quest = ET.SubElement(ET.SubElement(root, 'Skald'), 'Quest', Name=QUEST)
    ports = ET.SubElement(quest, 'Ports')
    ET.SubElement(ports, 'Port', Name='arrive', Direction='In', Type='trigger')
    ET.SubElement(ports, 'Port', Name='talked', Direction='In', Type='trigger')
    nodes = ET.SubElement(quest, 'Nodes')
    progress = ET.SubElement(nodes, 'State', Name='progress', TypeT='wh::questmodule::QuestProgress')
    ET.SubElement(progress, 'Constant', Name='DefaultValue', Value='None')
    first = ET.SubElement(nodes, 'If', Name='first_arrival')
    edge(first, 'arrive', 'Exec'); edge(first, 'progress.None', 'Condition')
    edge(progress, 'first_arrival.True', 'SetActive')
    spoken = ET.SubElement(nodes, 'If', Name='spoke_to_innkeeper')
    edge(spoken, 'talked', 'Exec'); edge(spoken, 'progress.Active', 'Condition')
    edge(progress, 'spoke_to_innkeeper.True', 'SetDone')
    edge(ET.SubElement(nodes, 'Output', Name='Output'), 'progress.State', 'Progress')
    state = ET.SubElement(nodes, 'State', Name='objective', TypeT=TYPE)
    ET.SubElement(state, 'Constant', Name='DefaultValue', Value='None')
    edge(state, 'progress.OnActive', 'SetActive'); edge(state, 'progress.OnDone', 'SetDone')
    # Event-only diagnostics distinguish a missing dialogue event from a saved
    # quest that is already done or has not become active. No polling or timers.
    for name, event in [('talk_received', 'talked'), ('quest_completed', 'progress.OnDone')]:
        trace = ET.SubElement(nodes, 'Trace', Name=name, TypeT='wh::questmodule::QuestProgress')
        edge(trace, event, 'Exec'); edge(trace, 'progress.State', 'Value')
    edge(ET.SubElement(nodes, 'visit_inn', Name='journal'), 'objective.State', 'Progress')
    ET.SubElement(quest, 'QuestName', StringName='gmtravel_visit_title', Text='A visit to Rattay')
    obj = ET.SubElement(ET.SubElement(quest, 'Objectives'), 'Objective', Name='visit_inn', TypeT=TYPE)
    ET.SubElement(obj, 'LocalizedName', StringName='gmtravel_visit_objective', Text='Talk to the Rattay innkeeper.')
    logs = ET.SubElement(obj, 'Logs')
    for value, kind, text in [('None', 'None', ''), ('Active', 'Started',
        'Talk to the innkeeper at the inn outside Rattay\u2019s upper gate.'), ('Done', 'Completed', 'I spoke to the Rattay innkeeper.')]:
        log = ET.SubElement(logs, 'EnumLog', Name=value, Type=kind)
        if value == 'Active': log.set('Marker', 'rattay_innkeeper')
        if text:
            ET.SubElement(log, 'Log', StringName='gmtravel_visit_' + value.lower(), Text=text)
    return xml(root)


def attach(graphs, graph_path, level, player_soul, target_soul):
    result = dict(graphs)
    parent = graph_path.rsplit('/', 1)[0]
    entry_path = parent + '/entry/' + level + '.xml'
    entry = ET.fromstring(result[entry_path])
    host = entry.find('./Skald/Level')
    ET.SubElement(host.find('Ports'), 'Port', Name='arrived', Direction='Out', Type='trigger')
    output = host.find("Nodes/Output[@Name='Output']")
    edge(output, 'OnWake', 'arrived'); edge(output, 'OnLevelSwitched', 'arrived')
    root = ET.fromstring(result[graph_path]); project = root.find('./Skald/Project')
    ET.SubElement(project.find('Definitions'), 'Definition', File='visit_rattay.xml')
    node = ET.SubElement(project.find('Nodes'), QUEST, Name=QUEST)
    edge(node, level + '.arrived', 'arrive')
    edge(node, 'rattay_innkeeper.dialog_started', 'talked')
    types = ET.SubElement(project, 'Types')
    typ = ET.SubElement(types, 'Type', TypeName=TYPE)
    logs = ET.SubElement(project, 'ObjectiveValueTypes')
    for value, kind in [('None', 'None'), ('Active', 'Started'), ('Done', 'Completed')]:
        ET.SubElement(typ, 'StateTypeEnumeration', Name=value, ObjectiveValueType=kind)
        ET.SubElement(logs, 'ObjectiveValueType', Type=kind, IsPast=str(value == 'Done').lower(),
                      Icon='Check' if value == 'Done' else 'Exclamation')
    assets = project.find('Assets')
    if assets is None: assets = ET.SubElement(project, 'Assets')
    ET.SubElement(assets, 'SoulAsset', Name='travel_player', SharedSoulGuids=player_soul)
    ET.SubElement(assets, 'SoulAsset', Name='rattay_innkeeper', SharedSoulGuids=target_soul)
    # Keep the saved State nodes at their original fully-qualified paths. The
    # journal itself must be a child of Level, as in the shipped campaign;
    # a Quest directly under Project has no region and says "Different region".
    state_root = ET.fromstring(quest_graph())
    state = state_root.find('./Skald/Quest')
    state.tag = 'Module'
    # Quest defaults HasteNamespace=true; Module defaults false in Skald.Core.
    # Retain the old namespace explicitly when changing the wrapper type.
    state.set('HasteNamespace', 'true')
    journal_root = ET.Element('Database', Name='brambora')
    journal = ET.SubElement(ET.SubElement(journal_root, 'Skald'), 'Quest', Name=QUEST, HasteNamespace='false')
    journal_ports = ET.SubElement(journal, 'Ports')
    journal_nodes = ET.SubElement(journal, 'Nodes')
    for child in list(state):
        if child.tag in ('QuestName', 'Objectives'):
            state.remove(child); journal.append(child)
    state.find('Nodes').remove(state.find("Nodes/visit_inn[@Name='journal']"))
    state_output = state.find("Nodes/Output[@Name='Output']")
    for child in list(state_output): state_output.remove(child)
    outputs = [('quest_progress','wh::questmodule::QuestProgress','progress.State'),
               ('objective_progress',TYPE,'objective.State')]
    definitions = host.find('Definitions')
    if definitions is None: definitions = ET.SubElement(host, 'Definitions')
    ET.SubElement(definitions, 'Definition', File='../visit_rattay_journal.xml')
    regional = ET.SubElement(host.find('Nodes'), QUEST, Name=QUEST)
    level_node = project.find('Nodes/'+level)
    for port, typ, source in outputs:
        ET.SubElement(state.find('Ports'), 'Port', Name=port, Direction='Out', Type=typ)
        edge(state_output, source, port)
        ET.SubElement(host.find('Ports'), 'Port', Name=port, Direction='In', Type=typ)
        edge(level_node, QUEST+'.'+port, port)
        edge(regional, port, port)
        ET.SubElement(journal_ports, 'Port', Name=port, Direction='In', Type=typ)
    edge(ET.SubElement(journal_nodes, 'Output', Name='Output'), 'quest_progress', 'Progress')
    edge(ET.SubElement(journal_nodes, 'visit_inn', Name='journal'), 'objective_progress', 'Progress')
    result[entry_path] = xml(entry)
    result[graph_path] = xml(root)
    result[parent + '/visit_rattay.xml'] = xml(state_root)
    result[parent + '/visit_rattay_journal.xml'] = xml(journal_root)
    strings = ET.Element('Table')
    for element in journal_root.iter():
        if element.get('StringName'):
            row = ET.SubElement(strings, 'Row')
            for value in [element.get('StringName'), element.get('Text'), element.get('Text')]:
                ET.SubElement(row, 'Cell').text = value
    return result, xml(strings)
