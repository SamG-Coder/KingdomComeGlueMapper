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


def native_xml(blob):
    return ET.fromstring(re.sub(rb'<!--.*?-->', b'', blob, flags=re.S))


def unique(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f'Expected one {label}, found {len(values)}')
    return values[0]


def resolve_person(source, name, level='rataje'):
    with RetailSourceReader(source) as reader:
        tables = SourceTables(source, reader)
        soul = tables.row('rpg/soul', 'soul_name', name)
        evidence = {k: v['provenance'] for k, v in tables.cache.items()}
    with zipfile.ZipFile(Path(source) / 'Data/Levels' / level / 'level.pak') as archive:
        mission = read(archive, 'objects_mission0.xml')
        entities = list(ET.fromstring(mission).iter('Entity'))
        actor = unique((e for e in entities if e.get('Name') == name and
                        e.get('EntityClass') in ('NPC', 'NPC_Female')), 'source person')
        instance = unique((s for s in ET.fromstring(read(archive, 'whdata_0')).findall('./SoulList/Souls/Soul')
                           if s.findtext('Name') == name), 'source person instance')
    if instance.findtext('SharedSoulGuid') != soul['soul_id']:
        raise ValueError('Source person instance/table identity differs')
    return dict(name=name, soul=soul, actor=actor, instance=instance, source_level=level,
                provenance=dict(tables=evidence, mission_sha256=hashlib.sha256(mission).hexdigest()))


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
    root = ET.Element('database', name='barbora')
    soul = ET.SubElement(ET.SubElement(root, 'souls', version='2'), 'soul', dict(template.attrib))
    soul.set('soul_id', person['soul']['soul_id']); soul.set('soul_name', person['name'])
    soul.set('soul_archetype_id', character['archetype'])
    soul.attrib.pop('skald_character_name', None)
    # The caller selects the native brain. Importing appearance does not imply
    # conversion of the source person's daily schedule or quest behaviors.
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
    return files

