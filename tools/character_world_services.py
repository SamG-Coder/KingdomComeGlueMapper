"""Register destination-world AI services using native contracts and source places.

The runtime graph consumes waitinglinks.xml, not just editor EntityLinks. This
module writes both and accounts for every native land/interrupt-host dependency.
Quest-owned crime services remain explicit dependencies until their quest actors
and cutscenes are converted; an empty SmartObjectHolder is not such a service.
"""
import copy
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import append_links
from upgrade_map import read, xml


QUEST_SERVICES = {
    'punishment_executionCH': 'Requires a converted execution scene and its actors.',
    'punishment_fallbackArea': 'Requires a punishment area, guard, and punishment scenes.',
    'punishment_openworldQSO': 'Requires the open-world concept module and its linked assets.',
    'punishment_fader': 'Requires the crime_fader cutscene registration.',
}
ROOT_SERVICES = {'mrkev', 'redkev', 'levelExit', 'hangoverSpotsHub',
                 'playerLinkRerouter', 'flutistController', 'npcEmergencyPoint', *QUEST_SERVICES}


def level_entities(archive):
    result = []
    for name in archive.namelist():
        normalized = name.replace('\\', '/').lower()
        if normalized == 'objects_mission0.xml' or (normalized.startswith('layers/') and normalized.endswith('.xml')):
            result.extend(ET.fromstring(read(archive, name)).iter('Entity'))
    return result


