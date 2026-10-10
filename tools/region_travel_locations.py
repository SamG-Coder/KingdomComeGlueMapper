"""Import a destination's own discovery records, location areas and cartography.

The map texture alone does not register RPG locations. KCD1 exports location
areas by transient WUID; KCD2 expects persistent entity GUIDs. No native region
records or saved discoveries are replaced by this additive conversion.
"""
import copy
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_dependency_adapters import SourceTables
from campaign_entity_links import guid_value, native_guid
from campaign_sources import RetailSourceReader
from campaign_trigger_areas import TriggerArea, read_areas, write_areas
from region_travel_services import native_xml
from upgrade_map import read, xml


def merge_missing_strings(base, additions):
    from region_travel_dialogue import merge_localization
    existing = {r.findtext('Cell') for r in ET.fromstring(base).findall('Row')}
    extra = ET.fromstring(additions)
    incoming = {}
    for row in list(extra):
        key = row.findtext('Cell')
        value = tuple(c.text for c in row.findall('Cell'))
        if key in incoming and incoming[key] != value:
            raise ValueError('Conflicting incoming localization: ' + str(key))
        if key in existing or key in incoming: extra.remove(row)
        incoming[key] = value
    return merge_localization(base, xml(extra))


def patch_table(container, row_name, rows):
    root = ET.Element('database', name='barbora')
    table = ET.SubElement(root, container, version='1')
    for row in rows:
        ET.SubElement(table, row_name, row)
    return xml(root)


def local_map(row, size, pixels, side):
    """Decode old world-space/top-left extents to native map-image pixels.

    KCD1's stored height is a Y coordinate (size minus the actual height).
    Its activation ellipse uses two foci and the full major-axis length.
    Native tables store a top-left rectangle enclosing that ellipse.
    """
    scale = pixels / size
    f1 = tuple(float(row['focus1_' + k]) for k in 'xy')
    f2 = tuple(float(row['focus2_' + k]) for k in 'xy')
    major = float(row['major_axis'])
    focal = math.dist(f1, f2)
    if major < focal or (abs(f1[0]-f2[0]) > .05 and abs(f1[1]-f2[1]) > .05):
        raise ValueError('Unsupported rotated/invalid source activation ellipse')
    minor = math.sqrt(max(0, major*major-focal*focal))
    width, height = (major, minor) if abs(f1[0]-f2[0]) >= abs(f1[1]-f2[1]) else (minor, major)
    center = ((f1[0]+f2[0])/2, size-(f1[1]+f2[1])/2)
    image_height = size-float(row['height'])
    if image_height <= 0 or float(row['width']) <= 0:
        raise ValueError('Invalid source local-map extent')
    result = {k: row[k] for k in ('ui_local_map_id', 'location_id')}
    result.update(ui_local_map_name='gmtravel_'+row['ui_local_map_name'],
        position_x=f"{float(row['position_x'])*scale:g}",
        position_y=f"{(size-float(row['position_y']))*scale:g}",
        width=f"{float(row['width'])*scale:g}", height=f'{image_height*scale:g}',
        active_x=f'{(center[0]-width/2)*scale:g}', active_y=f'{(center[1]-height/2)*scale:g}',
        active_width=f'{width*scale:g}', active_height=f'{height*scale:g}',
        active_area_ellipse='true', tile_width=str(side), tile_height=str(side),
        enabled_by_default=row['enabled_by_default'].lower(), enabled_only_when_inside='false')
    return result


def convert_mark(mark, source_types, native_types):
    result = copy.deepcopy(mark)
    poi = mark.findtext('POITypeId')
    if not poi and mark.findtext('MarkType') == '-1':
        # Location-only discovery shapes have no rendered POI icon.
        return result
    if not poi:
        candidates = [r for r in source_types.values() if r.get('mark_type') == mark.findtext('MarkType')]
        if len(candidates) != 1:
            raise ValueError('Ambiguous legacy mark type: '+str(mark.findtext('MarkType')))
        poi = candidates[0]['poi_type_id']
        ET.SubElement(result, 'POITypeId').text = poi
    target = native_types[poi]
    # Explicit enum values moved between engines (notably fast travel 5 -> 7).
    result.find('MarkType').text = target.get('mark_type', '-1') if mark.findtext('MarkType') != '-1' else '-1'
    layer = result.find('LayerName')
    if layer is not None and layer.text.lower().startswith('fast_travel_network{'):
        result.remove(layer)  # the source's permanent network is resident here
    return result


