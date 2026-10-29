"""Resolve authored household beds and bounded unassigned public rest places."""
import math
import xml.etree.ElementTree as E

from chairs import inventory
from entity_visuals import world_transform
from character_person_world import source_home_geometry
from character_station_work import unquote

QUALITY = {'low': 0, 'medium': 1, 'mediumPlus': 2, 'high': 3, 'exceptional': 4}
PUBLIC_RANGE = 300


def tags(label):
    return {v.strip() for v in (label or '').split(',')}


def inside(position, area):
    points = area.points
    if not points or not min(p[2] for p in points)-1 <= position[2] <= max(p[2] for p in points)+area.height+1:
        return False
    x, y = position[:2]; odd = False
    for a, b in zip(points, (*points[1:], points[0])):
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            odd = not odd
    return odd


def source_contract(program):
    """Refuse changed selection rules instead of silently reinterpreting homes."""
    root = program.find("BehaviorTree[@name='Sleeper']/Root")
    reserve = None if root is None else root.find('.//ReserveBehavior')
    if reserve is None or unquote(reserve.get('reserveFrom', '')) != 'bed' or unquote(reserve.get('behaviorName', '')) != 'use':
        raise ValueError('Source sleep reservation changed')
    queries = reserve.findall('.//GraphSearch')
    if len(queries) != 3 or any(unquote(q.get('depth', '')) != '1' or
            unquote(q.get('EdgePruning', '')) != 'parent,inventory' for q in queries):
        raise ValueError('Source sleep home/bed query changed')
    sorter = reserve.find('.//SleepQualitySorter')
    call = reserve.find('.//CallBehavior')
    if (queries[0].find("LinkTagFilter[@tag='\"Home\"']") is None
            or len(reserve.findall(".//LinkTagFilter[@tag='\"preferedSleeper\"']")) != 2
            or sorter is None or sorter.get('SortOrder') != '"Descending"'
            or call is None or call.get('BehaviorName') != '"use"'):
        raise ValueError('Source sleep preference contract changed')


def catalog(archive, tables, *, notify=lambda message: None):
    entities, beds, _ = inventory(archive, 'Bed', notify=notify)
    notify('Checking source bed ownership and home boundaries')
    incoming = {}
    for e in entities.values():
        for link in e.findall('EntityLinks/Link'):
            incoming.setdefault(link.get('TargetId'), []).append((e, link))
    behavior_ids = {r['sa_behaviour_tag_id'] for r in tables.get('ai/sa_behaviour_tag')['rows']
                   if r['tree_file'].replace('\\', '/').lower() == 'final/sa_home.xml' and r['tree_name'] == 'Sleeper'}
    home_templates = {r['sa_smart_area_id'] for r in tables.get('ai/sa_smart_area2sa_behaviour_tag')['rows']
                      if r['sa_behaviour_tag_id'] in behavior_ids}
    homes = [e for e in entities.values() if e.find('Properties') is not None and
             e.find('Properties').get('guidSmartAreaTemplate') in home_templates]
    # Private home footprints exclude unlinked beds inside houses too.
    from campaign_trigger_areas import read_areas
    from upgrade_map import read
    geometry = {a.guid: a for a in read_areas(read(archive, 'triggerareas.fubar'))['areas']}
    areas = []
    for home in homes:
        area = geometry.get(int(home.get('EntityGuid'), 16))
        if area is None and home.find('Area') is not None:
            area = source_home_geometry(home)
        if area is None:
            raise ValueError('Missing source home boundary for public sleep selection')
        areas.append(area)
    records = {}
    for bed in beds:
        p = bed.find('Properties'); family = p.get('soclasses_SmartObjectHelpers', '')
        preferences = {l.get('TargetId') for l in bed.findall('EntityLinks/Link') if 'preferedSleeper' in tags(l.get('Name'))}
        pos = world_transform(bed, entities)[0]
        parents = incoming.get(bed.get('EntityId'), [])
        hostel_only = bool(parents) and all(e.find('Properties') is not None and
            'hostel' in tags(e.find('Properties').get('sWH_AI_EntityCategory')) and
            not tags(l.get('Name')).intersection({'reserved', 'private', 'owner'}) for e, l in parents)
        public = (not preferences and (not parents or hostel_only) and
                  (family in ('CampBed', 'ShelterBed', 'TentBed') or family.startswith('SleepingBench')) and
                  not any(inside(pos, a) for a in areas))
        quality = p.find('Bed').get('esSleepQuality')
        if quality not in QUALITY:
            raise ValueError('Unknown source sleep quality: '+str(quality))
        records[bed.get('EntityId')] = dict(entity=bed, position=pos, preferred=preferences,
            quality=QUALITY[quality], public=public, public_evidence='authored_hostel' if hostel_only else 'unassigned_outside_homes')
    return records


def candidates(person, records, *, position=None):
    homes = [E.fromstring(l['source_xml']) for l in person['ai']['links'] if 'Home' in tags(l['label'])]
    if len(homes) > 1:
        raise ValueError('Ambiguous home for sleeping NPC')
    owned = set()
    for home in homes:
        owned.update(l.get('TargetId') for l in home.findall('EntityLinks/Link')
                     if not tags(l.get('Name')).intersection({'parent', 'inventory'}))
    actor_id = person['actor'].get('EntityId')
    origin = position if position is not None else tuple(float(x) for x in person['actor'].get('Pos').split(','))
    selected = []
    for sid, record in records.items():
        preferred = record['preferred']
        if preferred and actor_id not in preferred:
            continue
        if sid in owned:
            kind = 'preferred_home' if preferred else 'home'
            rank = 20 if preferred else 10+record['quality']
        elif record['public'] and math.dist(origin, record['position']) <= PUBLIC_RANGE:
            kind = 'public_fallback'; rank = record['quality']
        else:
            continue
        selected.append(dict(source_id=sid, kind=kind, rank=rank, position=record['position'],
                             evidence=record['public_evidence'] if kind == 'public_fallback' else 'source_home_link'))
    return sorted(selected, key=lambda r: (-r['rank'], math.dist(origin, r['position']), r['source_id']))
