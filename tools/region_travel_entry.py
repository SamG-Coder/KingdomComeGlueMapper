"""Package the native Level/OnWake/profile-streaming destination lifecycle.

This registers world services and activates the station through KCD2's normal
level graph. It never constructs a companion soul or changes horse ownership.
"""
import copy
import hashlib
import xml.etree.ElementTree as ET

from campaign_entity_links import append_links, guid_value
from campaign_trigger_areas import TriggerArea, read_areas, write_areas
from upgrade_map import read, xml
from character_world_ai import register_interrupt_services


STREAMING_SOURCE = 'Quests/Final/Barbora/utils/streaming/streamprofileshandling.xml'
PLAYER_SERVICES = ('playerProxy', 'so_player_scheduler')
PLAYER_BEHAVIORS = {'playerWait', 'animationAction'}


def unique(items, label):
    items = list(items)
    if len(items) != 1:
        raise ValueError(f'Expected one {label}, found {len(items)}')
    return items[0]


def entry_graph(level, profile):
    """Use the shipped streaming helper, including its loaded acknowledgement.

    OnWake covers save loading and repeat visits. OnLevelSwitched explicitly
    requests the same initialization when entering through regional travel.
    The helper coalesces requests; there is no per-frame or timed retry loop.
    """
    root = ET.Element('Database', Name='brambora')
    host = ET.SubElement(ET.SubElement(root, 'Skald'), 'Level',
                         Name=level, HibernateMode='Auto')
    ports = ET.SubElement(host, 'Ports')
    ET.SubElement(ports, 'Port', Name='ready', Direction='Out', Type='bool')
    if profile is None:
        nodes = ET.SubElement(host, 'Nodes')
        state = ET.SubElement(nodes, 'State', Name='entry_ready', TypeT='bool')
        ET.SubElement(state, 'Constant', Name='DefaultValue', Value='true')
        ET.SubElement(ET.SubElement(nodes, 'Output', Name='Output'), 'Edge',
                      From='entry_ready.State', To='ready')
        return xml(root)
    ET.SubElement(ET.SubElement(host, 'Assets'), 'ProfileAsset',
                  Name='arrivalProfile', AssetProfiles=profile)
    ET.SubElement(ET.SubElement(host, 'Definitions'), 'Definition',
                  File='streamprofileshandling.xml')
    nodes = ET.SubElement(host, 'Nodes')
    sequence = ET.SubElement(nodes, 'TriggerSequence', Name='begin_entry')
    ET.SubElement(sequence, 'Edge', From='OnWake', To='Exec')
    ET.SubElement(sequence, 'Edge', From='OnLevelSwitched', To='Exec')
    state = ET.SubElement(nodes, 'State', Name='entry_ready', TypeT='bool')
    ET.SubElement(state, 'Constant', Name='DefaultValue', Value='false')
    ET.SubElement(state, 'Edge', From='begin_entry.A', To='SetFalse')
    ET.SubElement(state, 'Edge', From='stream_station.onloaded', To='SetTrue')
    stream = ET.SubElement(nodes, 'streamprofileshandling', Name='stream_station')
    ET.SubElement(stream, 'Asset', Name='profiles', Alias='arrivalProfile')
    ET.SubElement(stream, 'Edge', From='begin_entry.B', To='streamprofiles')
    ET.SubElement(ET.SubElement(nodes, 'Output', Name='Output'), 'Edge',
                  From='entry_ready.State', To='ready')
    return xml(root)