def register_areas(files, source_wh, source_mission, manager):
    files = dict(files)
    wh = ET.fromstring(files['whdata_0'])
    if wh.find('RPGLocationManager') is not None:
        raise ValueError('Destination already has RPG locations; explicit merge required')
    mission = ET.fromstring(files['objects_mission0.xml'])
    entities = {int(e.get('EntityGuid'), 16): e for e in source_mission.iter('Entity') if e.get('EntityGuid')}
    area_guids = {a.findtext('AreaId'): int(a.findtext('Guid'))
                  for a in source_wh.findall('AI/SmartAreaManager/SmartAreas/SmartArea')}
    next_id = max(int(e.get('EntityId', '0')) for e in mission.iter('Entity'))+1
    used = {e.get('EntityGuid') for e in mission.iter('Entity')}
    areas = read_areas(files['triggerareas.fubar'])
    used_areas = {a.guid for a in areas['areas']}
    ai = wh.find('AI/SmartAreaManager/SmartAreas')
    if ai is None:
        raise ValueError('Native SmartAreaManager is required')
    memberships = ET.SubElement(manager, 'SmartAreas')
    from region_travel import rotate
    for membership in source_wh.findall('RPGLocationManager/SmartAreas/SmartArea'):
        number = area_guids[membership.findtext('wuid')]
        original = entities[number]
        guid = native_guid(number)
        if guid in used or number in used_areas:
            raise ValueError('Location area GUID collision: '+guid)
        used.add(guid); used_areas.add(number)
        shape = original.find('Area')
        if shape is None:
            raise ValueError('Source location area has no polygon: '+original.get('Name'))
        entity = ET.SubElement(mission, 'Entity', Name='gmtravel_location_'+original.get('Name'),
            EntityClass='SmartAreaShape', EntityId=str(next_id), EntityGuid=guid,
            **{k: original.get(k) for k in ('Pos', 'Rotate', 'Scale') if original.get(k)})
        next_id += 1
        props = ET.SubElement(entity, 'Properties', guidLocationId=membership.findtext('Location'), bSaved_by_game='0')
        ET.SubElement(props, 'Script', bValidWithoutTemplate='1')
        entity.append(copy.deepcopy(shape))
        origin = tuple(map(float, original.get('Pos').split(',')))
        rotation = tuple(map(float, original.get('Rotate', '1,0,0,0').split(',')))
        scale = tuple(map(float, original.get('Scale', '1,1,1').split(',')))
        points = []
        for p in shape.findall('Points/Point'):
            local = tuple(a*b for a,b in zip(map(float, p.get('Pos').split(',')), scale))
            points.append(tuple(a+b for a,b in zip(origin, rotate(rotation, local))))
        areas['areas'].append(TriggerArea(number, float(shape.get('Height'))*scale[2], tuple(points)))
        ET.SubElement(ET.SubElement(ai, 'SmartArea'), 'Guid').text = str(number)
        record = ET.SubElement(memberships, 'SmartArea')
        ET.SubElement(record, 'guid').text = str(number)
        ET.SubElement(record, 'Location').text = membership.findtext('Location')
    wh.append(manager)
    files.update({'whdata_0': xml(wh), 'objects_mission0.xml': xml(mission),
                  'triggerareas.fubar': write_areas(areas['areas'], areas['export_version'])})
    return files


