"""Bind source schedule area arrivals to native interruptible movement.

This is the arrival phase, not a replacement for the area's work behavior.
Only importer-owned idle links may be replaced. No teleport or update script.
"""
import copy
import hashlib
import uuid
import xml.etree.ElementTree as ET
from character_person import resolve_link_targets, world_entity
from campaign_entity_links import native_guid, guid_value, append_links
from character_person_activity import validate_scheduler_targets
from character_person_world import native_wait_activity
from upgrade_map import read, xml

ARRIVAL_TEMPLATE = str(uuid.uuid5(uuid.NAMESPACE_URL, 'kingdomcomegluemapper/schedule/arrival'))


def arrival_resources():
    table = ET.Element('database', name='barbora')
    template = ET.SubElement(ET.SubElement(table, 'SmartEntitys', version='1'), 'SmartEntityTemplate',
        DatabaseId=ARRIVAL_TEMPLATE, Name='gm_schedule_arrival', UpdatePriority='false')
    behavior = ET.SubElement(ET.SubElement(template, 'BehaviorTemplates'), 'SmartBehaviorTemplate',
                            Name='use', InitialState='Enabled', MaxInstances='1')
    ET.SubElement(behavior, 'TreeLocation', FileName='gluemapper/schedule_arrival.xml', TreeName='use')
    trees = ET.Element('BehaviorTrees')
    tree = ET.SubElement(trees, 'BehaviorTree', name='use', is_function='0')
    ET.SubElement(tree, 'Variables')
    root = ET.SubElement(tree, 'Root', OneTimeOnly='false', FailState='Recoverable', saveVersion='2')
    seq = ET.SubElement(ET.SubElement(root, 'Behavior', canSkip='1'), 'Sequence')
    ET.SubElement(seq, 'Move', destinationSpecification='$__object.id', destinationSpecification2='',
        destinationSpecification3='', speed='Walk', stopWithinDistance='0.2', stopDistanceVariation='0',
        rayCasteFlee='false', successDistance='0', changeNPCState='true', fastForwardIncludesMove='false',
        additionalParams='', pathFindingParams='', staminaPolicy='', pathInfo='', AnimationToPlay='', AnimationContext='')
    ET.SubElement(seq, 'WaitAction')
    ET.SubElement(seq, 'Wait', duration="'10s'", timeType='GameTime', doFail='false', variation='', skipInLOD='false')
    return {'Libs/Tables/ai/smartEntity/SmartEntity__gm_schedule_arrival.xml':xml(table),
            'AI/gluemapper/schedule_arrival.xml':xml(trees)}


def resolve_arrivals(schedule, archive, person=None, source=None, reader=None, target=None):
    result = []
    mission = list(ET.fromstring(read(archive, 'objects_mission0.xml')).iter('Entity'))
    for entry in schedule['resolved']:
        behavior = entry['behavior']
        from character_horse_handler_work import supports as stable_supports, resolve as stable_resolve
        if stable_supports(entry):
            result.append(stable_resolve(entry, archive, person, source, reader, target))
            continue
        from character_farmer_work import supports as farmer_supports, resolve as farmer_resolve
        if farmer_supports(entry):
            result.append(farmer_resolve(entry, archive, person, source, reader))
            continue
        from character_shop_guard_work import supports as guard_supports, resolve as guard_resolve
        if guard_supports(entry):
            result.append(guard_resolve(entry, archive, source, reader, target))
            continue
        from character_seated_work import supports as seated_supports, resolve as seated_resolve
        if seated_supports(entry):
            result.append(seated_resolve(entry, archive, source, reader))
            continue
        from character_city_work import supports as city_supports, resolve as city_resolve
        if city_supports(entry):
            result.append(city_resolve(entry, archive, source, reader))
            continue
        from character_market_jobs import supports as market_supports, resolve as market_resolve
        if market_supports(entry):
            result.append(market_resolve(entry, archive))
            continue
        if entry.get('target_kind') == 'quest_controller':
            if source is None or reader is None:
                raise ValueError('Controller work requires its effective source behavior')
            from character_station_work import resolve
            result.append(resolve(entry, archive, source, reader))
            continue
        if (behavior['tree_file'].lower(), behavior['tree_name']) == ('final/sa_garrison.xml', 'Guard'):
            forced = [l for l in (person or {}).get('ai', {}).get('links', [])
                      if l['label'] == 'garrison_forcedWorkplace']
            if len(forced) != 1:
                raise ValueError('Guard work requires one authored forced workplace; dynamic garrison assignment is not converted')
            point = ET.fromstring(forced[0]['source_xml'])
            if point.get('EntityClass') != 'TagPoint':
                raise ValueError('Unsupported forced guard workplace')
            pose = world_entity(archive, point, mission)
            result.append(dict(entry, arrival=dict(pose.attrib),
                workplace_links=[dict(l.attrib) for l in point.findall('EntityLinks/Link')]))
            continue
        area = ET.Element('Entity', entry['area'])
        links = ET.SubElement(area, 'EntityLinks')
        inside = [l for l in entry['target_links'] if l.get('Name') == 'inside']
        if len(inside) != 1: raise ValueError('Expected one authored area arrival link')
        ET.SubElement(links, 'Link', inside[0])
        resolved = resolve_link_targets(archive, area, mission, {})
        targets = [e for e in resolved if e.get('EntityId') == inside[0]['TargetId']]
        if len(targets) != 1 or targets[0].get('EntityClass') != 'TagPoint':
            raise ValueError('Unsupported authored arrival target')
        result.append(dict(entry, arrival=dict(world_entity(archive, targets[0], resolved).attrib)))
    return result


