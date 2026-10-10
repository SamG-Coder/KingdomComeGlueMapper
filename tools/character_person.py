"""Shared source-person identity, native soul, appearance and world registration.

Quest and shop adapters select a person; this helper does not select a quest,
merchant, gender, body or coordinates on their behalf.
"""
import copy
import hashlib
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile

from campaign_dependency_adapters import SourceTables
from campaign_sources import RetailSourceReader
from campaign_entity_links import native_guid
from upgrade_map import read, xml
from character_person_ai import (capture_person_ai, native_brain_and_class,
                               stat_operations, apply_person_properties, receipt, register_factions,
                               register_guard_selector)
from character_world_services import level_entities


def native_xml(blob):
    return ET.fromstring(re.sub(rb'<!--.*?-->', b'', blob, flags=re.S))


def unique(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f'Expected one {label}, found {len(values)}')
    return values[0]


def effective_soul(definition, instance):
    """Use authored instance overrides, including souls with no shared DB row."""
    soul = dict(definition or {})
    if not definition:
        soul.update(soul_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/inline-soul/' + instance.findtext('Guid'))),
                    soul_name=instance.findtext('Name'))
    static = instance.find('StaticData')
    if static is None: return soul
    fields = {'faction':'FactionId', 'social_class_id':'SocialClassId', 'brain_id':'InitialAIData/BrainId',
              'inventory_id':'InventoryId', 'initial_weapon_preset_id':'InitialWeaponPresetId',
              'initial_clothing_preset_id':'InitialClothingDescription/PresetId',
              'initial_clothing_dirt':'InitialClothingDescription/DirtLevel', 'xp_multiplier':'XPMultiplier',
              'digestion_multiplier':'DigestionMultiplier', 'shadiness':'Shadiness', 'charisma':'Charisma'}
    fields.update({old:'InitialSoulStats/'+new for old,new in dict(str='Strength',agi='Agility',vit='Vitality',
        spc='Speech',vision='Vision',hearing='Hearing',barter='Barter',courage='Courage',reputation='PlayerReputation').items()})
    for key, path in fields.items():
        value = static.findtext(path)
        if value not in (None, ''): soul[key] = value
    body = static.find('CharacterBodyDescription')
    if body is not None:
        for value in body:
            key = 'character_' + re.sub(r'(?<!^)(?=[A-Z])', '_', value.tag).lower()
            if value.text is not None: soul[key] = value.text
    activities = static.find('InitialAIData/Activities')
    if activities is not None:
        for key in list(soul):
            if re.fullmatch(r'(?:activity|time)_\d+', key): del soul[key]
        for i, activity in enumerate(activities):
            soul['activity_' + str(i)] = activity.findtext('Activity')
            soul['time_' + str(i)] = activity.findtext('Time')
    return soul


class PersonSource:
    """One shared source reader for an entire population, including layer actors.

    Resolve instances by persistent entity GUID. Names can repeat across quest
    layers and shared souls can have different instance appearance/AI overrides.
    """
    def __init__(self, source, level='rataje'):
        self.source, self.level = Path(source), level

    def __enter__(self):
        with zipfile.ZipFile(self.source / 'Data/Levels' / self.level / 'level.pak') as archive:
            self.entities = level_entities(archive)
            self.instances = ET.fromstring(read(archive, 'whdata_0')).findall('./SoulList/Souls/Soul')
            self.mission_hash = hashlib.sha256(read(archive, 'objects_mission0.xml')).hexdigest()
        self.reader = RetailSourceReader(self.source).__enter__()
        self.tables = SourceTables(self.source, self.reader)
        self.by_id = {e.get('EntityId'): e for e in self.entities}
        self.actors = [e for e in self.entities if e.get('EntityClass') in ('NPC', 'NPC_Female')]
        return self

    def __exit__(self, *args):
        return self.reader.__exit__(*args)

    def resolve(self, name=None, *, entity_guid=None):
        matches = (e for e in self.actors if (e.get('EntityGuid').lower() == entity_guid.lower()
                   if entity_guid else e.get('Name') == name))
        actor = unique(matches, 'source person placement')
        number = int(actor.get('EntityGuid'), 16)
        instance = unique((s for s in self.instances if s.findtext('EntityGuid') == str(number)), 'source person instance')
        shared = instance.findtext('SharedSoulGuid')
        definition = self.tables.row('rpg/soul', 'soul_id', shared) if shared else None
        soul = effective_soul(definition, instance)
        ai = capture_person_ai(self.tables, soul, actor, instance, self.by_id)
        from character_person_brain import capture_brain
        capture_brain(ai, self.tables, self.reader)
        ai['source_soul_definition'] = definition
        return dict(name=actor.get('Name'), soul=soul, actor=actor, instance=instance,
                    source_level=self.level, ai=ai,
                    provenance=dict(tables={k: v['provenance'] for k, v in self.tables.cache.items()},
                                    mission_sha256=self.mission_hash, layer=actor.get('Layer')))


