"""Generic sleep extension: native shared beds, authored or inferred job clocks.

Quest links are preserved. Ordinary generated work is limited to waking hours;
a timed, lower-than-quest sleep pool uses native beds and stance lifetimes.
"""
import copy
import hashlib
import xml.etree.ElementTree as E

from campaign_entity_links import append_links, guid_value, native_guid
from character_person import unique
from character_city_benches import signature
from character_sleep_clock import statistics, plan_clocks
from character_sleep_beds import catalog, candidates, source_contract
from character_sleep_native import contract
from character_person_activity import validate_scheduler_targets
from upgrade_map import xml

PREFIX = 'gm_sleep_'


def waking_work_clock(mission, scheduler, actor, source_clock):
    """Apply only a validated generated sleep pool to an authored work clock."""
    rows = scheduler.find('Schedulers')
    owner = unique((r for r in rows if r.get('EntityGuid') == str(guid_value(actor.get('EntityGuid')))),
                   'work clock owner')
    intervals = set(source_clock)
    for link in owned_links(mission, rows, actor, owner):
        p = link.find('Parameters')
        a, b = int(p.get('TimeOfDayStart')), int(p.get('TimeOfDayEnd'))
        if not 0 <= a < b <= 1440: raise ValueError('Invalid sleep clock')
        intervals = {piece for x, y in intervals for piece in ((x, min(y, a)), (max(x, b), y)) if piece[0] < piece[1]}
    return intervals


def restrict_work_to_waking_hours(mission, scheduler, *, actors=None):
    """Bound converter-owned routine work by the actor's existing sleep clock.

    Merely adding a higher-priority sleep choice leaves continuous work valid
    all night. Restrict only ordinary generated work links, never quest links.
    Splitting intervals is idempotent and also supports rotating guard shifts.
    """
    targets={str(guid_value(e.get('EntityGuid'))):e for e in mission if e.get('EntityGuid')}
    rows=scheduler.find('Schedulers');changed=[]
    for actor in mission:
        if actor.get('EntityClass') not in ('NPC','NPC_Female'):continue
        if actors is not None and actor.get('Name') not in actors:continue
        row=next((r for r in rows if r.get('EntityGuid')==str(guid_value(actor.get('EntityGuid')))),None)
        if row is None:continue
        sleep=owned_links(mission,rows,actor,row)
        if not sleep:continue
        night=[(int(l.find('Parameters').get('TimeOfDayStart')),int(l.find('Parameters').get('TimeOfDayEnd'))) for l in sleep]
        links=row.find('Links');replacement=[];modified=False
        # The same invalidation rule applies at wake-up. Native all-day bed
        # links have no expiry, so their copied default is not suitable for a
        # generated clock. Limit this repair to our validated pool and leaves.
        pool=next(r for r in rows if r.get('EntityGuid')==str(guid_value(identity(actor))))
        for edge in [*sleep, *pool.findall('Links/S_ActivityLink')]:
            p=edge.find('Parameters')
            if p is not None and p.get('HaltOnInvalid')!='true':
                p.set('HaltOnInvalid','true');modified=True
        for link in links:
            p=link.find('Parameters');dest=targets.get(link.get('TargetGuid'))
            if (p is None or dest is None or not dest.get('Name','').startswith('gm_schedule_')
                    or p.get('Priority')!='0' or p.get('Privileged','false')!='false'
                    or any(k in p.attrib for k in ('RequiredRole','RequiredContext','LinkTag','BuffTag'))):
                replacement.append(link);continue
            original=(int(p.get('TimeOfDayStart','0')),int(p.get('TimeOfDayEnd','1440')))
            intervals=[original]
            for a,b in night:
                intervals=[piece for x,y in intervals for piece in ((x,min(y,a)),(max(x,b),y)) if piece[0]<piece[1]]
            # A clock excludes future selection; an already-running activity
            # must also become interruptible when that clock expires.
            if intervals==[original]:
                if intervals!=[(0,1440)] and p.get('HaltOnInvalid')!='true':
                    p.set('HaltOnInvalid','true');modified=True
                replacement.append(link);continue
            modified=True
            for a,b in intervals:
                new=copy.deepcopy(link);params=new.find('Parameters')
                params.set('TimeOfDayStart',str(a));params.set('TimeOfDayEnd',str(b))
                params.set('HaltOnInvalid','true');replacement.append(new)
        if modified:links[:]=replacement;changed.append(actor.get('Name'))
    return changed


def identity(actor):
    return native_guid(int.from_bytes(hashlib.sha256(('sleep-pool/'+actor.get('EntityGuid')).encode()).digest()[:8], 'little'))