def baseline_schedule_names(files, names):
    """Select only idle schedules owned by the person importer, including old imports.

    A previous pending report is not authorization to replace a live authored
    schedule. Recheck the current actor, anchor and every compiled link.
    """
    mission = ET.fromstring(files['objects_mission0.xml'])
    rows = ET.fromstring(files['tables/ai/scheduler.xml']).find('Schedulers')
    selected = set()
    for name in names:
        actors = [e for e in mission if e.get('Name') == name]
        anchors = [e for e in mission if e.get('Name') == 'gm_person_idle_' + name]
        if len(actors) != 1 or len(anchors) != 1:
            continue
        identity = str(guid_value(actors[0].get('EntityGuid')))
        schedules = [r for r in rows if r.get('EntityGuid') == identity]
        if len(schedules) != 1:
            continue
        from character_sleep_work import owned_links
        sleeping = owned_links(mission, rows, actors[0], schedules[0])
        links = [l for l in schedules[0].findall('Links/S_ActivityLink') if l not in sleeping]
        delegate = str(guid_value(anchors[0].get('EntityGuid')))
        if links and all(l.get('PositioningDelegate') == delegate and l.find('Parameters') is not None
                         and l.find('Parameters').get('BehaviorName') == 'schedulerWait' for l in links):
            selected.add(name)
    return selected


def bind_arrivals(files, schedules, target):
    files = dict(files)
    mission = ET.fromstring(files['objects_mission0.xml'])
    scheduler = ET.fromstring(files['tables/ai/scheduler.xml'])
    report = bind_arrival_trees(files, mission, scheduler, schedules, target)
    files.update({'objects_mission0.xml':xml(mission), 'tables/ai/scheduler.xml':xml(scheduler)})
    return files, report


def bind_arrival_trees(files, mission, scheduler, schedules, target):
    """bind_arrivals on already parsed trees, edited in place.

    Callers binding many people in turn parse the 30+ MB mission once instead
    of once per person. Only files['waitinglinks.xml'] is replaced.
    """
    rows = scheduler.find('Schedulers')
    template, _ = native_wait_activity(target)
    next_id = max(int(e.get('EntityId', '0')) for e in mission) + 1
    links, report = [], []
    waiting = ET.fromstring(files['waitinglinks.xml'])
    for name, entries in schedules.items():
        npc = next(e for e in mission if e.get('Name') == name)
        old_anchor = next(e for e in mission if e.get('Name') == 'gm_person_idle_' + name)
        row = next(r for r in rows if r.get('EntityGuid') == str(guid_value(npc.get('EntityGuid'))))
        old = row.find('Links')
        from character_sleep_work import owned_links
        sleeping = owned_links(mission, rows, npc, row)
        idle = [] if old is None else [l for l in old if l not in sleeping]
        if not idle or any(l.get('PositioningDelegate') != str(guid_value(old_anchor.get('EntityGuid')))
                          or l.find('Parameters') is None
                          or l.find('Parameters').get('BehaviorName') != 'schedulerWait' for l in idle):
            raise ValueError('Cannot replace non-baseline actor schedule: ' + name)
        if not entries: raise ValueError('Empty arrival schedule')
        # Remove only the importer-owned idle activity, preserving unrelated
        # entity links and tool/home graphs. The compiled schedule is replaced
        # below; its static graph representation must agree with that change.
        for parent in (npc.find('EntityLinks'), waiting.find('WaitingLinks')):
            if parent is None: continue
            for element in list(parent):
                if (element.tag == 'Link' and element.get('TargetGuid') == old_anchor.get('EntityGuid')
                        and element.get('Name') == '_,schedulerWait'):
                    parent.remove(element)
                elif (element.tag == 'WaitingLink' and element.get('SourceId') == npc.get('EntityGuid')
                      and element.get('TargetId') == old_anchor.get('EntityGuid')):
                    for definition in list(element):
                        if definition.tag == 'LinkDefinition' and definition.text == '_,schedulerWait':
                            element.remove(definition)
                    if not len(element): parent.remove(element)
        for link in idle: old.remove(link)
        for entry in entries:
            pose = entry['arrival']
            guid = native_guid(int.from_bytes(hashlib.sha256(('schedule-arrival/' + npc.get('EntityGuid') +
                '/' + pose['EntityGuid']).encode()).digest()[:8], 'little'))
            anchor = next((e for e in mission if e.get('EntityGuid') == guid), None)
            if anchor is None:
                anchor = ET.SubElement(mission, 'Entity', Name='gm_schedule_' + name + '_' + pose['EntityId'],
                    EntityClass='SmartObjectHolder', EntityGuid=guid, EntityId=str(next_id), Pos=pose['Pos'])
                next_id += 1
                if pose.get('Rotate'): anchor.set('Rotate', pose['Rotate'])
                ET.SubElement(anchor, 'Properties', bSaved_by_game='0', guidSmartObjectType=ARRIVAL_TEMPLATE)
                ET.SubElement(rows, 'C_SmartHub', EntityGuid=str(guid_value(guid)), IgnoreDeadEnds='false', StupidHub='false')
            links.append((npc.get('EntityGuid'), guid, '_,use'))
            link = copy.deepcopy(template)
            link.set('TargetGuid', str(guid_value(guid)))
            link.set('PositioningDelegate', '0')
            params = link.find('Parameters')
            params.set('BehaviorName', 'use')
            params.set('TimeOfDayStart', str(entry['start_minute']))
            params.set('TimeOfDayEnd', str(entry['end_minute']))
            old.append(link)
            report.append(dict(person=name, activity=entry['activity'], arrival=pose,
                interval=[entry['start_minute'],entry['end_minute']],
                activity_tree=entry['behavior']['tree_name'], activity_pending=True, runtime_verified=False))
    validate_scheduler_targets(scheduler)
    files['waitinglinks.xml'] = append_links(xml(waiting), links)
    return report
