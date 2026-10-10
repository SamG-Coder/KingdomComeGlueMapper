"""Place upgraded NPCs in their original streaming layers, never in the base.

This adapter owns actor-only layers. Mixed layers are owned by their world
entity converters and must arrive as a complete document before registration.
Character staging and native profile activation are separate operations.
"""
from collections import Counter
import copy
import struct
import xml.etree.ElementTree as ET

from campaign_entity_links import guid_value
from character_population_layers import register_layers
from upgrade_map import xml


def compiled_layer_dependencies(terrain, level):
    """Compiled brushes/vegetation are outside each layer's Objects XML."""
    from water_volumes import read_water
    names = {int(e.get('Id')): e.get('Name').lower() for e in level.findall('Layers/Layer')}
    counts = Counter()
    def visit(data, offset, size, kind):
        identity = struct.unpack_from('<H', data, offset + 28)[0]
        if identity:
            if identity not in names: raise ValueError('Unknown compiled source layer ID')
            counts[names[identity]] += 1
    read_water(terrain, visit)
    return dict(counts)


def plan_layers(documents, actors, compiled_dependencies=None):
    """Select complete actor layers from a population checkpoint.

    Return the exact source members too, so splitting cannot silently publish a
    partially converted layer. Empty XML layers are valid streaming members.
    """
    records = {a['source_guid'].lower(): a for a in actors}
    selected, pending = {}, []
    for path, payload in sorted(documents.items()):
        root = ET.fromstring(payload)
        if root.tag != 'Objects':
            raise ValueError('Invalid source layer container: ' + path)
        layer = path.replace('\\', '/').removeprefix('layers/').removesuffix('.xml')
        classes = Counter(e.get('EntityClass', e.tag) for e in root)
        unsupported = set(classes) - {'NPC', 'NPC_Female'}
        missing = []
        for e in root:
            if e.get('EntityClass') not in ('NPC', 'NPC_Female'): continue
            identity = e.get('EntityGuid').lower()
            record = records.get(identity)
            if record is None or record['status'] != 'conditional_converted':
                missing.append(dict(guid=identity, reason=(record or {}).get('registration_dependency')
                                    or (record or {}).get('error') or (record or {}).get('status', 'not staged')))
            elif record['resident'] or record['container'].replace('\\', '/').lower() != path.lower():
                raise ValueError('Conditional source membership disagrees with staging: ' + identity)
        compiled = (compiled_dependencies or {}).get(layer.lower(), 0)
        if unsupported or missing or compiled:
            pending.append(dict(layer=layer, entity_classes=dict(classes),
                                world_adapters=sorted(unsupported), actors=missing, compiled_objects=compiled))
        else:
            selected[layer] = [e.get('EntityGuid').lower() for e in root]
    return selected, pending


def split_layers(files, plan, people, source_level, source_profiles):
    """Move conditional actors and their private scheduler helpers as one unit.

    Shared home polygons stay in the base world; they describe ownership and
    contain no active NPC. An actor's idle delegate and home hub unload with it.
    Compiled scheduler rows and SoulList records retain persistent GUIDs.
    """
    files = dict(files)
    mission = ET.fromstring(files['objects_mission0.xml'])
    by_guid = {guid_value(e.get('EntityGuid')): e for e in mission if e.get('EntityGuid')}
    by_name = {e.get('Name'): e for e in mission}
    by_source = {p['actor'].get('EntityGuid').lower(): p for p in people}
    layers, moved = {}, set()
    for layer, members in sorted(plan.items()):
        root = ET.Element('Objects')
        for identity in members:
            person = by_source[identity]
            actor = by_guid[int(identity, 16)]
            if actor.get('Name') != person['name']:
                raise ValueError('Conditional NPC registration differs from source')
            private = [actor]
            for prefix in ('gm_person_idle_', 'gm_person_home_'):
                helper = by_name.get(prefix + person['name'])
                if helper is not None: private.append(helper)
            for entity in private:
                guid = guid_value(entity.get('EntityGuid'))
                if guid in moved: raise ValueError('Entity belongs to multiple conditional layers')
                moved.add(guid); mission.remove(entity); root.append(copy.deepcopy(entity))
        layers[layer] = xml(root)
    files['objects_mission0.xml'] = xml(mission)
    result, receipt = register_layers(files, source_level, source_profiles, layers)
    receipt.update(conditional_actors=sum(map(len, plan.values())),
                   private_entities=len(moved), behaviors='native interruptible baseline; source activities pending',
                   controllers_converted=False)
    return result, receipt
