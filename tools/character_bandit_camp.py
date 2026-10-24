"""Bind a native combat clone to an authored destination camp.

The source camp supplies geometry and the arrival point; the native template
supplies faction ancestry. Neither the source quest nor Trosky jobs are started.
"""
import copy
import uuid
import zipfile
import xml.etree.ElementTree as ET

from campaign_entity_links import append_links, entity_link, guid_value, native_guid
from campaign_trigger_areas import TriggerArea, read_areas, write_areas
from character_native_clone import identity
from character_person import native_xml, unique
from character_person_world import source_home_geometry
from upgrade_map import read, xml


def resolve_camp(source, name, level='rataje'):
    with zipfile.ZipFile(source/'Data/Levels'/level/'level.pak') as archive:
        mission = native_xml(read(archive, 'objects_mission0.xml'))
    area = unique((e for e in mission if e.get('Name') == name), 'source camp')
    if area.get('EntityClass') != 'SmartAreaShape':
        raise ValueError('Camp must be an authored smart area')
    inside = unique((l for l in area.findall('EntityLinks/Link') if l.get('Name') == 'inside'), 'camp inside link')
    marker = unique((e for e in mission if e.get('EntityId') == inside.get('TargetId')), 'camp inside marker')
    geometry = source_home_geometry(area)
    return dict(area=copy.deepcopy(area), geometry=geometry,
                position=tuple(map(float, marker.get('Pos').split(','))), marker=marker.get('Name'))


def faction_patch(tree, source_name, namespace, level_id, location_id):
    parents = {c: p for p in tree.iter() for c in p}
    node = unique((f for f in tree.iter('Faction') if f.get('Name') == source_name), 'bandit faction')
    chain = [node]
    while node in parents:
        node = parents[node]
        if node.tag == 'Faction': chain.append(node)
    if not any('publicEnemy' in f.get('Labels', '').split() for f in chain):
        raise ValueError('Template faction is not a native public enemy')
    names = {f.get('Name'): namespace+'_faction_'+str(i) for i, f in enumerate(reversed(chain))}
    root = ET.Element('database', name='barbora')
    container = ET.SubElement(root, 'FactionTree', version='1')
    for original in reversed(chain):
        clone = ET.SubElement(container, 'Faction', dict(original.attrib))
        clone.set('Name', names[original.get('Name')])
        if 'LevelId' in clone.attrib: clone.set('LevelId', str(level_id))
        if 'LocationId' in clone.attrib: clone.set('LocationId', location_id)
        relations = original.find('Relations')
        if relations is not None:
            relations = copy.deepcopy(relations)
            for relation in relations:
                relation.set('target', names.get(relation.get('target'), relation.get('target')))
            clone.append(relations)
        container = ET.SubElement(clone, 'Children')
    clone.remove(container)
    clone.set('LevelId', str(level_id)); clone.set('LocationId', location_id)
    # This test camp is deliberately hostile to Henry. Keep that policy in a
    # faction record, not an attack timer or a forced combat-state script.
    relations = clone.find('Relations')
    if relations is None: relations = ET.SubElement(clone, 'Relations')
    player = relations.find("Relation[@target='player']")
    if player is None: player = ET.SubElement(relations, 'Relation', target='player')
    player.set('reputation', '-1')
    return xml(root), names[source_name]


def register(files, resources, target, namespace, camp, level_id, template):
    files, resources = dict(files), dict(resources)
    location_id = identity(namespace+'/camp-location')
    with zipfile.ZipFile(target/'Data/Tables.pak') as archive:
        tree = native_xml(read(archive, 'Libs/Tables/rpg/FactionTree.xml'))
    soul_path = 'Libs/Tables/rpg/soul__'+namespace+'.xml'
    soul_root = ET.fromstring(resources[soul_path]); soul = soul_root.find('souls/soul')
    faction, faction_name = faction_patch(tree, soul.get('factionName'), namespace, level_id, location_id)
    soul.set('factionName', faction_name); resources[soul_path] = xml(soul_root)
    resources['Libs/Tables/rpg/FactionTree__'+namespace+'.xml'] = faction
    root = ET.Element('database', name='barbora')
    ET.SubElement(ET.SubElement(root, 'locations', version='1'), 'location',
                  level_id=str(level_id), location_category_id='1', location_id=location_id,
                  location_name=namespace+'_camp', ui_type='Hidden')
    resources['Libs/Tables/rpg/location__'+namespace+'.xml'] = xml(root)
    mission = ET.fromstring(files['objects_mission0.xml'])
    wh = ET.fromstring(files['whdata_0'])
    scheduler = ET.fromstring(files['tables/ai/scheduler.xml'])
    rows = scheduler.find('Schedulers')
    npc = unique((e for e in mission if e.get('Name') == namespace), 'camp actor')
    waiting = []

    def entity(suffix, cls, pos):
        name = namespace+suffix
        guid = native_guid(int.from_bytes(uuid.UUID(identity(name)).bytes[:8], 'little'))
        if any(e.get('Name') == name or e.get('EntityGuid') == guid for e in mission):
            raise ValueError('Duplicate camp entity '+name)
        return ET.SubElement(mission, 'Entity', Name=name, EntityClass=cls,
            EntityId=str(max(int(e.get('EntityId', '0')) for e in mission)+1), EntityGuid=guid, Pos=pos)

    area = entity('_camp_area', 'SmartAreaShape', camp['area'].get('Pos'))
    for key in ('Rotate', 'Scale'):
        if camp['area'].get(key): area.set(key, camp['area'].get(key))
    area.append(copy.deepcopy(camp['area'].find('Area')))
    props = ET.SubElement(area, 'Properties', guidLocationId=location_id, bSaved_by_game='0')
    ET.SubElement(props, 'Script', bValidWithoutTemplate='1')
    number = guid_value(area.get('EntityGuid'))
    ET.SubElement(ET.SubElement(wh.find('AI/SmartAreaManager/SmartAreas'), 'SmartArea'), 'Guid').text = str(number)
    membership = ET.SubElement(wh.find('RPGLocationManager/SmartAreas'), 'SmartArea')
    ET.SubElement(membership, 'guid').text = str(number)
    ET.SubElement(membership, 'Location').text = location_id
    shapes = read_areas(files['triggerareas.fubar'])
    geometry = camp['geometry']
    shapes['areas'].append(TriggerArea(number, geometry.height, geometry.points))
    home = entity('_camp_home', 'SchedulerHub', npc.get('Pos'))
    # Match the successful generated control: home is a static ownership link,
    # while the actor retains its independent interruptible schedulerWait job.
    entity_link(npc, home, 'home', waiting); entity_link(home, area, 'home_area', waiting)
    from character_person_activity import validate_scheduler_targets
    validate_scheduler_targets(scheduler)
    files.update({'objects_mission0.xml': xml(mission), 'whdata_0': xml(wh),
                  'tables/ai/scheduler.xml': xml(scheduler),
                  'waitinglinks.xml': append_links(files.get('waitinglinks.xml'), waiting),
                  'triggerareas.fubar': write_areas(shapes['areas'], shapes['export_version'])})
    return files, resources, dict(source_area=camp['area'].get('Name'), source_marker=camp['marker'],
        location_id=location_id, level_id=level_id, faction=faction_name, home=home.get('Name'),
        hostility='publicEnemy with player reputation -1',
        activity='native interruptible schedulerWait inside authored camp',
        pending=['camp work/rest/healing activity conversion', 'destination flee/exit dependencies'], runtime_verified=False)
