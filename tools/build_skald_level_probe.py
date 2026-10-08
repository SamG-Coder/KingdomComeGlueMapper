"""Package an isolated level with a native Skald project and diagnostic startup.

GameStart activation here tests engine lifecycle wiring, not source guards.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

from skald_state_probe import project_entities


def inject_entities(mission, fragment):
    root = ET.fromstring(mission)
    existing = list(root.iter('Entity'))
    names = {e.get('Name') for e in existing}
    guids = {e.get('EntityGuid') for e in existing}
    # Imported visual entities often omit IDs. Starting at 1 collides with the
    # player's reserved entity ID even when no explicit IDs exist in the XML.
    next_id = max(1000 + len(existing),
                  max((int(e.get('EntityId', '0')) for e in existing), default=0) + 1)
    for entity in ET.fromstring(fragment):
        if entity.get('Name') in names or entity.get('EntityGuid') in guids:
            raise ValueError('Project entity already exists')
        entity = copy.deepcopy(entity)
        entity.set('EntityId', str(next_id)); next_id += 1
        entity.set('Pos', '735,3421,64')
        root.append(entity)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def build(base, destination, graph_file, data):
    if destination.exists(): raise ValueError('Destination already exists')
    if not re.fullmatch(r'[A-Za-z0-9_]+', destination.name): raise ValueError('Invalid level name')
    graph = ET.parse(graph_file).getroot()
    project = graph.find('./Skald/Project')
    name = project.get('Name')
    nodes = project.find('Nodes')
    state = nodes.find("State[@Name='source_state']")
    if state is None: raise ValueError('Expected generated state probe')
    ET.SubElement(nodes, 'GameStart', Name='probe_start')
    edge = state.find('Edge')
    ET.SubElement(state, 'Edge', From='probe_start.OnStart', To=edge.get('To'))
    relative = 'Quests/' + destination.name + '/' + name + '.xml'
    target = data / relative
    if target.exists(): raise ValueError('Graph target already exists')
    fragment = project_entities(name, relative)
    with zipfile.ZipFile(base / 'level.pak') as src:
        mission = inject_entities(src.read('objects_mission0.xml'), fragment)
        destination.mkdir(parents=True)
        shutil.copy2(base / 'terrain.pak', destination / 'terrain.pak')
        with zipfile.ZipFile(destination / 'level.pak', 'x', zipfile.ZIP_STORED) as out:
            for entry in src.infolist():
                payload = src.read(entry)
                if entry.filename == 'objects_mission0.xml': payload = mission
                elif entry.filename in ('levelinfo.xml', 'leveldata.xml'):
                    doc = ET.fromstring(payload)
                    if entry.filename == 'levelinfo.xml': doc.set('Name', 'data/levels/' + destination.name)
                    else: doc.find('LevelInfo').set('Name', destination.name)
                    payload = ET.tostring(doc, encoding='utf-8', xml_declaration=True)
                    if entry.filename == 'levelinfo.xml': (destination / entry.filename).write_bytes(payload)
                out.writestr(entry, payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ET.tostring(graph, encoding='utf-8', xml_declaration=True))
    report = dict(level=destination.name, project=name, graph=relative,
                  activation='diagnostic GameStart; source guards not evaluated', campaign_ready=False)
    (destination / 'skald-probe.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--base-level', required=True)
    p.add_argument('--level', required=True)
    p.add_argument('--graph', type=Path, required=True)
    a = p.parse_args()
    if not all(re.fullmatch(r'[A-Za-z0-9_]+', v) for v in (a.base_level, a.level)):
        p.error('Invalid level name')
    print(json.dumps(build(a.data/'Levels'/a.base_level, a.data/'Levels'/a.level, a.graph, a.data)))