def register_services(files, source, native, identity, helper_data):
    """Import map-independent controllers and source-authored spatial services.

    Resident KCD1 horse-parking links identify parking points. Native type and
    animation helpers supply KCD2's contract. Conditional quest camps are not
    made permanently active. Town shelter anchors supply emergency destinations
    (an explicit fallback mapping, not a claim of identical KCD1 semantics).
    """
    files = dict(files)
    mission = ET.fromstring(files['objects_mission0.xml'])
    land = next(e for e in mission if e.get('Name') == 'sa_land')
    src_land = next(e for e in source if e.get('Name', '').lower() == 'sa_land')
    native_land = next(e for e in native if e.get('Name') == 'sa_land')
    src_index = {e.get('EntityId'): e for e in source}
    native_index = {e.get('EntityId'): e for e in native}
    ids = {e.get('EntityId', '0') for e in mission.iter('Entity')}
    guids = {e.get('EntityGuid') for e in mission.iter('Entity')}
    next_id = max(map(int, ids)) + 1
    created, waiting, registered, deferred = {}, [], [], []

    def resolve(index, link):
        target = index.get(link.get('TargetId'))
        if target is None:
            raise ValueError('Unresolved source world service: ' + str(link.attrib))
        return target

    def child(parent, label, index):
        links = [l for l in parent.findall('EntityLinks/Link') if l.get('Name') == label]
        if not links:
            raise ValueError('Missing native world contract: ' + label)
        return resolve(index, links[0])

    def create(key, template, position, cls=None, props=None):
        nonlocal next_id
        if key in created:
            return created[key]
        guid = identity('world-services/' + key)
        if guid in guids:
            raise ValueError('World-service GUID collision: ' + key)
        xyz = tuple(map(float, position.split(',')))
        if len(xyz) != 3 or not all(math.isfinite(v) for v in xyz):
            raise ValueError('Invalid world-service position: ' + key)
        name = 'gmworld_' + key.replace('/', '_')
        if any(e.get('Name') == name for e in mission):
            raise ValueError('World service already registered: ' + key)
        entity = ET.SubElement(mission, 'Entity', Name=name, EntityId=str(next_id),
                               EntityGuid=guid, EntityClass=cls or template.get('EntityClass'), Pos=position)
        next_id += 1
        guids.add(guid)
        if template.get('Rotate'):
            entity.set('Rotate', template.get('Rotate'))
        properties = props if props is not None else template.find('Properties')
        if properties is not None:
            entity.append(copy.deepcopy(properties))
        created[key] = entity
        return entity

    def connect(a, b, label, provenance):
        links = a.find('EntityLinks')
        if links is None:
            links = ET.SubElement(a, 'EntityLinks')
        if any(l.get('Name') == label and l.get('TargetGuid') == b.get('EntityGuid') for l in links):
            raise ValueError('Duplicate world-service edge: ' + label)
        ET.SubElement(links, 'Link', Name=label, TargetId=b.get('EntityId'), TargetGuid=b.get('EntityGuid'))
        waiting.append((a.get('EntityGuid'), b.get('EntityGuid'), label))
        registered.append(dict(label=label, source=a.get('Name'), target=b.get('Name'),
                               guid=b.get('EntityGuid'), position=b.get('Pos'), provenance=provenance))

    native_labels = {l.get('Name') for l in native_land.findall('EntityLinks/Link')}
    if native_labels - ROOT_SERVICES:
        raise ValueError('Unmapped native world services: ' + repr(sorted(native_labels - ROOT_SERVICES)))
    # Verify the existing interrupt hosts instead of replacing them and losing
    # their runtime identities on every new build.
    hosts = {}
    destination_index = {e.get('EntityId'): e for e in mission}
    for label in ('mrkev', 'redkev'):
        hosts[label] = child(land, label, destination_index)
        template = child(native_land, label, native_index)
        if hosts[label].find('Properties').get('guidSmartObjectType') != template.find('Properties').get('guidSmartObjectType'):
            raise ValueError('Interrupt host type mismatch: ' + label)

    for label in ('flutistController', 'playerLinkRerouter'):
        template = child(native_land, label, native_index)
        entity = create(label, template, land.get('Pos'))
        connect(land, entity, label, 'native nonspatial service')
        for link in template.findall('EntityLinks/Link'):
            # The native router's ambush horse override is a quest-owned edge,
            # not the player's owned horse. Never redirect ownership to it.
            if label != 'playerLinkRerouter' or not link.get('Name', '').startswith('useHorse['):
                raise ValueError('New native controller dependency: ' + str(link.attrib))
            deferred.append(dict(service=label, label=link.get('Name'), reason='Native quest-owned horse override is not active in the imported region.'))

    for link in src_land.findall('EntityLinks/Link'):
        target = resolve(src_index, link)
        props = target.find('Properties')
        if link.get('Name') == 'levelExit' or (props is not None and props.get('sWH_AI_EntityCategory') == 'levelExit'):
            if target.get('EntityClass') != 'TagPoint' or target.get('Layer'):
                raise ValueError('Unsupported source level-exit contract')
            point = create('levelExit/' + target.get('EntityGuid'), target, target.get('Pos'))
            connect(land, point, 'levelExit', target.get('Name'))
        elif link.get('Name') == 'hangoverSpotsHub':
            hub = create('hangoverSpotsHub', target, target.get('Pos'))
            connect(land, hub, 'hangoverSpotsHub', target.get('Name'))
            native_hub = child(native_land, 'hangoverSpotsHub', native_index)
            allowed = {l.get('Name') for l in native_hub.findall('EntityLinks/Link')}
            for leaf_link in target.findall('EntityLinks/Link'):
                leaf = resolve(src_index, leaf_link)
                if leaf_link.get('Name') not in allowed or leaf.get('EntityClass') != 'TagPoint' or leaf.get('Layer') or leaf.findall('EntityLinks/Link'):
                    raise ValueError('Unsupported hangover dependency: ' + str(leaf.attrib))
                point = create('hangover/' + leaf.get('EntityGuid'), leaf, leaf.get('Pos'))
                connect(hub, point, leaf_link.get('Name'), leaf.get('Name'))
        elif link.get('Name') == 'rainshelterMainPoint':
            if target.get('EntityClass') != 'TagPoint' or target.get('Layer'):
                raise ValueError('Unsupported source town refuge')
            template = child(native_land, 'npcEmergencyPoint', native_index)
            point = create('emergency/' + target.get('EntityGuid'), target, target.get('Pos'),
                           cls='TagPoint', props=template.find('Properties'))
            connect(land, point, 'npcEmergencyPoint', 'source town shelter anchor: ' + target.get('Name'))

    human_template = child(native_land, 'mrkev', native_index)
    parking_template = child(human_template, 'horseParkingSpot', native_index)
    parking_props = parking_template.find('Properties')
    if parking_props is None or not parking_props.get('guidSmartObjectType') or not parking_props.get('soclass_SmartObjectHelpers'):
        raise ValueError('Incomplete native horse-parking contract')
    parking = {}
    for owner in source:
        for link in owner.findall('EntityLinks/Link'):
            if link.get('Name') != 'horseParkingSpot':
                continue
            point = resolve(src_index, link)
            if owner.get('Layer') or point.get('Layer'):
                deferred.append(dict(service='horseParkingSpot', source=point.get('Name'),
                                     reason='Source conditional layer is not active.', layer=owner.get('Layer') or point.get('Layer')))
                continue
            if point.get('EntityClass') != 'TagPoint' or point.findall('EntityLinks/Link'):
                raise ValueError('Unsupported source horse-parking point: ' + str(point.attrib))
            parking[point.get('EntityGuid')] = point
    for key, point in sorted(parking.items()):
        entity = create('horseParking/' + key, point, point.get('Pos'), cls='SmartObjectHolder', props=parking_props)
        connect(hosts['mrkev'], entity, 'horseParkingSpot', point.get('Name'))
    if not parking:
        raise ValueError('No resident source horse-parking services')
    # KCD2 use() invokes an animal_horseDrink UnstanceAction; without its exported
    # helper animation collection a typed holder alone is incomplete.
    helpers = ET.fromstring(files.get('smartobjecthelpersetanimations.xml', b'<SmartObjectAnimationCollections/>'))
    native_helpers = ET.fromstring(helper_data)
    helper_name = parking_props.get('soclass_SmartObjectHelpers')
    collections = [c for c in native_helpers if c.get('HelperSetName') == helper_name]
    if len(collections) != 1:
        raise ValueError('Missing native horse-parking helper animations: ' + helper_name)
    for old in list(helpers):
        if old.get('HelperSetName') == helper_name:
            helpers.remove(old)
    helpers.append(copy.deepcopy(collections[0]))
    files['smartobjecthelpersetanimations.xml'] = xml(helpers)

    for link in human_template.findall('EntityLinks/Link'):
        if link.get('Name') not in {'debugger', 'horseParkingSpot', 'extraGuards_proxy'}:
            raise ValueError('Unmapped native human-host dependency: ' + str(link.attrib))
        if link.get('Name') == 'extraGuards_proxy':
            deferred.append(dict(service='mrkev', label='extraGuards_proxy',
                                 reason='Requires converted guard bundles and settlement concept modules.'))
    for label in sorted(native_labels & QUEST_SERVICES.keys()):
        deferred.append(dict(service=label, reason=QUEST_SERVICES[label]))

    files['objects_mission0.xml'] = xml(mission)
    files['waitinglinks.xml'] = append_links(files.get('waitinglinks.xml'), waiting)
    counts = {label: sum(r['label'] == label for r in registered) for label in sorted({r['label'] for r in registered})}
    required = {'levelExit', 'hangoverSpotsHub', 'npcEmergencyPoint', 'horseParkingSpot'}
    if not required <= counts.keys():
        raise ValueError('Source world is missing required service destinations: ' + repr(required - counts.keys()))
    return files, dict(schema=1, registered=registered, counts=counts, added_entities=len(created),
                       preserved_services=['mrkev', 'redkev', 'playerProxy', 'so_player_scheduler'],
                       deferred=deferred, all_native_services_registered=False, runtime_verified=False)


def register_world_services(files, source, target, identity):
    with zipfile.ZipFile(Path(source) / 'Data/Levels/rataje/level.pak') as src, \
         zipfile.ZipFile(Path(target) / 'Data/Levels/trosecko/level.pak') as native:
        return register_services(files, level_entities(src), level_entities(native), identity,
                                 read(native, 'smartobjecthelpersetanimations.xml'))
