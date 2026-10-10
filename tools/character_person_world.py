"""Register source homes and a native interruptible baseline for imported people."""
import copy
import hashlib
import math
import struct
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import append_links, guid_value, native_guid
from campaign_trigger_areas import TriggerArea, read_areas, write_areas
from character_person_activity import validate_scheduler_targets
from upgrade_map import read, xml


def same_area_geometry(left, right):
    """Compare at the compiled format's float32 precision, including height."""
    def encoded(area):
        values = [area.height, *(v for point in area.points for v in point)]
        return struct.pack('<' + 'f' * len(values), *values)
    return len(left.points) == len(right.points) and encoded(left) == encoded(right)


def source_home_geometry(entity):
    """SmartAreaShape stores local vertices in XML, unlike triggerareas.fubar."""
    area = entity.find('Area')
    if area is None: raise ValueError('Source home has neither compiled nor XML polygon')
    origin = tuple(map(float, entity.get('Pos', '0,0,0').split(',')))
    scale = tuple(map(float, entity.get('Scale', '1,1,1').split(',')))
    q = tuple(map(float, entity.get('Rotate', '1,0,0,0').split(',')))
    if len(origin) != 3 or len(scale) != 3 or len(q) != 4 or not all(math.isfinite(v) for v in (*origin, *scale, *q)):
        raise ValueError('Invalid source home transform')
    if abs(sum(v*v for v in q)-1) > .001 or abs(q[1])+abs(q[2]) > .0001 or min(scale) <= 0:
        raise ValueError('Tilted or invalid source home requires explicit volume conversion')
    w, _, _, z = q
    vertices = []
    for point in area.findall('Points/Point'):
        x, y, h = (float(v)*s for v,s in zip(point.get('Pos').split(','),scale))
        vertices.append((origin[0]+(1-2*z*z)*x-2*w*z*y,
                         origin[1]+2*w*z*x+(1-2*z*z)*y, origin[2]+h))
    return TriggerArea(int(entity.get('EntityGuid'),16), float(area.get('Height'))*scale[2], tuple(vertices))


def native_wait_activity(target):
    with zipfile.ZipFile(Path(target) / 'Data/Levels/trosecko/level.pak') as z:
        entities = ET.fromstring(read(z, 'objects_mission0.xml'))
        land = next(e for e in entities if e.get('Name') == 'sa_land')
        rows = ET.fromstring(read(z, 'tables/ai/scheduler.xml')).find('Schedulers')
    guid = str(guid_value(land.get('EntityGuid')))
    for row in rows:
        for link in row.findall('Links/S_ActivityLink'):
            p = link.find('Parameters')
            if (link.get('TargetGuid') == guid and link.get('PositioningDelegate') == '0'
                    and p is not None and p.get('BehaviorName') == 'schedulerWait'
                    and p.get('Priority') == '0' and p.get('TimeOfDayStart') == '0'
                    and p.get('TimeOfDayEnd') == '1440'
                    and not any(k in p.attrib for k in ('RequiredRole', 'LinkTag', 'ProvidedRole', 'RequiredContext'))):
                terminal = next(r for r in rows if r.get('EntityGuid') == guid)
                if len(terminal): raise ValueError('Native land scheduler has new dependencies')
                return copy.deepcopy(link), copy.deepcopy(terminal)
    raise ValueError('Missing unrestricted native schedulerWait contract')