def resources(source, target, files, level_id, *, source_level='rataje', modid='gluemappertravel'):
    source, target = Path(source), Path(target)
    with RetailSourceReader(source) as reader:
        tables = SourceTables(source, reader)
        old_locations = tables.get('rpg/location')['rows']
        labels = tables.get('ui/ui_map_label')['rows']
        local_maps = tables.get('ui/ui_local_maps')['rows']
        old_types = {r['poi_type_id']: r for r in tables.get('rpg/poi_type')['rows']}
    with zipfile.ZipFile(source/'Data/Levels'/source_level/'level.pak') as z:
        source_wh = ET.fromstring(read(z, 'whdata_0'))
        source_mission = ET.fromstring(read(z, 'objects_mission0.xml'))
        wanted_wuids = {a.findtext('wuid') for a in source_wh.findall('RPGLocationManager/SmartAreas/SmartArea')}
        wanted_guids = {int(a.findtext('Guid')) for a in source_wh.findall('AI/SmartAreaManager/SmartAreas/SmartArea')
                        if a.findtext('AreaId') in wanted_wuids}
        present = {int(e.get('EntityGuid'),16) for e in source_mission.iter('Entity') if e.get('EntityGuid')}
        for name in z.namelist():
            if not name.lower().startswith('layers/') or not name.lower().endswith('.xml'): continue
            if wanted_guids <= present: break
            blob = read(z,name)
            if not any(f'{g:X}'.encode() in blob for g in wanted_guids-present): continue
            for entity in ET.fromstring(blob).iter('Entity'):
                number = int(entity.get('EntityGuid','0'),16)
                if number in wanted_guids-present:
                    source_mission.append(copy.deepcopy(entity));present.add(number)
        terrain = ET.fromstring(read(z, 'levelinfo.xml')).find('TerrainInfo')
        size = float(terrain.get('HeightmapSize'))*float(terrain.get('UnitSize'))
    native_types, native_locations = {}, {}
    with zipfile.ZipFile(target/'Data/Tables.pak') as z:
        for name in z.namelist():
            if re.fullmatch(r'Libs/Tables/rpg/poi_type(?:__[^/]+)?\.xml', name, re.I):
                native_types.update({r.get('poi_type_id'): dict(r.attrib) for r in native_xml(read(z,name))[0]})
            if re.fullmatch(r'Libs/Tables/rpg/location(?:__[^/]+)?\.xml', name, re.I):
                native_locations.update({r.get('location_id'): dict(r.attrib) for r in native_xml(read(z,name))[0]})
    manager = ET.Element('RPGLocationManager')
    # The shipped source export retains two editor areas whose location rows
    # were deleted. They are not locations we can register in the target DB.
    known_locations = {r['location_id'] for r in old_locations}
    orphan_areas = []
    source_areas = source_wh.find('RPGLocationManager/SmartAreas')
    for area in list(source_areas):
        if area.findtext('Location') not in known_locations:
            orphan_areas.append(dict(wuid=area.findtext('wuid'), location=area.findtext('Location')))
            source_areas.remove(area)
    marks = ET.SubElement(manager, 'MarkIdMap')
    new_types, generic_icons = {}, []
    source_marks = source_wh.findall('RPGLocationManager/MarkIdMap/Mark')
    # Import only referenced source types; never overwrite a native shared type.
    used_types = {m.findtext('POITypeId') for m in source_marks if m.findtext('POITypeId')}
    used_types.update(r['poi_type_id'] for r in old_types.values()
                      if r.get('mark_type') in {m.findtext('MarkType') for m in source_marks if not m.findtext('POITypeId')})
    general = next(r for r in native_types.values() if r.get('label') == 'ui_maplegend_general_poi')
    for id in sorted(used_types-native_types.keys()):
        old = old_types[id]
        match = next((r for r in native_types.values() if r.get('label') == old.get('label')), None)
        if match is None:
            match = general; generic_icons.append(old['label'])
        row = dict(poi_type_id=id, poi_type_name='gmtravel_'+id.replace('-',''),
            label=old['label'], mark_type=match.get('mark_type','-1'), discovery_dist=old['discovery_dist'],
            discovery_msg_mode='DoNotShow' if old['discovery_msg_mode']=='2' else 'Normal',
            discoverable_by_location=old['discoverable_by_location'].lower(), ui_order=old['ui_order'])
        new_types[id]=row
    native_types.update(new_types)
    for mark in source_marks:
        marks.append(convert_mark(mark, old_types, native_types))
    ids = {m.findtext('LocationId') for m in marks if m.findtext('LocationId')}
    ids.update(a.findtext('Location') for a in source_wh.findall('RPGLocationManager/SmartAreas/SmartArea'))
    ids.update(r['location_id'] for r in labels+local_maps)
    locations = []
    for row in old_locations:
        id = row['location_id']
        if id not in ids: continue
        if id in native_locations:
            raise ValueError('Refusing to reassign a native location: '+id)
        converted = dict(location_id=id, location_name=row['location_name'], level_id=str(level_id), ui_type='Hidden')
        if id in {label['location_id'] for label in labels}:
            converted.update(ui_type='Common', icon_id='village')
        if row['location_category_id']: converted['location_category_id']=row['location_category_id']
        locations.append(converted)
    if ids != {r['location_id'] for r in locations}:
        raise ValueError('Unresolved source map locations')
    blobs = {}
    local_rows = []
    with zipfile.ZipFile(source/'Data/GameData.pak') as z, RetailSourceReader(source) as reader:
        entries = z.namelist()
        global_tiles = [n for n in entries if re.fullmatch(r'Libs/UI/Textures/Maps/globalMap_[1-9]\d*\.dds', n)]
        # Runtime map coordinates are in the global atlas's image pixels.
        import struct
        tile_width = struct.unpack_from('<I', read(z, global_tiles[0]), 16)[0]
        pixels = math.isqrt(len(global_tiles))*tile_width
        for old in local_maps:
            name = old['ui_local_map_name']
            pattern = re.compile(r'Libs/UI/Textures/Maps/'+re.escape(name)+r'_(\d+)\.dds(?:\.(?:\d+a?|a))?$', re.I)
            found = [n for n in entries if pattern.fullmatch(n)]
            tiles = sorted(int(pattern.fullmatch(n)[1]) for n in found if n.lower().endswith('.dds'))
            side = math.isqrt(len(tiles)-1)
            if not side or tiles != list(range(side*side+1)):
                raise ValueError('Incomplete source local-map tiles: '+name)
            row = local_map(old, size, pixels, side)
            # Several campaigns share a location. Enable the most specific
            # authored inset; keep larger alternate-campaign art registered but
            # inactive rather than placing two paintings over the same village.
            alternatives = [r for r in local_maps if r['location_id']==old['location_id'] and r['enabled_by_default']=='True']
            if old['enabled_by_default']=='True' and alternatives:
                chosen = min(alternatives, key=lambda r:float(r['width'])*(size-float(r['height'])))
                if chosen['ui_local_map_id'] != old['ui_local_map_id']: row['enabled_by_default']='false'
            local_rows.append(row)
            for entry in found:
                target_name = entry.replace('/'+name+'_', '/gmtravel_'+name+'_')
                blobs[target_name] = reader(source, {'texture': ('GameData.pak', entry)})['texture']['data']
    labels = [dict(r, position_x=f"{float(r['position_x'])*pixels/size:g}",
                      position_y=f"{(size-float(r['position_y']))*pixels/size:g}") for r in labels]
    for path,container,row_name,rows in [('rpg/location','locations','location',locations),
        ('rpg/poi_type','poi_types','poi_type',list(new_types.values())),
        ('ui/ui_map_label','ui_map_labels','ui_map_label',labels),
        ('ui/ui_local_maps','ui_local_mapss','ui_local_maps',local_rows)]:
        if rows: blobs['Libs/Tables/'+path+'__'+modid+'.xml']=patch_table(container,row_name,rows)
    # Source-only labels are added to the corresponding native localization file.
    wanted={r['location_name'] for r in locations}|{r['ui_name'] for r in labels}|{r['label'] for r in new_types.values()}
    localized={}
    with zipfile.ZipFile(source/'Localization/English_xml.pak') as z:
        for name in z.namelist():
            if not name.lower().endswith('.xml'):continue
            root=ET.fromstring(read(z,name))
            rows=[copy.deepcopy(r) for r in root.findall('Row') if r.findtext('Cell') in wanted]
            if rows:
                table=ET.Element('Table');table.extend(rows);localized[name]=xml(table)
    registered=register_areas(files,source_wh,source_mission,manager)
    return registered,blobs,localized,dict(locations=len(locations),marks=len(marks),
        location_areas=len(manager.find('SmartAreas')),labels=len(labels),local_maps=len(local_rows),
        atlas_pixels=pixels,source_extent=size,new_poi_types=len(new_types),generic_icon_labels=generic_icons,
        unresolved_source_areas=orphan_areas,
        gated_marks=sum(m.find('LayerName') is not None for m in marks),runtime_verified=False)
