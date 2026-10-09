"""Translate an authored person lean point to native, interruptible scheduling."""
import copy
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import guid_value
from character_person import unique
from upgrade_map import read, xml


def source_lean_point(source, person, area):
    with zipfile.ZipFile(Path(source) / 'Data/Levels' / person['source_level'] / 'level.pak') as archive:
        entities = list(ET.fromstring(read(archive, 'objects_mission0.xml')).iter('Entity'))
    by_id = {e.get('EntityId'): e for e in entities}
    points = [by_id[l.get('TargetId')] for l in area.findall('EntityLinks/Link')
              if l.get('Name') == 'Lean' and l.get('TargetId') in by_id]
    if not points:
        raise ValueError('Source work area has no authored lean point')
    origin = tuple(map(float, person['actor'].get('Pos').split(',')))
    # Prefer the actor's floor, then the closest authored point. No coordinate
    # or person-name rules: upper floors must not win on horizontal distance.
    def distance(point):
        pos = tuple(map(float, point.get('Pos').split(',')))
        return (abs(pos[2] - origin[2]), sum((a-b)**2 for a, b in zip(pos, origin)))
    point = min(points, key=distance)
    misc = point.find('Properties/Script')
    flags = dict(pair.split(':', 1) for pair in misc.get('Misc', '').split('|')) if misc is not None else {}
    side = unique((side for side in ('Back', 'Left', 'Right') if flags.get('lean' + side) == 'true'),
                  'source lean direction')
    return point, 'leaning_' + side.lower()


def native_lean_activity(target, helper):
    with zipfile.ZipFile(Path(target) / 'Data/Levels/trosecko/level.pak') as archive:
        entities = list(ET.fromstring(read(archive, 'objects_mission0.xml')).iter('Entity'))
        scheduler = ET.fromstring(read(archive, 'tables/ai/scheduler.xml'))
    candidates = {str(guid_value(e.get('EntityGuid'))): e for e in entities
                  if e.get('EntityClass') == 'DetailMovementSmartObject' and e.find('Properties') is not None
                  and e.find('Properties').get('soclass_SmartObjectHelpers') == helper}
    for row in scheduler.findall('Schedulers/C_SmartHub'):
        for link in row.findall('Links/S_ActivityLink'):
            params = link.find('Parameters')
            if (link.get('TargetGuid') in candidates and link.get('PositioningDelegate') == '0'
                    and params is not None and params.get('BehaviorName') == 'use'
                    and params.get('TimeOfDayStart') == '0' and params.get('TimeOfDayEnd') == '1440'
                    and not any(k in params.attrib for k in ('RequiredRole', 'LinkTag', 'BuffTag', 'ProvidedRole'))):
                terminal = unique((r for r in scheduler.findall('Schedulers/C_SmartHub')
                                   if r.get('EntityGuid') == link.get('TargetGuid')), 'native lean scheduler record')
                if len(terminal):
                    raise ValueError('Native lean terminal has additional scheduler dependencies')
                return (copy.deepcopy(candidates[link.get('TargetGuid')].find('Properties')),
                        copy.deepcopy(link), copy.deepcopy(terminal))
    raise ValueError('No native unrestricted leaning activity for ' + helper)


def register_lean_activity(scheduler_blob, add_entity, npc, hub, point, helper, target, name):
    props, activity, terminal = native_lean_activity(target, helper)
    lean = add_entity(name, 'DetailMovementSmartObject', point.get('Pos'), rotation=point.get('Rotate'))
    lean.append(props)
    root = ET.fromstring(scheduler_blob)
    rows = root.find('Schedulers')
    if rows is None:
        raise ValueError('Missing native schedulers container')
    for actor, destination, is_hub in ((npc, hub, False), (hub, lean, True)):
        guid = str(guid_value(actor.get('EntityGuid')))
        if any(r.get('EntityGuid') == guid for r in rows):
            raise ValueError('Person activity already registered')
        row = ET.SubElement(rows, 'C_SmartHub', EntityGuid=guid, IgnoreDeadEnds='false', StupidHub='false')
        if is_hub: row.set('Position', actor.get('Pos'))
        link = copy.deepcopy(activity)
        link.set('TargetGuid', str(guid_value(destination.get('EntityGuid'))))
        if not is_hub:
            # Native NPC -> SchedulerHub links use the plain '_' activity.
            link.find('Parameters').attrib.pop('BehaviorName')
        ET.SubElement(row, 'Links').append(link)
    # Native activities resolve TargetGuid through the compiled scheduler,
    # not the entity list. Even a terminal smart object needs an empty row.
    # Omitting it leaves a null destination in C_SchedulerSubbrain at load.
    terminal.set('EntityGuid', str(guid_value(lean.get('EntityGuid'))))
    terminal.attrib.pop('Position', None)
    if any(r.get('EntityGuid') == terminal.get('EntityGuid') for r in rows):
        raise ValueError('Lean terminal already registered')
    rows.append(terminal)
    validate_scheduler_targets(root)
    return xml(root), lean


def validate_scheduler_targets(root):
    """Every activity target must have a compiled record, including leaves."""
    rows = root.find('Schedulers')
    if rows is None:
        raise ValueError('Missing native schedulers container')
    identities = [row.get('EntityGuid') for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError('Duplicate compiled scheduler record')
    registered = set(identities)
    missing = sorted({link.get('TargetGuid') for row in rows for link in row.findall('Links/S_ActivityLink')
                      if link.get('TargetGuid') not in registered})
    if missing:
        raise ValueError('Unregistered compiled scheduler targets: ' + ', '.join(missing))
