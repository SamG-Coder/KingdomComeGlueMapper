"""Validate emitted population bindings; this does not claim live AI behavior."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import guid_value
from character_person import native_xml
from upgrade_map import read
from retail_pak import PakSet
from character_shared_materials import ROOT, SHARED, key


def validate(stage, package, tables_output=None):
    stage, package = Path(stage), Path(package)
    package_report = json.loads((package / 'population-package.json').read_text())
    service_profile = package_report.get('population_profile') == 'services-only'
    if service_profile:
        expected, target = [], Path(package_report['target'])
    else:
        report = json.loads((stage / 'population.json').read_text())
        expected = package_report.get('placements', [r for r in report['actors'] if r['status'] == 'converted'])
        target = Path(report['target'])
    with zipfile.ZipFile(target / 'Data/Tables.pak') as z:
        brains = {e.get('brain_id') for e in native_xml(read(z, 'Libs/Tables/ai/brain.xml')).iter('brain')}
        classes = {e.get('social_class_id') for e in native_xml(read(z, 'Libs/Tables/rpg/social_class.xml')).iter('social_class')}
    main = package / 'Data/gluemappertravel.pak'
    with PakSet(main.parent) as z:
        names = {n.lower(): n for n in z.namelist()}
        if len(names) != len(z.namelist()): raise ValueError('Duplicate case-insensitive package member')
        tables = []
        for name in z.namelist():
            if name.startswith('Libs/Tables/') and name.endswith('.xml'):
                blob = z.read(name); tables.append(ET.fromstring(blob))
                if tables_output:
                    path = Path(tables_output) / name
                    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(blob)
        def records(tag, identity):
            rows = [r for t in tables for r in t.iter(tag)]
            result = {r.get(identity): r for r in rows}
            if len(result) != len(rows): raise ValueError('Duplicate emitted ' + tag + ' identity')
            return result
        brains.update(records('brain', 'brain_id'))
        souls = records('soul', 'soul_id')
        factions = records('Faction', 'Name')
        for person in expected:
            row = souls[person['soul_id']]
            if row.get('soul_name') != person['target_name']: raise ValueError('Wrong population soul name')
            if row.get('brain_id') not in brains: raise ValueError('Missing brain dependency')
            if row.get('social_class_id') not in classes: raise ValueError('Missing social-class dependency')
            if row.get('factionName') and row.get('factionName') not in factions: raise ValueError('Missing faction dependency')
        texture_references = 0
        component_references = 0
        shared_files = shared_shader_slots = 0
        for table in tables:
            for component in table.iter('Component'):
                prefix = component.get('FilePath', '')
                if prefix != 'gluetravel/': continue
                for element in component.iter():
                    for attribute in ('Model', 'Material'):
                        value = element.get(attribute, '')
                        if not value or attribute == 'Material' and not value.lower().endswith('.mtl'): continue
                        path = key(ROOT + prefix + value)
                        if path not in names: raise ValueError('Unbundled character binding: ' + path)
                        component_references += 1
        for name in z.namelist():
            if name.lower().endswith('.mtl') and name.startswith(('objects/characters/gluetravel/gmp_', SHARED)):
                material = ET.fromstring(z.read(name))
                if name.startswith(SHARED):
                    shared_files += 1
                    shared_shader_slots += sum(bool(e.get('Shader')) for e in material.iter('Material'))
                for texture in material.iter('Texture'):
                    path = texture.get('File', '').replace('\\', '/').lower()
                    if path.startswith('objects/characters/gluepopulation/'):
                        texture_references += 1
                        if path not in names: raise ValueError('Unbundled population texture: ' + path)
    with zipfile.ZipFile(package / 'Data/Levels/kcd1_travel/level.pak') as z:
        mission = ET.fromstring(read(z, 'objects_mission0.xml'))
        base_guids = {guid_value(e.get('EntityGuid')) for e in mission if e.get('EntityGuid')}
        all_entities = list(mission)
        layer_names = {e.get('Name').lower() for e in ET.fromstring(read(z, 'leveldata.xml')).findall('Layers/Layer')}
        for name in z.namelist():
            if name.lower().startswith('layers/') and name.lower().endswith('.xml'):
                members = list(ET.fromstring(read(z, name)).iter('Entity'))
                for entity in members:
                    if entity.get('Layer', '').lower() not in layer_names:
                        raise ValueError('Conditional entity references an unregistered layer')
                all_entities.extend(members)
        wh = ET.fromstring(read(z, 'whdata_0'))
        rows = wh.findall('SoulList/Souls/Soul')
        instances = {r.findtext('Guid'): r for r in rows}
        entities = {guid_value(e.get('EntityGuid')): e for e in all_entities if e.get('EntityGuid')}
        if len(entities) != sum(bool(e.get('EntityGuid')) for e in all_entities):
            raise ValueError('Duplicate persistent entity GUID across streamed layers')
        if len(instances) != len(rows): raise ValueError('Duplicate placed soul instance')
        ids = [e.get('EntityId') for e in all_entities if e.get('EntityId')]
        if len(ids) != len(set(ids)): raise ValueError('Duplicate runtime entity ID')
        for person in expected:
            if person.get('resident', True) != (int(person['source_guid'], 16) in base_guids):
                raise ValueError('Conditional population was promoted to the base world or resident was removed')
            instance = instances[person['instance_guid']]
            entity = entities[int(person['source_guid'], 16)]
            if (instance.findtext('SharedSoulGuid') != person['soul_id']
                or guid_value(instance.findtext('EntityGuid')) != int(person['source_guid'], 16)
                or entity.get('Name') != person['target_name']):
                raise ValueError('Population world binding differs from source identity')
    with zipfile.ZipFile(package / 'Localization/English_xml.pak') as z:
        for name in z.namelist():
            if name.lower() in ('text_ui_items.xml', 'text_ui_soul.xml'):
                keys = [r.findtext('Cell') for r in ET.fromstring(read(z, name)).findall('Row')]
                if len(keys) != len(set(keys)): raise ValueError('Duplicate localization key in ' + name)
    result = dict(converted_placements=len(expected), tables=len(tables),
                  checked_texture_references=texture_references,
                  checked_component_references=component_references,
                  shared_material_files=shared_files, shared_shader_slots=shared_shader_slots,
                  world_bindings_verified=True, runtime_verified=False)
    if service_profile:
        from population_build_profile import validate as validate_services
        result.update(validate_services(package, package_report['base_package']))
    (package / 'population-validation.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--package', required=True)
    parser.add_argument('--tables-output')
    args = parser.parse_args()
    print(json.dumps(validate(args.stage, args.package, args.tables_output)))
