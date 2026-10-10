"""Register converted source layers and complete profiles in native level data.

Profile membership is a dependency, not a reason to turn a quest actor into a
permanent resident. Only profiles whose complete layers were supplied are emitted.
"""
import copy
import re
import xml.etree.ElementTree as ET

from campaign_entity_links import guid_value
from upgrade_map import xml


def layer_name(source):
    match = re.fullmatch(r'(.+)\{([0-9a-fA-F-]{36})\}', source)
    if not match: raise ValueError('Invalid source layer name: ' + source)
    return 'kcd1_' + match[1] + '_' + match[2].lower()


def register_layers(files, source_level, source_profiles, converted_layers):
    """converted_layers maps source layer names to fully converted Objects XML.

    Caller must supply all entities/dependencies in a layer, not just its NPCs.
    Missing profile members and navigation-dependent profiles remain explicit.
    """
    files = dict(files)
    level = ET.fromstring(files['leveldata.xml'])
    layers = level.find('Layers')
    if layers is None: layers = ET.SubElement(level, 'Layers')
    known = {e.get('Name').lower() for e in layers}
    next_layer = max((int(e.get('Id')) for e in layers), default=0) + 1
    reserved = ET.fromstring(files.get('extractedlayerentityids.xml', b'<ReservedEntityIDsFromLayers/>'))
    used_ids = {e.get('ID') for e in reserved}
    used_guids = set()
    for name, blob in files.items():
        if name == 'objects_mission0.xml' or name.lower().startswith('layers/') and name.endswith('.xml'):
            for e in ET.fromstring(blob).iter('Entity'):
                if e.get('EntityId'): used_ids.add(e.get('EntityId'))
                if e.get('EntityGuid'): used_guids.add(guid_value(e.get('EntityGuid')))
    source_layers = {e.get('Name').lower(): e for e in source_level.findall('Layers/Layer')}
    names = {}
    for old, document in sorted(converted_layers.items()):
        definition = source_layers[old.lower()]
        name = layer_name(definition.get('Name'))
        if name.lower() in known: raise ValueError('Layer is already registered: ' + name)
        root = ET.fromstring(document)
        if root.tag != 'Objects': raise ValueError('Layer has no Objects container')
        for e in root.iter('Entity'):
            identity, guid = e.get('EntityId'), guid_value(e.get('EntityGuid'))
            if not identity or identity in used_ids or guid in used_guids:
                raise ValueError('Conditional entity identity collision: ' + str(e.attrib))
            used_ids.add(identity); used_guids.add(guid)
            ET.SubElement(reserved, 'ReservedEntityID', ID=identity)
            e.set('Layer', name)
            e.set('EditorLayer', 'KCD1/' + name)
        attributes = dict(Name=name, Id=str(next_layer), Specs=definition.get('Specs', '-1'))
        if 'Physics' in definition.attrib: attributes['Physics'] = definition.get('Physics')
        ET.SubElement(layers, 'Layer', attributes)
        next_layer += 1; known.add(name.lower()); names[old.lower()] = name
        files['layers/' + name.lower() + '.xml'] = xml(root)
    meta = ET.fromstring(files.get('whdata_1', b'<Root version="1"/>'))
    manager = meta.find('GameProfileManager')
    if manager is None: manager = ET.SubElement(meta, 'GameProfileManager')
    profiles = manager.find('GameProfiles')
    if profiles is None: profiles = ET.SubElement(manager, 'GameProfiles')
    existing_names = {p.findtext('Name').lower() for p in profiles}
    next_profile = max((int(p.findtext('Id')) for p in profiles), default=-1) + 1
    mapping, ids, pending = {}, {}, []
    for old in source_profiles.findall('.//GameProfiles/GameProfile'):
        name = old.findtext('Name')
        members = [r.text for r in old.findall('GameLayers/GameLayer')]
        missing = [n for n in members if n.lower() not in names]
        if missing or old.findtext('HasNavigation', '0') != '0' or old.findtext('NeedsSVORebuild', '0') != '0':
            pending.append(dict(profile=name, missing_layers=missing,
                                navigation=old.findtext('HasNavigation', '0'), svo=old.findtext('NeedsSVORebuild', '0')))
            continue
        # Both retail games serialize empty profiles. Keep them so their script
        # operations and group membership still refer to a registered profile.
        target = 'kcd1_' + name
        if target.lower() in existing_names: raise ValueError('Profile already registered: ' + target)
        record = ET.SubElement(profiles, 'GameProfile')
        for tag, value in [('Name', target), ('Id', str(next_profile)), ('HasNavigation', '0'),
                           ('DoesPrepareSOAnimData', '0'), ('AllowRuntimeAnimValidationFallback', '0'), ('NeedsSVORebuild', '0')]:
            ET.SubElement(record, tag).text = value
        members_node = ET.SubElement(record, 'GameLayers')
        for member in members: ET.SubElement(members_node, 'GameLayer').text = names[member.lower()]
        mapping[name] = target; ids[old.findtext('Id')] = str(next_profile)
        next_profile += 1; existing_names.add(target.lower())
    holder = manager.find('GameProfileGroups')
    if holder is None: holder = ET.SubElement(manager, 'GameProfileGroups')
    groups = holder.find('Groups')
    if groups is None: groups = ET.SubElement(holder, 'Groups')
    next_group = max((int(g.findtext('Id')) for g in groups), default=-1) + 1
    for old in source_profiles.findall('.//GameProfileGroups/Groups/Group'):
        members = [p.text for p in old.findall('Profiles/Profile')]
        if not members or any(i not in ids for i in members): continue
        target = copy.deepcopy(old)
        target.find('Name').text = 'kcd1_' + old.findtext('Name')
        target.find('Id').text = str(next_group); next_group += 1
        for p in target.findall('Profiles/Profile'): p.text = ids[p.text]
        groups.append(target)
    files.update({'leveldata.xml': xml(level), 'whdata_1': xml(meta), 'extractedlayerentityids.xml': xml(reserved)})
    return files, dict(layers=names, profiles=mapping, profile_ids=ids, pending_profiles=pending,
                       activation='source script operations only', runtime_verified=False)
