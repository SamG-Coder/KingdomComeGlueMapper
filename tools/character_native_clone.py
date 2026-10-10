"""Independent clones of a registered native actor for controlled import tests.

The template supplies behavior and interaction; a source person supplies only
appearance, clothes and the displayed name. Shared services remain shared.
"""
import copy
import json
import uuid
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import native_guid, guid_value
from character_person import unique
from upgrade_map import read, xml


def identity(namespace):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/native-clone/' + namespace))


def clone_world(files, template_name, namespace, position):
    """Copy an actor and its explicitly owned home, remapping persistent links.

    This does not recursively clone shared town services or other NPCs. Unknown
    external links retain their targets. The original input bytes are untouched.
    """
    result = dict(files)
    mission = ET.fromstring(files['objects_mission0.xml'])
    wh = ET.fromstring(files['whdata_0'])
    entities = list(mission.iter('Entity'))
    actor = unique((e for e in entities if e.get('Name') == template_name), 'clone template')
    if any(e.get('Name') == namespace for e in entities):
        raise ValueError('Clone already exists: ' + namespace)
    by_id = {e.get('EntityId'): e for e in entities}
    owned = [actor]
    for link in actor.findall('EntityLinks/Link'):
        if link.get('Name') == '_!home':
            home = by_id.get(link.get('TargetId'))
            if home is None: raise ValueError('Clone template home is unresolved')
            if home not in owned: owned.append(home)
    next_id = max(int(e.get('EntityId', '0')) for e in entities) + 1
    replacements = {}
    for i, entity in enumerate(owned):
        name = namespace if i == 0 else namespace + '_home'
        new_guid = native_guid(int.from_bytes(uuid.UUID(identity(name)).bytes[:8], 'little'))
        if any(e.get('EntityGuid') == new_guid or e.get('Name') == name for e in entities):
            raise ValueError('Clone entity identity collision: ' + name)
        replacements[entity.get('EntityGuid')] = new_guid
        replacements[entity.get('EntityId')] = str(next_id + i)
        replacements[entity.get('Name')] = name
        replacements[str(guid_value(entity.get('EntityGuid')))] = str(guid_value(new_guid))
    def remap(node):
        for e in node.iter():
            for key, value in list(e.attrib.items()):
                if key in ('Name', 'EntityGuid', 'EntityId', 'TargetGuid', 'TargetId',
                           'SourceGuid', 'SourceId') and value in replacements:
                    e.set(key, replacements[value])
            if e.tag in ('EntityGuid', 'Name') and e.text in replacements:
                e.text = replacements[e.text]
        return node
    origin = tuple(map(float, actor.get('Pos').split(',')))
    for entity in owned:
        clone = remap(copy.deepcopy(entity))
        old_pos = tuple(map(float, entity.get('Pos').split(',')))
        clone.set('Pos', ','.join(str(position[i] + old_pos[i] - origin[i]) for i in range(3)))
        mission.append(clone)
    souls = wh.find('./SoulList/Souls')
    source = unique((s for s in souls if s.findtext('Name') == template_name), 'template world soul')
    soul = remap(copy.deepcopy(source))
    for field in ('Guid', 'SharedSoulGuid'):
        soul.find(field).text = identity(namespace)
    if any(s.findtext('Guid') == identity(namespace) for s in souls):
        raise ValueError('Clone soul identity collision')
    souls.append(soul)
    result['objects_mission0.xml'] = xml(mission)
    result['whdata_0'] = xml(wh)
    copied = {}
    owned_guids = {e.get('EntityGuid') for e in owned}
    owned_numbers = {str(guid_value(g)) for g in owned_guids}
    for path, owner_attribute in [('tables/ai/scheduler.xml', 'EntityGuid'),
                                  ('waitinglinks.xml', 'SourceId')]:
        count = 0
        if path in result:
            root = ET.fromstring(result[path])
            for parent in list(root.iter()):
                for row in list(parent):
                    if row.get(owner_attribute) in owned_guids | owned_numbers:
                        parent.append(remap(copy.deepcopy(row))); count += 1
            result[path] = xml(root)
        copied[path] = count
    return result, dict(template=template_name, name=namespace, soul_id=identity(namespace),
        entity_guid=replacements[actor.get('EntityGuid')], position=list(position),
        owned_entities=len(owned), copied_records=copied, runtime_verified=False)