def register_entry(files, graph, graph_path, native_level, native_scripts,
                   level, level_id, identity, *, station_streaming=False):
    """Return level replacements, quest resources and a verifiable build receipt.

    ``files`` contains the already-converted destination metadata. Existing
    scenery, showroom, horse schedulers, player identity and dialogue survive.
    Native profile IDs are local to this level; all entity IDs remain unique.
    """
    files = dict(files)
    # Native weather conditions are region-independent (rain/wind/time), unlike
    # scheduler and quest rows. Keep them instead of the empty probe shell.
    files['tables/weatherprofiles.xml'] = read(native_level, 'tables/weatherprofiles.xml')
    # Static terrain probes explicitly disable layer activation. Native region
    # entry relies on it; registering a profile alone leaves that gate closed.
    mission_settings = ET.fromstring(files['mission_mission0.xml'])
    environment = mission_settings.find('Environment/EnvState')
    native_environment = ET.fromstring(read(native_level, 'mission_mission0.xml')).find('Environment/EnvState')
    if environment is None or native_environment is None or native_environment.get('UseLayersActivation') != '1':
        raise ValueError('Expected native enabled layer activation and destination environment settings')
    environment.set('UseLayersActivation', native_environment.get('UseLayersActivation'))
    files['mission_mission0.xml'] = xml(mission_settings)
    mission = ET.fromstring(files['objects_mission0.xml'])
    entities = list(mission.findall('Entity'))
    if any(e.get('EntityClass') == 'LevelHolder' for e in entities):
        raise ValueError('Destination already has a LevelHolder; merge explicitly')
    arrival = unique((e for e in entities if e.get('Name') == 'gmtravel_arrival_kcd1'), 'arrival marker')
    source = list(ET.fromstring(read(native_level, 'objects_mission0.xml')).findall('Entity'))
    by_name = {name: unique((e for e in source if e.get('Name') == name), name)
               for name in (*PLAYER_SERVICES, 'sa_land')}
    used_guids = {e.get('EntityGuid') for e in entities}
    next_id = max(int(e.get('EntityId', '0')) for e in entities) + 1

    def place(name, cls, template=None):
        nonlocal next_id
        if any(e.get('Name') == name for e in mission.findall('Entity')):
            raise ValueError('Duplicate entry service: ' + name)
        entity = copy.deepcopy(template) if template is not None else ET.Element('Entity')
        guid = identity('entry/' + name)
        if guid in used_guids:
            raise ValueError('Entry entity GUID collision: ' + name)
        used_guids.add(guid)
        entity.attrib.pop('Layer', None)
        entity.attrib.pop('EditorLayer', None)
        entity.set('Name', name)
        entity.set('EntityClass', cls)
        entity.set('EntityId', str(next_id))
        entity.set('EntityGuid', guid)
        entity.set('Pos', arrival.get('Pos'))
        next_id += 1
        mission.append(entity)
        return entity

    holder = place(level, 'LevelHolder')
    ET.SubElement(holder, 'Properties', iLevelId=str(level_id))
    services = {name: place(name, by_name[name].get('EntityClass'), by_name[name])
                for name in PLAYER_SERVICES}
    # Only generic player scheduling belongs to this destination. Native links
    # to weddings, combat tutorials etc. point into an entirely different map.
    src_ids = {e.get('EntityId'): name for name, e in by_name.items()}
    waiting = []
    for name, entity in services.items():
        links = entity.find('EntityLinks')
        if links is None:
            continue
        for link in list(links):
            target = services.get(src_ids.get(link.get('TargetId')))
            behavior = link.get('Name', '').split(',')[-1]
            if target is None or behavior not in PLAYER_BEHAVIORS:
                links.remove(link)
                continue
            link.set('TargetId', target.get('EntityId'))
            link.set('TargetGuid', target.get('EntityGuid'))
            waiting.append((entity.get('EntityGuid'), target.get('EntityGuid'), link.get('Name')))

    # The native root smart area supplies world AI services. Build its geometry
    # from the destination bounds, never from Trosecko's navigation coordinates.
    data = ET.fromstring(files['leveldata.xml'])
    info = data.find('LevelInfo')
    terrain = ET.fromstring(files['levelinfo.xml']).find('TerrainInfo')
    size = float(terrain.get('HeightmapSize')) * float(terrain.get('UnitSize'))
    maximum = float(info.get('HeightmapMaxHeight'))
    if not 0 < size <= 65536 or not 0 < maximum <= 10000:
        raise ValueError('Invalid destination terrain bounds')
    land = place('sa_land', 'SmartAreaShape', by_name['sa_land'])
    for child in list(land):
        if child.tag != 'Properties':
            land.remove(child)
    bottom = -50.0
    height = maximum - bottom + 50.0
    land.set('Pos', f'0,0,{bottom}')
    world_ai = register_interrupt_services(source, by_name['sa_land'], land, place, waiting)
    area = ET.SubElement(land, 'Area', Id='0', Group='0', Proximity='0', Priority='0', Height=str(height))
    points = ((0., 0., bottom), (size, 0., bottom), (size, size, bottom), (0., size, bottom))
    point_xml = ET.SubElement(area, 'Points')
    for x, y, _ in points:
        ET.SubElement(point_xml, 'Point', Pos=f'{x},{y},0', ObstructSound='0')
    ET.SubElement(area, 'Roof', ObstructSound='0')
    ET.SubElement(area, 'Floor', ObstructSound='0')
    native_areas = read_areas(read(native_level, 'triggerareas.fubar'))
    export_version = native_areas['export_version']
    old_areas = read_areas(files['triggerareas.fubar'])['areas'] if 'triggerareas.fubar' in files else []
    if any(a.guid == guid_value(land.get('EntityGuid')) for a in old_areas):
        raise ValueError('Root AI area already registered')
    files['triggerareas.fubar'] = write_areas(
        [*old_areas, TriggerArea(guid_value(land.get('EntityGuid')), height, points)], export_version)
    level_info = ET.fromstring(files['levelinfo.xml'])
    export = level_info.find('ExportInfo')
    if export is None:
        export = ET.SubElement(level_info, 'ExportInfo')
    export.set('Version', str(export_version))
    files['levelinfo.xml'] = xml(level_info)

    # Runtime reads compiled scheduler records independently of editor links.
    scheduler = ET.fromstring(files['tables/ai/scheduler.xml'])
    rows = scheduler.find('Schedulers')
    native_rows = ET.fromstring(read(native_level, 'tables/ai/scheduler.xml')).find('Schedulers')
    if rows is None or native_rows is None or rows.attrib != native_rows.attrib:
        raise ValueError('Native scheduler schema mismatch')
    remap = {str(guid_value(by_name[n].get('EntityGuid'))): str(guid_value(e.get('EntityGuid')))
             for n, e in services.items()}
    scheduler_links = 0
    for old, new in remap.items():
        if any(r.get('EntityGuid') == new for r in rows):
            raise ValueError('Duplicate compiled player scheduler')
        row = copy.deepcopy(unique((r for r in native_rows if r.get('EntityGuid') == old), 'native scheduler record'))
        row.set('EntityGuid', new)
        links = row.find('Links')
        for link in list(links) if links is not None else []:
            params = link.find('Parameters')
            if (link.get('TargetGuid') not in remap or params is None or
                    params.get('BehaviorName') not in PLAYER_BEHAVIORS):
                links.remove(link)
                continue
            if link.get('PositioningDelegate', '0') not in {'0', *remap}:
                raise ValueError('Unresolved player scheduler positioning delegate')
            link.set('TargetGuid', remap[link.get('TargetGuid')])
            if link.get('PositioningDelegate', '0') != '0':
                link.set('PositioningDelegate', remap[link.get('PositioningDelegate')])
            scheduler_links += 1
        rows.append(row)
    if scheduler_links != len(PLAYER_BEHAVIORS):
        raise ValueError('Native generic player scheduling links are incomplete')
    files['tables/ai/scheduler.xml'] = xml(scheduler)

    # Keep the return route resident by default. Streaming this small station
    # made dialogue availability depend on an unverified profile lifecycle.
    profile = None
    layer = []
    if station_streaming:
        # Native station objects are streamed as a profile, with an explicit loaded
        # acknowledgement before the travel interaction becomes available.
        profile = level + '_arrival'
        layer_name = profile + '_' + identity('entry/station')
        layer = ET.Element('Objects')
        for entity in list(mission):
            name = entity.get('Name', '')
            if (name.startswith('gmtravel_kcd1_') or name in
                    {'gmtravel_interact_kcd1', 'gmtravel_return_driver', 'gmtravel_return_driver_home'}):
                mission.remove(entity)
                entity.set('Layer', layer_name)
                layer.append(entity)
        if not len(layer) or not any(e.get('EntityClass') == 'NPC' for e in layer):
            raise ValueError('Arrival profile is missing its station/driver')
        layers = data.find('Layers')
        if layers is None:
            layers = ET.SubElement(data, 'Layers')
        if any(e.get('Name') == layer_name for e in layers):
            raise ValueError('Arrival layer already registered')
        layer_id = max((int(e.get('Id')) for e in layers), default=0) + 1
        ET.SubElement(layers, 'Layer', Name=layer_name, Id=str(layer_id), Specs='-1')
        files['leveldata.xml'] = xml(data)
        files['layers/' + layer_name.lower() + '.xml'] = xml(layer)
        reserved = ET.fromstring(files['extractedlayerentityids.xml']) if 'extractedlayerentityids.xml' in files else ET.Element('ReservedEntityIDsFromLayers')
        existing_ids = {e.get('ID') for e in reserved}
        for entity in layer:
            if entity.get('EntityId') in existing_ids:
                raise ValueError('Arrival layer entity ID collision')
            ET.SubElement(reserved, 'ReservedEntityID', ID=entity.get('EntityId'))
        files['extractedlayerentityids.xml'] = xml(reserved)

        meta = ET.fromstring(files['whdata_1']) if 'whdata_1' in files else ET.Element('Root', version='1')
        manager = meta.find('GameProfileManager')
        if manager is None:
            manager = ET.SubElement(meta, 'GameProfileManager')
        profiles = manager.find('GameProfiles')
        if profiles is None:
            profiles = ET.SubElement(manager, 'GameProfiles')
        if any(p.findtext('Name') == profile for p in profiles):
            raise ValueError('Arrival profile already registered')
        profile_id = max((int(p.findtext('Id')) for p in profiles), default=-1) + 1
        record = ET.SubElement(profiles, 'GameProfile')
        for key, value in [('Name', profile), ('Id', str(profile_id)), ('HasNavigation', '0'),
                           ('DoesPrepareSOAnimData', '0'), ('AllowRuntimeAnimValidationFallback', '0'), ('NeedsSVORebuild', '0')]:
            ET.SubElement(record, key).text = value
        ET.SubElement(ET.SubElement(record, 'GameLayers'), 'GameLayer').text = layer_name
        if manager.find('GameProfileGroups') is None:
            ET.SubElement(ET.SubElement(manager, 'GameProfileGroups'), 'Groups')
        files['whdata_1'] = xml(meta)

    preprocessing = ET.fromstring(files['whdata_0'])
    concept = preprocessing.find('ConceptManager')
    if concept is None:
        raise ValueError('Missing destination concept registration')
    modules = concept.find('ConceptModules')
    if modules is None:
        modules = ET.SubElement(concept, 'ConceptModules')
    if any(m.text == level for m in modules):
        raise ValueError('Level module already registered')
    ET.SubElement(modules, 'Module').text = level
    areas = preprocessing.find('AI/SmartAreaManager/SmartAreas')
    if areas is None:
        raise ValueError('Missing native SmartAreaManager registration')
    ET.SubElement(ET.SubElement(areas, 'SmartArea'), 'Guid').text = str(guid_value(land.get('EntityGuid')))
    files['whdata_0'] = xml(preprocessing)
    files['waitinglinks.xml'] = append_links(files.get('waitinglinks.xml'), waiting)
    files['objects_mission0.xml'] = xml(mission)

    root = ET.fromstring(graph)
    project = root.find('Skald/Project')
    if project is None:
        raise ValueError('Expected the existing travel Project')
    definitions = project.find('Definitions')
    if definitions is None:
        definitions = ET.SubElement(project, 'Definitions')
    ET.SubElement(definitions, 'Definition', File='entry/' + level + '.xml')
    ET.SubElement(project.find('Nodes'), level, Name=level)
    trigger = project.find("Nodes/InteractionTriggerNode[@Name='use_cart']")
    if station_streaming and trigger is not None:
        for edge in trigger.findall("Edge[@To='IsActive']"):
            edge.set('From', level + '.ready')
    parent = graph_path.rsplit('/', 1)[0]
    streaming = read(native_scripts, STREAMING_SOURCE)
    helper = ET.fromstring(streaming).find('Skald/Module')
    if helper is None or helper.get('Name') != 'streamprofileshandling' or helper.find('.//ProfileStateTrigger') is None:
        raise ValueError('Native streaming helper changed')
    resources = {graph_path: xml(root), parent + '/entry/' + level + '.xml': entry_graph(level, profile),
                 parent + '/entry/streamprofileshandling.xml': streaming}
    return files, resources, {'level_id': level_id, 'level_module': level, 'profile': profile,
        'profile_entities': len(layer), 'station_streaming': station_streaming, 'layer_activation': True, 'root_ai_area': land.get('EntityGuid'),
        'root_ai_bounds': [0, 0, size, size], 'player_scheduler_links': scheduler_links,
        'world_ai': world_ai,
        'streaming_source': STREAMING_SOURCE, 'streaming_sha256': hashlib.sha256(streaming).hexdigest(),
        'entry_events': ['OnWake', 'OnLevelSwitched'], 'creates_companion': False,
        'changes_ownership': False, 'runtime_verified': False}
