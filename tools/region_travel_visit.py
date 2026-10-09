"""Native persistent arrival objective, completed by the imported inn polygon."""
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
    nodes = ET.SubElement(quest, 'Nodes')
    progress = ET.SubElement(nodes, 'State', Name='progress', TypeT='wh::questmodule::QuestProgress')
    ET.SubElement(progress, 'Constant', Name='DefaultValue', Value='None')
    first = ET.SubElement(nodes, 'If', Name='first_arrival')
    edge(first, 'arrive', 'Exec'); edge(first, 'progress.None', 'Condition')
    edge(progress, 'first_arrival.True', 'SetActive')
    edge(progress, 'visit.OnEnter', 'SetDone')
    edge(ET.SubElement(nodes, 'Output', Name='Output'), 'progress.State', 'Progress')
    state = ET.SubElement(nodes, 'State', Name='objective', TypeT=TYPE)
    ET.SubElement(state, 'Constant', Name='DefaultValue', Value='None')
    edge(state, 'progress.OnActive', 'SetActive'); edge(state, 'progress.OnDone', 'SetDone')
    visit = ET.SubElement(nodes, 'AreaTrigger', Name='visit')
    ET.SubElement(visit, 'Asset', Name='Souls', Alias='travel_player')
    ET.SubElement(visit, 'Asset', Name='Areas', Alias='rattay_inn_area')
    edge(visit, 'progress.Active', 'IsActive')
    edge(ET.SubElement(nodes, 'visit_inn', Name='journal'), 'objective.State', 'Progress')
    ET.SubElement(quest, 'QuestName', StringName='gmtravel_visit_title', Text='A visit to Rattay')
    obj = ET.SubElement(ET.SubElement(quest, 'Objectives'), 'Objective', Name='visit_inn', TypeT=TYPE)
    ET.SubElement(obj, 'LocalizedName', StringName='gmtravel_visit_objective', Text='Visit the Rattay tavern.')
    logs = ET.SubElement(obj, 'Logs')
    for value, kind, text in [('None', 'None', ''), ('Active', 'Started',
        'Visit the inn outside Rattay\u2019s upper gate.'), ('Done', 'Completed', 'I visited the Rattay tavern.')]:
        log = ET.SubElement(logs, 'EnumLog', Name=value, Type=kind)
        if text:
            ET.SubElement(log, 'Log', StringName='gmtravel_visit_' + value.lower(), Text=text)
    return xml(root)


def attach(graphs, graph_path, level, player_soul):
    result = dict(graphs)
    parent = graph_path.rsplit('/', 1)[0]
    entry_path = parent + '/entry/' + level + '.xml'
    entry = ET.fromstring(result[entry_path])
    host = entry.find('./Skald/Level')
    ET.SubElement(host.find('Ports'), 'Port', Name='arrived', Direction='Out', Type='trigger')
    output = host.find("Nodes/Output[@Name='Output']")
    edge(output, 'OnWake', 'arrived'); edge(output, 'OnLevelSwitched', 'arrived')
    result[entry_path] = xml(entry)
    root = ET.fromstring(result[graph_path]); project = root.find('./Skald/Project')
    ET.SubElement(project.find('Definitions'), 'Definition', File='visit_rattay.xml')
    node = ET.SubElement(project.find('Nodes'), QUEST, Name=QUEST)
    edge(node, level + '.arrived', 'arrive')
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
    ET.SubElement(assets, 'TriggerAreaAsset', Name='rattay_inn_area')
    result[graph_path] = xml(root)
    result[parent + '/visit_rattay.xml'] = quest_graph()
    strings = ET.Element('Table')
    for element in ET.fromstring(result[parent + '/visit_rattay.xml']).iter():
        if element.get('StringName'):
            row = ET.SubElement(strings, 'Row')
            for value in [element.get('StringName'), element.get('Text'), element.get('Text')]:
                ET.SubElement(row, 'Cell').text = value
    return result, xml(strings)
