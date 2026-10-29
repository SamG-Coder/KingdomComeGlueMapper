"""Register converted cinematic layers with native profile and area metadata.

Profiles are dormant until a CutsceneHandler requests them. This does not
flatten cinematic actors into the persistent population or auto-start a scene.
"""
import copy
import xml.etree.ElementTree as ET

from campaign_entity_links import append_links, guid_value
from campaign_trigger_areas import TriggerArea, read_areas, write_areas
from upgrade_map import xml


def register(files, scenes, native_profiles, native_areas):
    """scenes contains (converted XML, conversion receipt, source profile name).

    Require navigation-free, single-layer cinematic profiles. Dynamic quest
    profiles with navigation or multiple authored layers need the full profile
    adapter, rather than silently dropping the extra content.
    """
    result = dict(files)
    level = ET.fromstring(files['leveldata.xml'])
    layers = level.find('Layers')
    if layers is None: layers = ET.SubElement(level, 'Layers')
    manager = ET.fromstring(files.get('whdata_1', b'<Root/>'))
    profiles = manager.find('GameProfileManager')
    if profiles is None: profiles = ET.SubElement(manager, 'GameProfileManager')
    records = profiles.find('GameProfiles')
    if records is None: records = ET.SubElement(profiles, 'GameProfiles')
    if profiles.find('GameProfileGroups') is None: ET.SubElement(profiles, 'GameProfileGroups')
    sources = {}
    for p in native_profiles.findall('.//GameProfile'):
        name = p.findtext('Name')
        if name in sources: raise ValueError('Ambiguous source cinematic profile: ' + str(name))
        sources[name] = p
    source_areas = {a.guid:a for a in read_areas(native_areas)['areas']}
    destination = read_areas(files['triggerareas.fubar'])
    areas = {a.guid:a for a in destination['areas']}
    next_layer = max((int(e.get('Id')) for e in layers), default=0)+1
    next_profile = max((int(e.findtext('Id')) for e in records), default=0)+1
    names = {e.findtext('Name') for e in records}
    layer_names = {e.get('Name') for e in layers}
    links, reports = [], []
    for data, receipt, source_name in scenes:
        profile = sources.get(source_name)
        if profile is None: raise ValueError('Missing source cinematic profile: ' + source_name)
        if (profile.findtext('HasNavigation') != '0' or
                len(profile.findall('GameLayers/GameLayer')) != 1 or
                profile.findtext('NeedsSVORebuild') != '0'):
            raise ValueError('Cinematic profile requires navigation or additional layer conversion: ' + source_name)
        grouped = {n.text for n in native_profiles.findall('.//GameProfileGroups//Profiles/Profile')}
        if profile.findtext('Id') in grouped:
            raise ValueError('Cinematic profile belongs to a mutually exclusive group: ' + source_name)
        name = 'gm_' + source_name
        layer = receipt['layer']
        if name in names or layer in layer_names or 'layers/'+layer+'.xml' in result:
            raise ValueError('Cinematic profile already exists: ' + name)
        if next_layer > 65535: raise ValueError('Cinematic layer ID exceeds native range')
        root = ET.fromstring(data)
        guids = {e.get('EntityGuid') for e in root}
        if len(guids) != len(root): raise ValueError('Duplicate converted cinematic entity')
        for entity in root:
            if entity.get('Layer') != layer: raise ValueError('Cinematic entity has the wrong layer')
            for link in entity.findall('EntityLinks/Link'):
                if link.get('TargetGuid') not in set(receipt['entity_guid_map'].values()):
                    raise ValueError('Cinematic layer contains an unbound link')
                links.append((entity.get('EntityGuid'),link.get('TargetGuid'),link.get('Name')))
        profile = copy.deepcopy(profile)
        profile.find('Name').text = name
        profile.find('Id').text = str(next_profile)
        profile.find('GameLayers/GameLayer').text = layer
        records.append(profile)
        ET.SubElement(layers,'Layer',Name=layer,Id=str(next_layer),Specs='-1',Physics='0')
        count = 0
        for source_guid, target_guid in receipt['entity_guid_map'].items():
            if target_guid not in receipt['areas']: continue
            # receipt keys are normalized native GUID text, with its low word
            # first. Restore separators before decoding the binary identity.
            if len(source_guid)>16: raise ValueError('Editor GUID cannot bind a compiled cinematic area')
            text = source_guid[:8]+'-'+source_guid[8:12]+'-'+source_guid[12:]
            source = source_areas.get(int(source_guid,16) if receipt['source_game']=='kcd1' else guid_value(text))
            if source is None: raise ValueError('Missing compiled cinematic area: ' + source_guid)
            target = guid_value(target_guid)
            if target in areas: raise ValueError('Cinematic area identity collision')
            offset = receipt['offset']
            areas[target] = TriggerArea(target, source.height,
                tuple(tuple(v+d for v,d in zip(point,offset)) for point in source.points))
            count += 1
        result['layers/'+layer+'.xml'] = data
        reports.append(dict(source_profile=source_name, profile=name, profile_id=next_profile,
                            layer=layer, layer_id=next_layer, registered_areas=count,
                            runtime_verified=False))
        names.add(name);layer_names.add(layer);next_layer+=1;next_profile+=1
    result['leveldata.xml']=xml(level)
    result['whdata_1']=xml(manager)
    result['triggerareas.fubar']=write_areas(list(areas.values()),destination['export_version'])
    result['waitinglinks.xml']=append_links(files['waitinglinks.xml'],links)
    return result,reports