def owned_links(mission, rows, actor, row):
    """Recognize the additive extension when a later upgrader replaces idle work."""
    guid = identity(actor); number = str(guid_value(guid))
    links = [l for l in row.findall('Links/S_ActivityLink') if l.get('TargetGuid') == number]
    if not links:
        return []
    pools = [e for e in mission if e.get('EntityGuid') == guid]
    hubs = [r for r in rows if r.get('EntityGuid') == number]
    graph = [l for l in actor.findall('EntityLinks/Link') if l.get('TargetGuid') == guid]
    if (len(pools) != 1 or pools[0].get('Name') != PREFIX+actor.get('Name')
            or pools[0].get('EntityClass') != 'SchedulerHub' or len(hubs) != 1
            or hubs[0].tag != 'C_SmartHub' or len(graph) != 1
            or graph[0].get('Name') != '_' or graph[0].get('TargetId') != pools[0].get('EntityId')):
        raise ValueError('Unrecognized sleep extension on baseline actor')
    for link in links:
        p = link.find('Parameters')
        if (link.get('PositioningDelegate') != '0' or p is None or p.get('Priority') != '2'
                or 'BehaviorName' in p.attrib or 'ProvidedRole' in p.attrib):
            raise ValueError('Unrecognized sleep extension activity')
    return links


def bind(mission, scheduler, name, clock, selected, template, native_activity):
    actor = unique((e for e in mission if e.get('Name') == name), 'sleep actor')
    if actor.get('EntityClass') not in ('NPC', 'NPC_Female'):
        raise ValueError('Sleep target is not a human NPC')
    rows = scheduler.find('Schedulers')
    owner = unique((r for r in rows if r.get('EntityGuid') == str(guid_value(actor.get('EntityGuid')))), 'sleep actor scheduler')
    old_links = owner.find('Links')
    if old_links is None or not len(old_links):
        raise ValueError('Sleep requires an existing daytime schedule')
    pool_guid = identity(actor); pool_id = str(guid_value(pool_guid))
    current = [l for l in old_links if l.get('TargetGuid') == pool_id]
    # Do not overwrite a pre-existing authored sleep routine.
    by_guid = {str(guid_value(e.get('EntityGuid'))): e for e in mission if e.get('EntityGuid')}
    for l in old_links:
        target = by_guid.get(l.get('TargetGuid')); p = l.find('Parameters')
        if target is not None and target.find('Properties') is not None and target.find('Properties').get('guidSmartObjectType') == template and p is not None and p.get('BehaviorName') == 'use':
            return None, [], False
    beds = []
    for choice in selected:
        matches = [e for e in mission if e.get('Name') == choice.get('entity_name', 'gm_world_bed_'+choice['source_id']+'_bed')]
        if not matches:
            continue  # An old/rental contract needs an explicit identity adapter.
        bed = unique(matches, 'converted sleep bed'); props = bed.find('Properties')
        if bed.get('EntityClass') != 'StanceSmartObject' or props is None or props.get('guidSmartObjectType') != template:
            raise ValueError('Incompatible converted sleep bed')
        hub = unique((r for r in rows if r.get('EntityGuid') == str(guid_value(bed.get('EntityGuid')))), 'sleep bed scheduler')
        if hub.findall('.//S_ActivityLink') or hub.find(".//EffectHeal[@Role='sleeper']") is None:
            raise ValueError('Converted sleep bed has foreign dependencies or no healing role')
        beds.append((bed, choice))
    if not beds:
        return None, [], False
    # An explicit household assignment may be added after public fallbacks.
    # The pool's spatial anchor must follow its preferred bed, not list order.
    anchor=max(beds,key=lambda pair:pair[1]['rank'])[0].get('Pos')
    outgoing = E.Element('Links'); incoming = []; graph = []
    for start, end in clock['intervals']:
        if not 0 <= start < end <= 1440:
            raise ValueError('Invalid sleep interval')
        edge = copy.deepcopy(native_activity); edge.set('TargetGuid', pool_id)
        params = edge.find('Parameters')
        params.attrib.pop('BehaviorName'); params.attrib.pop('ProvidedRole')
        params.set('Priority', '2'); params.set('TimeOfDayStart', str(start)); params.set('TimeOfDayEnd', str(end))
        params.set('HaltOnInvalid', 'true')
        incoming.append(edge)
        for bed, choice in beds:
            link = copy.deepcopy(native_activity); link.set('TargetGuid', str(guid_value(bed.get('EntityGuid'))))
            p = link.find('Parameters'); p.set('TimeOfDayStart', str(start)); p.set('TimeOfDayEnd', str(end))
            p.set('Priority', str(choice['rank']))
            p.set('HaltOnInvalid', 'true')
            outgoing.append(link)
    existing = [e for e in mission if e.get('EntityGuid') == pool_guid or e.get('Name') == PREFIX+name]
    hub_matches = [r for r in rows if r.get('EntityGuid') == pool_id]
    if existing or hub_matches or current:
        pool = unique(existing, 'owned sleep pool'); hub = unique(hub_matches, 'owned sleep scheduler')
        expected_edges = sorted(('_,use', b.get('EntityGuid'), b.get('EntityId')) for b, _ in beds)
        actual_edges = sorted((l.get('Name', ''), l.get('TargetGuid', ''), l.get('TargetId', ''))
                              for l in pool.findall('EntityLinks/Link'))
        actor_edges = [l for l in actor.findall('EntityLinks/Link') if l.get('TargetGuid') == pool_guid]
        if (pool.get('EntityClass') != 'SchedulerHub' or pool.get('Name') != PREFIX+name or pool.get('EntityGuid') != pool_guid
                or actual_edges != expected_edges or len(actor_edges) != 1
                or actor_edges[0].get('Name') != '_' or actor_edges[0].get('TargetId') != pool.get('EntityId')
                or hub.find('Links') is None or clock_signature(hub.find('Links')) != clock_signature(outgoing)
                or [clock_signature(l) for l in current] != [clock_signature(l) for l in incoming]):
            raise ValueError('Existing sleep pool differs from the current source/policy; rebuild from a pre-sleep package')
        moved=pool.get('Pos')!=anchor or hub.get('Position')!=anchor
        pool.set('Pos',anchor);hub.set('Position',anchor)
        for edge in [*current, *hub.findall('Links/S_ActivityLink')]:
            if edge.find('Parameters').get('HaltOnInvalid')!='true':
                edge.find('Parameters').set('HaltOnInvalid','true');moved=True
        return [dict(c, bed_guid=b.get('EntityGuid')) for b, c in beds], [], moved
    pool = E.SubElement(mission, 'Entity', Name=PREFIX+name, EntityClass='SchedulerHub',
                        EntityId=str(max(int(e.get('EntityId', '0')) for e in mission)+1),
                        EntityGuid=pool_guid, Pos=anchor)
    E.SubElement(pool, 'Properties', bSaved_by_game='0')
    hub = E.SubElement(rows, 'C_SmartHub', EntityGuid=pool_id, IgnoreDeadEnds='false', StupidHub='false', Position=pool.get('Pos'))
    hub.append(outgoing)
    edges = E.SubElement(pool, 'EntityLinks')
    for bed, choice in beds:
        E.SubElement(edges, 'Link', Name='_,use', TargetGuid=bed.get('EntityGuid'), TargetId=bed.get('EntityId'))
        graph.append((pool_guid, bed.get('EntityGuid'), '_,use'))
    actor_edges = actor.find('EntityLinks')
    if actor_edges is None:
        actor_edges = E.SubElement(actor, 'EntityLinks')
    E.SubElement(actor_edges, 'Link', Name='_', TargetGuid=pool_guid, TargetId=pool.get('EntityId'))
    graph.append((actor.get('EntityGuid'), pool_guid, '_'))
    old_links.extend(incoming)
    return [dict(c, bed_guid=b.get('EntityGuid')) for b, c in beds], graph, True


