"""Authored CityWalk bench choices using shared native sitting resources."""
import copy
from pathlib import Path
import re
import xml.etree.ElementTree as E
import zipfile

from campaign_entity_links import guid_value
from character_person import resolve_link_targets, unique, world_entity
from character_station_work import unquote
from upgrade_map import read

TABLE = 'Libs/Tables/ai/smartEntity/SmartEntity__so_sitPlace.xml'


def resolve(start, entities, archive, source, reader):
    # The seat query has different pruning from the walking-point query:
    # look/startTest edges are not excluded by the source seat selector.
    query = E.Element('Entity', start.attrib)
    links = E.SubElement(query, 'EntityLinks')
    for link in start.findall('EntityLinks/Link'):
        if not {'parent', 'inventory'}.intersection(link.get('Name', '').split(',')):
            links.append(copy.deepcopy(link))
    # Existing walking-only callers do not supply source programs. Require the
    # effective source program before enabling the new phase in production.
    if source is None or reader is None:
        return []
    root = E.fromstring(reader(source, {'city': ('Scripts.pak', 'Libs/AI/final/sa_city.xml')})['city']['data'])
    tree = root.find("BehaviorTree[@name='CityWalk']/Root")
    searches = [] if tree is None else [s for s in tree.iter('GraphSearch')
        if any(unquote(f.get('categories', '')) == 'Seat' for f in s)]
    search = unique(searches, 'source city-walk seat query')
    attrs = {k: unquote(v) for k, v in search.attrib.items()}
    if (any(attrs.get(k) != v for k, v in dict(Origin='startPoint', depth='1',
            EdgePruning='parent,inventory', includeOrigin='false', AllowedEdges='').items())
            or [f.tag for f in search] != ['CategoryFilter', 'HasBehaviorFilter', 'Nodalyzer']
            or unquote(search[1].get('BehaviorList', '')) != 'use'
            or unquote(search[1].get('FilterChoice', '')) != 'Requestable'):
        raise ValueError('Source city-walk seat selection contract changed')
    entities = resolve_link_targets(archive, query, entities, {})
    by_id = {e.get('EntityId'): e for e in entities}
    seats = {}
    for link in links:
        seat = by_id[link.get('TargetId')]
        props = seat.find('Properties')
        if props is None or 'Seat' not in props.get('sWH_AI_EntityCategory', '').split(','):
            continue
        if seat.get('EntityClass') != 'SmartObjectHolder' or not props.get('soclasses_SmartObjectHelpers', '').startswith('Bench_'):
            raise ValueError('Unsupported source city-walk seat: ' + seat.get('Name', seat.get('EntityId')))
        seats[seat.get('EntityId')] = dict(world_entity(archive, seat, entities).attrib)
    return list(seats.values())


_CONTRACT = {}


def native_contract(target):
    """The native city bench database id and its unrestricted activity link, deep-copied.

    Checked once per unchanged installation; a build asks for it per seated person.
    """
    target = Path(target)
    key = tuple((path, path.stat().st_size, path.stat().st_mtime_ns) for path in
                (target/'Data/Tables.pak', target/'Data/Scripts.pak', target/'Data/Levels/trosecko/level.pak'))
    if key not in _CONTRACT:
        _CONTRACT.clear()
        _CONTRACT[key] = _contract(target)
    database_id, link = _CONTRACT[key]
    return database_id, copy.deepcopy(link)


