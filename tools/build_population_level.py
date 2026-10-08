"""Build a separate development level with native placed-character bindings."""
import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile


def merge_objects(base, additions):
    root = ET.fromstring(base)
    for field in ('Name', 'EntityId', 'EntityGuid'):
        values = {e.get(field) for e in root.iter('Entity') if e.get(field)}
        for e in additions:
            value = e.get(field)
            if value and value in values: raise ValueError('Entity collision: ' + field + '=' + value)
            if value: values.add(value)
    root.extend(copy.deepcopy(list(additions)))
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def build(base, placement, destination):
    if destination.exists(): raise FileExistsError('Choose a new level directory')
    if not re.fullmatch('[A-Za-z0-9_]+', destination.name): raise ValueError('Invalid level name')
    objects = ET.parse(placement/'objects_mission0.xml').getroot()
    souls = ET.parse(placement/'whdata_0').getroot()
    entities = {e.get('EntityGuid'):e.get('Name') for e in objects}
    bindings = {s.findtext('EntityGuid'):s.findtext('Name') for s in souls.findall('./SoulList/Souls/Soul')}
    if entities != bindings or len(entities) != len(objects): raise ValueError('Unmatched character bindings')
    with zipfile.ZipFile(base/'level.pak') as source:
        if 'whdata_0' in source.namelist():
            raise ValueError('Base already has soul data; explicit merge required')
        mission = merge_objects(source.read('objects_mission0.xml'), objects)
        destination.mkdir(parents=True)
        shutil.copy2(base/'terrain.pak', destination/'terrain.pak')
        with zipfile.ZipFile(destination/'level.pak', 'x', zipfile.ZIP_STORED) as out:
            for entry in source.infolist():
                data = source.read(entry)
                if entry.filename == 'objects_mission0.xml': data = mission
                elif entry.filename in ('levelinfo.xml', 'leveldata.xml'):
                    doc = ET.fromstring(data)
                    if entry.filename == 'levelinfo.xml': doc.set('Name', 'data/levels/' + destination.name)
                    else: doc.find('LevelInfo').set('Name', destination.name)
                    data = ET.tostring(doc, encoding='utf-8', xml_declaration=True)
                    if entry.filename == 'levelinfo.xml': (destination/entry.filename).write_bytes(data)
                out.writestr(entry, data)
            out.writestr('whdata_0', ET.tostring(souls, encoding='utf-8', xml_declaration=True))
    report = dict(level=destination.name, actors=list(entities.values()),
                  base=str(base), placement=str(placement), campaign_ready=False)
    (destination/'population-level.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--placement', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.base, a.placement, a.destination)))