def clock_signature(element):
    """Allow only the known all-day-template invalidation default to migrate."""
    normalized=copy.deepcopy(element)
    for p in normalized.iter('Parameters'):
        if p.get('HaltOnInvalid') not in ('false','true'):
            raise ValueError('Invalid sleep clock halt policy')
        p.set('HaltOnInvalid','true')
    return signature(normalized)


def upgrade(files, people, archive, target, tables, reader, source, *, notify=lambda message: None):
    source_contract(E.fromstring(reader(source, {'home': ('Scripts.pak', 'Libs/AI/final/sa_home.xml')})['home']['data']))
    brains = {r['brain_id'] for r in tables.get('ai/brain')['rows'] if r['brain_name'] == 'npc_daycycle'}
    groups = statistics(tables.get('rpg/soul')['rows'], brains)
    clocks = plan_clocks(people, groups)
    notify('Scanning source beds and home boundaries')
    records = catalog(archive, tables, notify=notify)
    notify('Loading native sleeping activities')
    template, activity = contract(target)
    mission = E.fromstring(files['objects_mission0.xml']); scheduler = E.fromstring(files['tables/ai/scheduler.xml'])
    graph = []; reports = []; changed = False
    for name, clock in sorted(clocks.items()):
        notify('Binding sleeping schedule: ' + name)
        actor = next((e for e in mission if e.get('Name') == name), None)
        if actor is None:
            continue
        selected = candidates(people[name], records, position=tuple(float(v) for v in actor.get('Pos').split(',')))
        from home_bed_assignment import choices as assigned_beds
        selected.extend(assigned_beds(mission, actor))
        beds, links, modified = bind(mission, scheduler, name, clock, selected, template, activity)
        graph.extend(links); changed |= modified
        reports.append(dict(person=name, job='native_sleep', extension_only=True, clock=clock,
            beds=beds or [], runtime_verified=False,
            pending=[] if beds else ['No compatible sleep place, or an authored bed schedule is already present']))
    validate_scheduler_targets(scheduler)
    changed |= bool(restrict_work_to_waking_hours(mission, scheduler))
    if not changed:
        return files, reports
    return dict(files, **{'objects_mission0.xml': xml(mission), 'tables/ai/scheduler.xml': xml(scheduler),
                         'waitinglinks.xml': append_links(files['waitinglinks.xml'], graph, allow_untagged=True)}), reports
