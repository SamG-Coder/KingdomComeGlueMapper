"""Emit native placed-NPC identities from authored world records.

Unconverted links and initialization remain explicit dependencies. These fragments
are not a runnable campaign and must be merged into a level before installation.
"""
import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from campaign_population import values


def entity_guid(value):
    compact = value.replace('-', '').lower()
    if not re.fullmatch('[0-9a-f]{16}', compact) or int(compact, 16) == 0:
        raise ValueError('Expected nonzero 64-bit entity GUID: ' + value)
    return compact[:8] + '-' + compact[8:12] + '-' + compact[12:]


def build(world, registered):
    objects = ET.Element('Objects')
    root = ET.Element('Root', version='1')
    souls = ET.SubElement(ET.SubElement(root, 'SoulList'), 'Souls')
    pending = []; omitted = []; seen = {k: set() for k in ('Name', 'EntityGuid', 'EntityId', 'Guid')}
    for record in world['npcs']:
        source = record['entity']; attrs = source['attributes']
        identity = values(record['soul'])
        shared = identity.get('SharedSoulGuid')
        if shared not in registered:
            omitted.append(attrs['Name']); continue
        guid = entity_guid(attrs['EntityGuid'])
        if str(int(guid.replace('-', ''), 16)) != identity.get('EntityGuid'):
            raise ValueError('Entity/soul GUID mismatch: ' + attrs['Name'])
        if identity.get('Name') != attrs['Name']:
            raise ValueError('Entity/soul name mismatch: ' + attrs['Name'])
        for key, value in dict(Name=attrs['Name'], EntityGuid=guid,
                               EntityId=attrs['EntityId'], Guid=identity['Guid']).items():
            if value in seen[key]: raise ValueError('Duplicate placed actor ' + key + ': ' + value)
            seen[key].add(value)
        native = {k: attrs[k] for k in ('Name', 'Pos', 'Rotate', 'Scale', 'EntityClass',
                                       'EntityId', 'CastShadowMinSpec') if k in attrs}
        native['EntityGuid'] = guid
        # Source layer GUID syntax is not a native editor-layer path.
        native['EditorLayer'] = 'KDC1/' + record['layer']
        ET.SubElement(objects, 'Entity', native)
        soul = ET.SubElement(souls, 'Soul', version='8')
        for key, value in dict(SharedSoulGuid=shared, Guid=identity['Guid'],
                               EntityGuid=guid, Name=attrs['Name']).items():
            ET.SubElement(soul, key).text = value
        pending.append(dict(name=attrs['Name'], layer=record['layer'],
                            source_entity=source, source_soul=record['soul'],
                            reason='AI links, instance initialization and profile activation require conversion'))
    return objects, root, dict(placed=len(pending), omitted_unregistered=omitted,
                              dependencies=pending, campaign_ready=False, installed=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--world', type=Path, required=True)
    p.add_argument('--population-package', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists(): raise FileExistsError('Choose a new output directory')
    world = json.loads(a.world.read_text(encoding='utf-8'))
    package = json.loads((a.population_package/'population-package.json').read_text(encoding='utf-8'))
    registered = {r['soul_id'] for r in package['characters'] if r['source_person_registered']}
    objects, souls, report = build(world, registered)
    a.output.mkdir(parents=True)
    for name, tree in [('objects_mission0.xml', objects), ('whdata_0', souls)]:
        (a.output/name).write_bytes(ET.tostring(tree, encoding='utf-8', xml_declaration=True))
    (a.output/'placement-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'dependencies'}))
