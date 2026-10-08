"""Assemble imported dependency identities into native quest/world bindings.

This stage only registers outputs which the importers actually produced. It
does not activate source profiles or simulate the missing campaign program.
The resulting world fragment can be merged with a converted level later.
"""
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET

from campaign_bindings import emit_assets, native_links, registration_errors
from campaign_entity_links import append_links, guid_value, native_guid
from campaign_quest_graph import identifier, xml
from campaign_trigger_areas import read_areas, write_areas
from skald_state_probe import project_entities


def payload(value):
    return value.read_bytes() if isinstance(value, Path) else value


def assemble_registrations(project, quest_files, bindings, registered, imported_files, export_version):
    """Link successful imports individually; retain exact unresolved call sites."""
    identifier(project)
    root = ET.fromstring(quest_files[project + '.xml'])
    host = root.find('./Skald/Project')
    if host is None or host.get('Name') != project:
        raise ValueError('Quest project identity mismatch')
    if host.find('Assets') is not None:
        raise ValueError('Project already has an asset registration section')
    assets = ET.SubElement(host, 'Assets')
    objects = ET.fromstring(project_entities(project, 'Quests/' + project + '.xml'))
    for entity in objects:
        # Stable native 64-bit identities; do not reinterpret a UUID's first
        # textual groups as a native low32-mid16-high16 identifier.
        identity = int.from_bytes(hashlib.sha256((project + '/' + entity.get('Name')).encode()).digest()[:8], 'little') or 1
        entity.set('EntityGuid', native_guid(identity))
        entity.attrib.pop('EntityId', None)
    holders = [e for e in objects if e.get('EntityClass') == 'SmartObjectHolder']
    if len(holders) != 1:
        raise ValueError('Expected one native project holder')
    holder = holders[0]
    souls = ET.Element('Root', version='1')
    soul_list = ET.SubElement(ET.SubElement(souls, 'SoulList'), 'Souls')
    shapes, entities, profiles = {}, {}, {}
    for name, value in sorted(imported_files.items()):
        if name.startswith('world/entities/'):
            entity = ET.fromstring(payload(value))
            guid = entity.get('EntityGuid')
            guid_value(guid)
            if guid in entities:
                raise ValueError('Duplicate imported entity identity: ' + guid)
            entities[guid] = entity
            profiles.setdefault(entity.get('EditorLayer', ''), []).append(guid)
            objects.append(entity)
        elif name.startswith('world/souls/'):
            soul_list.append(ET.fromstring(payload(value)))
        elif name.startswith('world/triggerareas/'):
            data = read_areas(payload(value))
            if data['export_version'] != export_version:
                raise ValueError('Trigger export version differs from the world fragment')
            for area in data['areas']:
                if area.guid in shapes:
                    raise ValueError('Duplicate imported area identity')
                shapes[area.guid] = area
    for number in shapes:
        if native_guid(number) not in entities:
            raise ValueError('Imported trigger geometry has no placed entity')
    links, resolved, pending = [], [], []
    for name, plan in sorted(bindings.items()):
        # Binding issues on one asset must not discard unrelated valid imports.
        # Whole-campaign readiness still includes every original plan issue.
        for binding in plan['bindings']:
            one = dict(plan, bindings=[binding], unresolved=[])
            errors = registration_errors(one, registered)
            reg = binding['registration']
            if not binding.get('asset_type'):
                errors.append(dict(kind='unsupported_asset_type', source_class=reg.get('source_class')))
            if reg['kind'] in ('entity', 'placed_soul'):
                target = registered.get('entities', {}).get(reg['guid'].lower(), reg['guid'])
                if target not in entities:
                    errors.append(dict(kind='missing_placement_output', identity=target))
            if errors:
                pending.append(dict(quest=name, asset=binding['name'], errors=errors))
                continue
            emitted = ET.fromstring(emit_assets(one, registered))
            assets.extend(emitted)
            for source, target, definition in native_links(one, holder.get('EntityGuid')):
                links.append((source, registered['entities'][target.lower()], definition))
            resolved.append(dict(quest=name, asset=binding['name'], type=binding['asset_type']))
    # Allocate IDs only in this fragment. A merger must reallocate against the
    # destination world; links bind persistent GUIDs and remain valid then.
    by_guid = {}
    for index, entity in enumerate(objects, 1):
        if entity.get('EntityGuid') in by_guid:
            raise ValueError('Project/imported entity GUID collision')
        entity.set('EntityId', str(index))
        by_guid[entity.get('EntityGuid')] = entity
    cry_links = ET.SubElement(holder, 'EntityLinks')
    for source, target, definition in links:
        ET.SubElement(cry_links, 'Link', TargetId=by_guid[target].get('EntityId'), TargetGuid=target, Name=definition)
    updated = dict(quest_files)
    updated[project + '.xml'] = xml(root)
    files = {'world/registration/objects_mission0.xml': xml(objects),
             'world/registration/waitinglinks.xml': append_links(None, links),
             'world/registration/whdata_0': xml(souls),
             'world/registration/triggerareas.fubar': write_areas(shapes.values(), export_version)}
    report = dict(schema=1, project=project, assets=resolved, pending=pending,
                  entities=len(entities), areas=len(shapes), native_links=len(links),
                  source_profiles=profiles, export_version=export_version,
                  profile_activation_converted=False, runtime_validated=False)
    return updated, files, report