def _contract(target):
    with zipfile.ZipFile(target/'Data/Tables.pak') as z:
        template = E.fromstring(read(z, TABLE)).find('.//SmartEntityTemplate')
    behavior = template.find("BehaviorTemplates/SmartBehaviorTemplate[@Name='use']")
    resource = template.find("Resources/Resource[@Name='use']")
    if (behavior is None or behavior.get('InitialState') != 'Enabled' or behavior.get('Resource') != 'use'
            or resource is None or resource.get('MaxInstances') != '1'):
        raise ValueError('Native city bench reservation contract changed')
    loc = behavior.find('TreeLocation')
    with zipfile.ZipFile(target/'Data/Scripts.pak') as z:
        root = E.fromstring(read(z, 'AI/'+loc.get('FileName'))).find("BehaviorTree[@name='"+loc.get('TreeName')+"']/Root")
    if (root is None or root.find(".//StanceElement[@smartObject='$__object.id'][@stance='sitting']") is None
            or root.find(".//EntityContextElement[@context='speech_readyForSwitchDialog']") is None
            or root.find(".//Wait[@duration=\"'15s'\"]") is None
            or root.find('.//InstantSynchronize') is None):
        raise ValueError('Native city bench completion/interruption contract changed')
    with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as z:
        mission = E.fromstring(read(z, 'objects_mission0.xml'))
        rows = E.fromstring(read(z, 'tables/ai/scheduler.xml')).find('Schedulers')
    seats = {str(guid_value(e.get('EntityGuid'))) for e in mission if e.get('EntityClass') == 'StanceSmartObject'
        and e.find('Properties') is not None and e.find('Properties').get('guidSmartObjectType') == template.get('DatabaseId')}
    terminals = {r.get('EntityGuid'): r for r in rows if r.tag == 'C_SmartHub' and not len(r)}
    for link in rows.findall('.//S_ActivityLink'):
        p = link.find('Parameters')
        if (link.get('TargetGuid') in seats & terminals.keys() and link.get('PositioningDelegate') == '0'
                and p is not None and all(p.get(k) == v for k, v in dict(BehaviorName='use',
                    BehaviorOverride='false', AllowedGenders='0', TimeOfDayStart='0', TimeOfDayEnd='1440').items())
                and not any(k in p.attrib for k in ('RequiredRole', 'ProvidedRole', 'LinkTag', 'RequiredContext'))):
            return template.get('DatabaseId'), link
    raise ValueError('Missing unrestricted native city bench activity')


def prepare(mission, entry, target):
    """Resolve existing converted seats; never clone an occupied resource."""
    return prepare_sources(mission, entry['city_work'].get('benches', []), target)


def prepare_sources(mission, source_seats, target):
    """Resolve an authored seat selection shared by independent work adapters."""
    if not source_seats:
        return [], None
    template, activity = native_contract(target)
    seats = []
    for source in source_seats:
        pattern = re.compile(r'gm_bench_'+re.escape(source['EntityId'])+r'_\d+_StanceSmartObject')
        matches = [e for e in mission if pattern.fullmatch(e.get('Name', ''))]
        if not matches:
            raise ValueError('Missing converted city-walk bench: '+source['EntityId'])
        for seat in matches:
            p = seat.find('Properties')
            if (seat.get('EntityClass') != 'StanceSmartObject' or p is None
                    or p.get('guidSmartObjectType') != template
                    or not p.get('soclass_SmartObjectHelpers', '').startswith('Sit_1Place_Bench_')):
                raise ValueError('Incompatible converted city-walk bench: '+seat.get('Name'))
            if seat not in seats:
                seats.append(seat)
    return seats, activity


def choices(seats, activity, incoming):
    result = []
    for seat in seats:
        for source in incoming:
            link = copy.deepcopy(activity)
            link.set('TargetGuid', str(guid_value(seat.get('EntityGuid'))))
            for key in ('TimeOfDayStart', 'TimeOfDayEnd'):
                link.find('Parameters').set(key, source.find('Parameters').get(key))
            if signature(link) not in {signature(x) for x in result}:
                result.append(link)
    return result


def signature(element):
    """Compare XML meaning independently of indentation and attribute order."""
    return element.tag, tuple(sorted(element.attrib.items())), tuple(signature(c) for c in element)


def attach(job, hub, rows, seats, links):
    waiting = []
    outgoing = hub.find('Links')
    for seat in seats:
        identity = str(guid_value(seat.get('EntityGuid')))
        registered = [r for r in rows if r.get('EntityGuid') == identity]
        if registered and (len(registered) != 1 or registered[0].tag != 'C_SmartHub' or len(registered[0])):
            raise ValueError('City-walk bench has conflicting scheduler dependencies')
        if not registered:
            E.SubElement(rows, 'C_SmartHub', EntityGuid=identity, IgnoreDeadEnds='false', StupidHub='false')
        edges = job.find('EntityLinks')
        if edges is None:
            edges = E.SubElement(job, 'EntityLinks')
        if not any(e.get('TargetGuid') == seat.get('EntityGuid') and e.get('Name') == '_,use' for e in edges):
            E.SubElement(edges, 'Link', Name='_,use', TargetId=seat.get('EntityId'), TargetGuid=seat.get('EntityGuid'))
        waiting.append((job.get('EntityGuid'), seat.get('EntityGuid'), '_,use'))
    existing = {signature(l) for l in outgoing}
    for link in links:
        if signature(link) not in existing:
            outgoing.append(copy.deepcopy(link)); existing.add(signature(link))
    return waiting
