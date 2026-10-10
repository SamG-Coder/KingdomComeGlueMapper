"""Shared source-person identity, native soul, appearance and world registration.

Quest and shop adapters select a person; this helper does not select a quest,
merchant, gender, body or coordinates on their behalf.
"""
import copy
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_dependency_adapters import SourceTables
from campaign_sources import RetailSourceReader
from campaign_entity_links import native_guid
from upgrade_map import read, xml
from character_person_ai import (capture_person_ai, native_brain_and_class,
                               stat_operations, apply_person_properties, receipt, register_factions)


def native_xml(blob):
    return ET.fromstring(re.sub(rb'<!--.*?-->', b'', blob, flags=re.S))


def unique(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f'Expected one {label}, found {len(values)}')
    return values[0]


def resolve_person(source, name, level='rataje', *, include_ai=True):
    with RetailSourceReader(source) as reader:
        tables = SourceTables(source, reader)
        soul = tables.row('rpg/soul', 'soul_name', name)
        evidence = {k: v['provenance'] for k, v in tables.cache.items()}
    with zipfile.ZipFile(Path(source) / 'Data/Levels' / level / 'level.pak') as archive:
        mission = read(archive, 'objects_mission0.xml')
        actor_sources = {'objects_mission0.xml': hashlib.sha256(mission).hexdigest()}
        entities = list(ET.fromstring(mission).iter('Entity'))
        if not any(e.get('Name') == name for e in entities):
            for entry in archive.namelist():
                if entry.lower().startswith('layers/') and entry.lower().endswith('.xml'):
                    blob = read(archive, entry)
                    if name.encode('utf-8') in blob:
                        layer_entities = list(ET.fromstring(blob).iter('Entity'))
                        entities.extend(layer_entities)
                        if any(e.get('Name') == name for e in layer_entities):
                            actor_sources[entry] = hashlib.sha256(blob).hexdigest()
        actor = unique((e for e in entities if e.get('Name') == name and
                        e.get('EntityClass') in ('NPC', 'NPC_Female')), 'source person')
        instance = unique((s for s in ET.fromstring(read(archive, 'whdata_0')).findall('./SoulList/Souls/Soul')
                           if s.findtext('Name') == name), 'source person instance')
    if instance.findtext('SharedSoulGuid') != soul['soul_id']:
        raise ValueError('Source person instance/table identity differs')
    ai = None
    if include_ai:
        with RetailSourceReader(source) as reader:
            tables = SourceTables(source, reader)
            ai = capture_person_ai(tables, soul, actor, instance, entities)
            evidence.update({k: v['provenance'] for k, v in tables.cache.items()})
    return dict(name=name, soul=soul, actor=actor, instance=instance, source_level=level,
                ai=ai,
                provenance=dict(tables=evidence, mission_sha256=hashlib.sha256(mission).hexdigest(),
                                actor_sources=actor_sources))


def register_person(add_entity, wh, person, placement=None):
    """Preserve source soul/instance identity, using the converted entity GUID."""
    actor = person['actor']
    pose = placement if placement is not None else actor
    souls = wh.find('./SoulList/Souls')
    if souls is None:
        raise ValueError('Destination requires a native SoulList')
    identity = person['instance'].findtext('Guid')
    if any(s.findtext('Guid') == identity or s.findtext('Name') == person['name'] for s in souls):
        raise ValueError('Person is already registered: ' + person['name'])
    npc = add_entity(person['name'], actor.get('EntityClass'), pose.get('Pos'),
                     native_guid(int(actor.get('EntityGuid'), 16)), pose.get('Rotate'))
    apply_person_properties(npc, actor)
    soul = ET.SubElement(souls, 'Soul', version='8')
    for key, value in dict(SharedSoulGuid=person['soul']['soul_id'], Guid=identity,
                           EntityGuid=npc.get('EntityGuid'), Name=person['name']).items():
        ET.SubElement(soul, key).text = value
    return npc


def actor_resources(target, person, character, namespace, *, role_name, native_soul_table, native_soul_name,
                    native_role_name, resources=None):
    """Add a person to a resource collection; retain previously imported people."""
    files = dict(resources or {})
    with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
        native = native_xml(read(archive, native_soul_table))
        template = next(e for e in native.iter('soul') if e.get('soul_name') == native_soul_name)
        roles = native_xml(read(archive, 'Libs/Tables/rpg/role.xml'))
        role = copy.deepcopy(next(e for e in roles.iter('role') if e.get('role_name') == native_role_name))
        brains = native_xml(read(archive, 'Libs/Tables/ai/brain.xml'))
        classes = native_xml(read(archive, 'Libs/Tables/rpg/social_class.xml'))
    root = ET.Element('database', name='barbora')
    soul = ET.SubElement(ET.SubElement(root, 'souls', version='2'), 'soul', dict(template.attrib))
    soul.set('soul_id', person['soul']['soul_id']); soul.set('soul_name', person['name'])
    soul.set('soul_archetype_id', character['archetype'])
    soul.attrib.pop('skald_character_name', None)
    brain, social = native_brain_and_class(person['ai'], brains, classes)
    soul.set('brain_id', brain)
    soul.set('social_class_id', social)
    soul.set('factionName', register_factions(files, person['ai']))
    for key in ('xp_multiplier', 'digestion_multiplier', 'initial_clothing_dirt'):
        if person['soul'].get(key) not in ('', None): soul.set(key, person['soul'][key])
    # Source VIP IDs are quest-specific; a matching integer in KCD2 can name a
    # different character. Keep the neutral template VIP until explicitly mapped.
    role.set('role_name', role_name)
    role_root = ET.Element('database', name='barbora')
    ET.SubElement(role_root, 'roles', version='1').append(role)
    additions = {'Libs/Tables/rpg/soul__' + namespace + '.xml': xml(root),
                 'Libs/Tables/rpg/role__' + namespace + '.xml': xml(role_root)}
    if files.keys() & additions.keys():
        raise ValueError('Person namespace already registered: ' + namespace)
    files.update(additions)
    if 'Libs/Storm/storm.xml' in files:
        storm = ET.fromstring(files['Libs/Storm/storm.xml'])
    else:
        with zipfile.ZipFile(Path(target) / 'Data/IPL_GameData.pak') as archive:
            storm = ET.fromstring(read(archive, 'Libs/Storm/storm.xml'))
    operations = {
        'roles': [('addRole', dict(name=role_name))],
        'names': [('setUiName', dict(name=person['instance'].findtext('StaticData/NameStringId')))],
        'appearance': [('set' + k.title(), dict(name=v)) for k, v in character['appearance'].items()],
        'equipment': [('setInventory', dict(preset=character['inventory']))],
        'abilities': stat_operations(person['soul']),
    }
    for task, steps in operations.items():
        filename = task + '/' + namespace + '.xml'
        definition = storm.find(f"tasks/task[@name='{task}']")
        if definition is None: raise ValueError('Missing native Storm task: ' + task)
        ET.SubElement(definition, 'source', path=filename)
        rules = ET.Element('storm')
        rule = ET.SubElement(ET.SubElement(rules, 'rules'), 'rule', name=namespace + '_' + task)
        ET.SubElement(ET.SubElement(rule, 'selectors'), 'hasName', name=person['name'])
        ops = ET.SubElement(rule, 'operations')
        for name, attributes in steps: ET.SubElement(ops, name, attributes)
        files['Libs/Storm/' + filename] = xml(rules)
    files['Libs/Storm/storm.xml'] = xml(storm)
    files['Libs/GlueMapper/PersonImport/' + namespace + '.json'] = receipt(person, soul, namespace)
    return files