def resolve_person(source, name, level='rataje'):
    with PersonSource(source, level) as catalog:
        return catalog.resolve(name)


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


def actor_resources(target, person, character, namespace, *, role_name=None, native_soul_table=None, native_soul_name=None,
                    native_role_name=None, resources=None, native_cache=None):
    """Add a person to a resource collection; retain previously imported people."""
    files = dict(resources or {})
    cache = native_cache if native_cache is not None else {}
    def table(path):
        if path not in cache:
            with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
                cache[path] = native_xml(read(archive, path))
        return cache[path]
    if native_soul_table:
        native = table(native_soul_table)
        template = next(e for e in native.iter('soul') if e.get('soul_name') == native_soul_name)
    else:
        # Population records have no borrowed merchant/quest voice or VIP role.
        # These are the neutral required defaults used by shipped human souls;
        # source values and resolved native identities are applied below.
        template = ET.Element('soul', digestion_multiplier='0', initial_clothing_dirt='0',
                              soul_vip_class_id='0', xp_multiplier='0')
    roles = table('Libs/Tables/rpg/role.xml')
    role = copy.deepcopy(next(e for e in roles.iter('role') if e.get('role_name') == native_role_name)) if role_name else None
    brains = table('Libs/Tables/ai/brain.xml')
    from character_person_brain import register_brain
    brains = register_brain(person['ai'], target, files, brains, cache)
    classes = table('Libs/Tables/rpg/social_class.xml')
    faction_tree = table('Libs/Tables/rpg/FactionTree.xml')
    root = ET.Element('database', name='barbora')
    soul = ET.SubElement(ET.SubElement(root, 'souls', version='2'), 'soul', dict(template.attrib))
    soul.set('soul_id', person['soul']['soul_id']); soul.set('soul_name', person['name'])
    soul.set('soul_archetype_id', character['archetype'])
    soul.attrib.pop('skald_character_name', None)
    brain, social = native_brain_and_class(person['ai'], brains, classes)
    soul.set('brain_id', brain)
    soul.set('social_class_id', social)
    faction = register_factions(files, person['ai'], faction_tree)
    if faction: soul.set('factionName', faction)
    else: soul.attrib.pop('factionName', None)
    selector_path = 'Libs/Storm/common/base.xml'
    if selector_path not in files:
        with zipfile.ZipFile(Path(target) / 'Data/IPL_GameData.pak') as archive:
            files[selector_path] = register_guard_selector(read(archive, selector_path))
    for key in ('xp_multiplier', 'digestion_multiplier', 'initial_clothing_dirt'):
        if person['soul'].get(key) not in ('', None): soul.set(key, person['soul'][key])
    # Source VIP IDs are quest-specific; a matching integer in KCD2 can name a
    # different character. Keep the neutral template VIP until explicitly mapped.
    additions = {'Libs/Tables/rpg/soul__' + namespace + '.xml': xml(root)}
    if role is not None:
        role.set('role_name', role_name)
        role_root = ET.Element('database', name='barbora')
        ET.SubElement(role_root, 'roles', version='1').append(role)
        additions['Libs/Tables/rpg/role__' + namespace + '.xml'] = xml(role_root)
    if files.keys() & additions.keys():
        raise ValueError('Person namespace already registered: ' + namespace)
    files.update(additions)
    if 'Libs/Storm/storm.xml' in files:
        storm = ET.fromstring(files['Libs/Storm/storm.xml'])
    else:
        with zipfile.ZipFile(Path(target) / 'Data/IPL_GameData.pak') as archive:
            storm = ET.fromstring(read(archive, 'Libs/Storm/storm.xml'))
    appearance = character['appearance']
    if person['ai'].get('invisible_helper'):
        # Explicit empty components override default Storm appearance rules.
        # This preserves an invisible helper across render LOD transitions,
        # without KCD1's removed LODLock node or a per-frame hiding script.
        hidden = ET.Element('database', name='barbora')
        component = ET.SubElement(ET.SubElement(hidden, 'CharacterComponents', version='6'), 'Component',
                                 Name=namespace + '_hidden', Race='Human', Gender=character['gender'].title())
        derived = ET.SubElement(component, 'DerivedComponents')
        appearance = {}
        for kind in ('body', 'head', 'hair', 'beard', 'underwear'):
            name = namespace + '_hidden_' + kind; appearance[kind] = name
            ET.SubElement(derived, 'Clothing' if kind == 'underwear' else kind.title(), Name=name)
        files['Libs/Tables/Character/CharacterComponent__' + namespace + '_hidden.xml'] = xml(hidden)
    ui_name = person['instance'].findtext('StaticData/NameStringId')
    operations = {
        'roles': [('addRole', dict(name=role_name))] if role_name else [],
        'names': [('setUiName', dict(name=ui_name))] if ui_name else [],
        'appearance': [('set' + k.title(), dict(name=v)) for k, v in appearance.items()],
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