def clone_resources(files, source, person, character, namespace, *, template_name,
                    template_role, template_dialogue, target_graph):
    """Clone registered RPG/Storm/dialogue records without changing the template."""
    result = dict(files)
    if any(namespace in p for p in files):
        raise ValueError('Clone namespace already registered: ' + namespace)
    role_name = namespace.upper()
    for kind, key, old, new in [('soul', 'soul_name', template_name, namespace),
                               ('role', 'role_name', template_role, role_name)]:
        candidates = []
        for path, blob in files.items():
            if path.startswith('Libs/Tables/rpg/' + kind + '__'):
                document = ET.fromstring(blob)
                for row in document.iter(kind):
                    if row.get(key) == old: candidates.append((document, row))
        document, row = unique(candidates, 'registered template ' + kind)
        root = ET.Element(document.tag, document.attrib)
        parent = unique((e for e in document if row in list(e)), 'template row container')
        clone = copy.deepcopy(row); clone.set(key, new)
        if kind == 'soul':
            clone.set('soul_id', identity(namespace))
            clone.set('soul_archetype_id', character['archetype'])
        ET.SubElement(root, parent.tag, parent.attrib).append(clone)
        result['Libs/Tables/rpg/' + kind + '__' + namespace + '.xml'] = xml(root)
    storm = ET.fromstring(files['Libs/Storm/storm.xml'])
    operations = {
        'roles': [('addRole', dict(name=role_name))],
        'names': [('setUiName', dict(name=namespace + '_name'))],
        'appearance': [('set' + k.title(), dict(name=v)) for k, v in character['appearance'].items()],
        'equipment': [('setInventory', dict(preset=character['inventory']))],
    }
    for task, steps in operations.items():
        filename = task + '/' + namespace + '.xml'
        definition = storm.find(f"tasks/task[@name='{task}']")
        if definition is None: raise ValueError('Missing Storm task: ' + task)
        ET.SubElement(definition, 'source', path=filename)
        root = ET.Element('storm')
        rule = ET.SubElement(ET.SubElement(root, 'rules'), 'rule', name=namespace + '_' + task)
        ET.SubElement(ET.SubElement(rule, 'selectors'), 'hasName', name=namespace)
        ops = ET.SubElement(rule, 'operations')
        for tag, attrs in steps: ET.SubElement(ops, tag, attrs)
        result['Libs/Storm/' + filename] = xml(root)
    result['Libs/Storm/storm.xml'] = xml(storm)
    dialog = ET.fromstring(files[template_dialogue])
    host = dialog.find('./Skald/FaderDialog')
    old_name = host.get('Name'); host.set('Name', namespace)
    for node in dialog.iter():
        for key, value in list(node.attrib.items()):
            if value == template_name: node.set(key, namespace)
            if value == template_role: node.set(key, role_name)
    graph = ET.fromstring(files[target_graph])
    project = graph.find('./Skald/Project')
    ET.SubElement(project.find('Definitions'), 'Definition', File=namespace + '.xml')
    nodes = project.find('Nodes')
    node = unique((e for e in nodes if e.get('Name') == old_name), 'template dialogue node')
    clone = copy.deepcopy(node); clone.tag = namespace; clone.set('Name', namespace); nodes.append(clone)
    # Fan the clone's output ports into the same native travel action/waiter.
    for parent in list(graph.iter()):
        for edge in list(parent):
            if edge.tag == 'Edge' and edge.get('From', '').startswith(old_name + '.'):
                clone = copy.deepcopy(edge)
                clone.set('From', namespace + edge.get('From')[len(old_name):]); parent.append(clone)
    result[target_graph] = xml(graph)
    result[template_dialogue.rsplit('/', 1)[0] + '/' + namespace + '.xml'] = xml(dialog)
    with zipfile.ZipFile(source / 'Localization/English_xml.pak') as archive:
        names = ET.fromstring(read(archive, 'text_ui_soul.xml'))
    key = person['instance'].findtext('StaticData/NameStringId')
    row = copy.deepcopy(unique((r for r in names.findall('Row') if r.findtext('Cell') == key), 'source name'))
    row.find('Cell').text = namespace + '_name'
    names = ET.Element('Table'); names.append(row)
    result['Libs/GlueMapper/PersonImport/' + namespace + '.json'] = json.dumps(dict(
        source_person=person['name'], template=template_name, soul_id=identity(namespace),
        behavior='Native template retained; source appearance and clothing only',
        dialogue='Independent clone of template travel dialogue', runtime_verified=False), indent=2).encode()
    return result, xml(names)