def register_person_world(files, people, source, target):
    """Merge compiled homes and fallback scheduling; preserve existing shop jobs.

    schedulerWait is a native idle baseline, NOT a translated work/sleep routine.
    The receipt keeps all original daycycle entries pending until their activity
    adapters exist. No timers, forced animations, or per-frame retry scripts.
    """
    files = dict(files)
    mission = ET.fromstring(files['objects_mission0.xml'])
    scheduler = ET.fromstring(files['tables/ai/scheduler.xml']);rows = scheduler.find('Schedulers')
    shapes = read_areas(files['triggerareas.fubar'])
    areas = {a.guid: a for a in shapes['areas']}
    land = next(e for e in mission if e.get('Name') == 'sa_land')
    waiting, reports, source_shapes = [], [], {}
    activity, terminal = native_wait_activity(target)
    land_guid = str(guid_value(land.get('EntityGuid')))
    if not any(r.get('EntityGuid') == land_guid for r in rows):
        terminal.set('EntityGuid', land_guid);rows.append(terminal)
    next_id = max(int(e.get('EntityId', '0')) for e in mission) + 1

    def create(name, cls, pos, guid):
        nonlocal next_id
        if any(e.get('Name') == name or e.get('EntityGuid') == guid for e in mission):
            raise ValueError('Person world identity collision: ' + name)
        e = ET.SubElement(mission, 'Entity', Name=name, EntityClass=cls, Pos=pos,
                          EntityId=str(next_id), EntityGuid=guid)
        next_id += 1
        return e

    def connect(a, b, tag):
        container = a.find('EntityLinks')
        if container is None: container = ET.SubElement(a, 'EntityLinks')
        ET.SubElement(container, 'Link', Name=tag, TargetId=b.get('EntityId'), TargetGuid=b.get('EntityGuid'))
        waiting.append((a.get('EntityGuid'), b.get('EntityGuid'), tag))

    for person in people:
        npc = next(e for e in mission if e.get('Name') == person['name'])
        guid = str(guid_value(npc.get('EntityGuid')))
        row = next((r for r in rows if r.get('EntityGuid') == guid), None)
        if row is None: row = ET.SubElement(rows, 'C_SmartHub', EntityGuid=guid, IgnoreDeadEnds='false', StupidHub='false')
        baseline = not row.findall('Links/S_ActivityLink')
        if baseline:
            link = copy.deepcopy(activity);link.set('TargetGuid', land_guid)
            # The scheduler positions an actor before running schedulerWait.
            # A bare land target sends it to the land origin, below the map.
            # Native TagPoint delegates provide the position independently of
            # the smart area that owns the behavior.
            anchor_guid = native_guid(int.from_bytes(hashlib.sha256(('person-idle/' + guid).encode()).digest()[:8], 'little'))
            anchor = create('gm_person_idle_' + person['name'], 'TagPoint', npc.get('Pos'), anchor_guid)
            if npc.get('Rotate'): anchor.set('Rotate', npc.get('Rotate'))
            ET.SubElement(anchor, 'Properties', bSaved_by_game='0')
            connect(anchor, land, '^delegate')
            link.set('PositioningDelegate', str(guid_value(anchor_guid)))
            container = row.find('Links')
            if container is None: container = ET.SubElement(row, 'Links')
            container.append(link);connect(npc, anchor, '_,schedulerWait')
        homes = [l for l in person['ai']['links'] if l['label'].split(',')[0] == 'Home']
        # One source area may be linked for both ownership and work. Those are
        # separate roles on the same polygon, not two competing homes.
        homes = list({ET.fromstring(l['source_xml']).get('EntityGuid'): l for l in homes}.values())
        if len(homes) > 1: raise ValueError('Ambiguous source home for ' + person['name'])
        home_report = None
        if homes:
            if any(l.get('Name') == 'home' for l in npc.findall('EntityLinks/Link')):
                raise ValueError('Person already has a native home binding')
            src = ET.fromstring(homes[0]['source_xml']);area_guid = int(src.get('EntityGuid'), 16)
            level = person['source_level']
            if level not in source_shapes:
                with zipfile.ZipFile(Path(source) / 'Data/Levels' / level / 'level.pak') as z:
                    source_shapes[level] = {a.guid: a for a in read_areas(read(z, 'triggerareas.fubar'))['areas']}
            area = next((e for e in mission if e.get('EntityGuid') == native_guid(area_guid)), None)
            if area is None:
                # Source home names (notably "banditCamp") repeat in different
                # settlements. Persistent identity, not that display name,
                # identifies an ownership polygon in the destination world.
                area = create('gm_person_home_area_' + format(area_guid, '016x'),
                              'TriggerArea', src.get('Pos'), native_guid(area_guid))
                if src.get('Rotate'): area.set('Rotate', src.get('Rotate'))
                # Ownership geometry is separate from crime/trespass zoning.
                ET.SubElement(area, 'Properties', bSaved_by_game='0')
            elif area.get('EntityClass') not in ('TriggerArea', 'SmartAreaShape'):
                raise ValueError('Source home identity has an incompatible destination entity')
            geometry = source_shapes[level].get(area_guid)
            if geometry is None: geometry = source_home_geometry(src)
            if area_guid in areas and not same_area_geometry(areas[area_guid], geometry):
                raise ValueError('Existing home polygon differs from source')
            areas[area_guid] = geometry
            hub_guid = native_guid(int.from_bytes(hashlib.sha256(('person-home/' + guid).encode()).digest()[:8], 'little'))
            hub = create('gm_person_home_' + person['name'], 'SchedulerHub', src.get('Pos'), hub_guid)
            ET.SubElement(rows, 'C_SmartHub', EntityGuid=str(guid_value(hub_guid)), IgnoreDeadEnds='false', StupidHub='false')
            connect(npc, hub, 'home');connect(hub, area, 'home_area')
            home_report = dict(source=src.get('Name'), guid=area.get('EntityGuid'), vertices=len(geometry.points))
        reports.append(dict(person=person['name'], home=home_report,
                            baseline='native schedulerWait' if baseline else 'existing authored activity preserved',
                            original_schedule=person['ai']['schedule'], runtime_verified=False))
    validate_scheduler_targets(scheduler)
    files.update({'objects_mission0.xml': xml(mission), 'tables/ai/scheduler.xml': xml(scheduler),
                  'waitinglinks.xml': append_links(files.get('waitinglinks.xml'), waiting),
                  'triggerareas.fubar': write_areas(areas.values(), shapes['export_version'])})
    return files, reports
