"""Convert KCD1 authored vault and climb points into the KCD2 level format.

KCD2 stores authored fence and wall vaulting data in two coupled pieces: the
`whdata_1` archive member (a `<Root version="1">` carrying a `LedgeManager`
plus a `GameProfileManager`) and the `LedgeObject` layer entities whose
`EntityId` the nonzero `Ledge.entityId` records reference. The converted
level shipped neither. These records alone have not enabled runtime vaulting
in the reported tests (issue #5).

KCD1's `rataje/level.pak` stores the same `LedgeManager` schema (3,589
ledges, 30,140 markers, world-space coordinates the converter keeps 1:1).
KCD1's authored `LedgeObject` entities are present in the retail mission
and layer XML. Their transforms and Properties must be retained: a linked
record's first marker is not a replacement for its entity origin.

The converter therefore:
- copies the whole LedgeManager verbatim, re-numbering the 228 linked
  records to fresh ids reserved against the converted level's own entities;
- copies each linked object's authored transform and Properties, with
  reserved EntityId/EntityGuid, into `layers/ledges_import.xml`,
  registered in leveldata.xml and extractedlayerentityids.xml, matching
  the native layer pattern used by region_travel_entry;
- drops the KCD1-only WeatherProfileManager and emits the minimal empty
  GameProfileManager in the native child order.

The entityId=0 world-ledge bucket is preserved unchanged. Source layer
activation has not been converted. Registering this output layer does not
load it in game; state-aware activation and runtime vaulting remain pending.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import native_guid
from game_paths import GameLibrary
from upgrade_map import read, xml


SOURCE = "KingdomComeDeliverance/Data/Levels/rataje/level.pak"
LAYER = "ledges_import"


def _parse_whdata(blob):
    root = ET.fromstring(blob)
    if root.tag != "Root" or root.get("version", "1") != "1":
        raise ValueError("Expected a version 1 whdata Root")
    return root


def _child_text(record, name):
    node = record.find(name)
    if node is None or node.text is None:
        raise ValueError("Ledge record is missing " + name)
    return node


def read_ledges(blob):
    """Return the LedgeManager element of a whdata_1 blob after validating it."""
    manager = _parse_whdata(blob).find("LedgeManager")
    if manager is None:
        raise ValueError("whdata_1 has no LedgeManager")
    ledges = manager.find("Ledges")
    markers = manager.find("Markers")
    if ledges is None or markers is None:
        raise ValueError("LedgeManager must carry Ledges and Markers")
    records, points = list(ledges), list(markers)
    if not records or len(points) < 2:
        raise ValueError("LedgeManager must carry ledges and at least two markers")
    used = 0
    for record in records:
        if int(_child_text(record, "entityId").text) < 0:
            raise ValueError('Ledge entity ID must not be negative')
        start = int(_child_text(record, "markersStartIdx").text)
        count = int(_child_text(record, "markersCount").text)
        if count < 2 or start < 0 or start != used or start + count > len(points):
            raise ValueError("Ledge markers are not contiguous and gap-free")
        for position in (start, start + count - 1):
            node = points[position].find("endOrCorner")
            if node is None or node.text != "1":
                raise ValueError("Ledge end markers must set endOrCorner")
        used += count
    if used != len(points):
        raise ValueError("Ledge marker ranges do not cover the marker list")
    return manager


def read_source_entities(archive, blob):
    """Resolve linked objects from retail XML, retaining their source ownership."""
    required = {int(record.findtext('entityId'))
                for record in read_ledges(blob).find('Ledges')}
    required.discard(0)
    names = {}
    for name in archive.namelist():
        normalized = name.replace('\\', '/').lower()
        if normalized in names:
            raise ValueError('Duplicate source level package member: ' + name)
        names[normalized] = name
    entities, origins = {}, {}
    for normalized, name in sorted(names.items()):
        if not (normalized == 'objects_mission0.xml'
                or normalized.startswith('layers/') and normalized.endswith('.xml')):
            continue
        for entity in ET.fromstring(read(archive, name)).iter('Entity'):
            if entity.get('EntityClass') != 'LedgeObject':
                continue
            ident = int(entity.get('EntityId', '0'))
            if ident not in required:
                continue
            if ident in entities:
                raise ValueError('Duplicate authored LedgeObject ID: ' + str(ident))
            entities[ident] = entity
            origins[ident] = normalized
    missing = sorted(required - entities.keys())
    if missing:
        raise ValueError('Missing authored LedgeObject IDs: ' + ', '.join(map(str, missing)))
    return entities, origins


def _authored_object(entity, ident):
    if (entity is None or entity.tag != 'Entity'
            or entity.get('EntityClass') != 'LedgeObject'
            or entity.get('EntityId') != str(ident)):
        raise ValueError('Missing or mismatched authored LedgeObject: ' + str(ident))
    if entity.get('ParentId', '0') not in ('', '0'):
        raise ValueError('Parented LedgeObject needs a resolved world transform: ' + str(ident))
    for field, count in (('Pos', 3), ('Rotate', 4), ('Scale', 3)):
        value = entity.get(field)
        if value is None and field != 'Pos':
            continue
        try:
            values = [float(n) for n in (value or '').split(',')]
        except ValueError:
            values = []
        if len(values) != count or not all(math.isfinite(n) for n in values):
            raise ValueError('Invalid authored LedgeObject ' + field + ': ' + str(ident))
    if entity.find('Properties') is None:
        raise ValueError('Authored LedgeObject has no Properties: ' + str(ident))
    return entity


def plan(blob, next_id, layer=LAYER, *, existing_ids=None, source_entities=None):
    """Return (whdata_1 bytes, entities, id_map, report) for a KCD1 whdata_1.

    Every record and marker is preserved verbatim except the entityId of the
    linked ledges, which is re-numbered to fresh ids (next_id upward) that the
    converted LedgeObject entities use. Their authored transforms and
    Properties are required; missing metadata is never replaced with guesses.
    The world bucket (entityId 0) and all flag values are copied unchanged.
    """
    if not isinstance(next_id, int) or isinstance(next_id, bool) or next_id < 1:
        raise ValueError('Expected a positive next entity ID')
    existing_ids = existing_ids or {}
    source_entities = source_entities or {}
    source = _parse_whdata(blob)
    manager = read_ledges(blob)
    ledges, markers = manager.find("Ledges"), manager.find("Markers")
    id_map = {}
    entities = []
    for record in ledges:
        old = int(_child_text(record, "entityId").text)
        if not old:
            continue
        if old not in id_map:
            authored = _authored_object(source_entities.get(old), old)
            guid = native_guid(int.from_bytes(hashlib.sha256(
                ('ledge-import/' + str(old)).encode()).digest()[:8], 'little'))
            ident = existing_ids.get(old, next_id)
            if old not in existing_ids:
                next_id += 1
            entity = ET.Element('Entity', Name='gm_ledge_' + str(old),
                                EntityClass='LedgeObject', EntityId=str(ident),
                                EntityGuid=guid,
                                CastShadowMinSpec='1', Layer=layer)
            for field in ('Pos', 'Rotate', 'Scale', 'CastShadowMinSpec'):
                if field in authored.attrib:
                    entity.set(field, authored.get(field))
            entity.append(copy.deepcopy(authored.find('Properties')))
            entities.append(entity)
            id_map[old] = ident
        record.find("entityId").text = str(id_map[old])
    root = ET.Element("Root", version="1")
    converted = ET.SubElement(root, "LedgeManager")
    converted.append(ledges)
    converted.append(markers)
    profiles = ET.SubElement(root, "GameProfileManager")
    ET.SubElement(profiles, "GameProfiles")
    ET.SubElement(ET.SubElement(profiles, "GameProfileGroups"), "Groups")
    report = dict(
        ledges=len(ledges), markers=len(markers),
        world_ledges=sum(1 for r in ledges if int(r.findtext("entityId")) == 0),
        entity_linked=len(entities), entity_ids={str(old): new for old, new in id_map.items()},
        authored_entities_preserved=len(entities),
        weather_profile_manager_dropped=source.find("WeatherProfileManager") is not None,
        game_profiles_emitted=0,
        runtime_verified=False,
        status="Structural conversion verified; vaulting behavior must be checked in game",
        limitations=[
            "Authored source layer activation is not converted; output-layer registration alone does not load its entities",
            "KCD1 WeatherProfileManager is not converted",
            "GameProfileManager is emitted empty; region arrival profiles are added later",
            "Runtime vault, window and flip behavior requires an in-game check",
        ])
    return xml(root), entities, id_map, report


def convert(blob, next_id, *, source_entities=None):
    """Return only (whdata_1 bytes, report); linked ledges need authored objects."""
    result, _, _, report = plan(blob, next_id, source_entities=source_entities)
    return result, report


def _layer_xml(entities, layer=LAYER):
    root = ET.Element('Objects')
    for entity in entities:
        root.append(entity)
    return xml(root)


def _reserved_xml(entities, blob=None, *, replaced_ids=()):
    root = ET.fromstring(blob) if blob is not None else ET.Element('ReservedEntityIDsFromLayers')
    if root.tag != 'ReservedEntityIDsFromLayers':
        raise ValueError('Invalid reserved layer entity IDs root')
    for row in list(root):
        if row.get('ID') in replaced_ids:
            root.remove(row)
    ids = {row.get('ID') for row in root}
    for entity in entities:
        ident = entity.get('EntityId')
        if ident in ids:
            raise ValueError('Ledge entity ID is already reserved')
        ET.SubElement(root, 'ReservedEntityID', ID=ident)
        ids.add(ident)
    return xml(root)


def _leveldata_xml(blob, layer=LAYER, *, replace=False):
    data = ET.fromstring(blob)
    layers = data.find('Layers')
    if layers is None:
        raise ValueError('leveldata.xml has no Layers container')
    existing = [e for e in layers if e.get('Name') == layer]
    if existing:
        if not replace or len(existing) != 1:
            raise ValueError('Ledge layer already registered')
        return blob
    layer_id = max((int(e.get('Id')) for e in layers), default=0) + 1
    ET.SubElement(layers, 'Layer', Name=layer, Id=str(layer_id), Specs='-1')
    return xml(data)


def next_entity_id(pak):
    """ID above every mission, layer and reserved entity ID."""
    with zipfile.ZipFile(pak) as archive:
        names = set(archive.namelist())
        mission = ET.fromstring(archive.read('objects_mission0.xml'))
        ids = {int(e.get('EntityId', '0')) for e in mission.iter('Entity') if e.get('EntityId')}
        if 'extractedlayerentityids.xml' in names:
            reserved = ET.fromstring(archive.read('extractedlayerentityids.xml'))
            ids |= {int(e.get('ID')) for e in reserved if e.get('ID')}
        for name in names:
            if name.lower().startswith('layers/') and name.lower().endswith('.xml'):
                ids |= {int(e.get('EntityId')) for e in ET.fromstring(archive.read(name)).iter('Entity')
                        if e.get('EntityId')}
    return max(ids, default=0) + 1


def prepare_level(blob, pak, *, replace=False, source_entities=None):
    """Prepare only owned ledge data, retaining other profile/layer metadata."""
    layer_path = 'layers/' + LAYER + '.xml'
    existing_ids, replaced_ids = {}, set()
    with zipfile.ZipFile(pak) as archive:
        names = archive.namelist()
        if len({n.lower() for n in names}) != len(names):
            raise ValueError('Duplicate level package members')
        if 'whdata_1' in names and not replace:
            raise ValueError('Destination already has whdata_1; pass --replace to retain its other managers')
        if layer_path in names:
            if not replace:
                raise ValueError('Ledge layer already exists; pass --replace')
            previous = ET.fromstring(archive.read(layer_path))
            if previous.tag != 'Objects':
                raise ValueError('Invalid existing ledge layer root')
            for entity in previous:
                name = entity.get('Name', '')
                if (entity.tag != 'Entity' or entity.get('EntityClass') != 'LedgeObject' or entity.get('Layer') != LAYER
                        or not name.startswith('gm_ledge_') or not name[9:].isdigit()):
                    raise ValueError('Existing ledge layer contains an unowned entity')
                old, ident = int(name[9:]), int(entity.get('EntityId'))
                expected = native_guid(int.from_bytes(hashlib.sha256(
                    ('ledge-import/' + str(old)).encode()).digest()[:8], 'little'))
                if (old <= 0 or ident <= 0 or old in existing_ids or str(ident) in replaced_ids
                        or entity.get('EntityGuid') != expected):
                    raise ValueError('Existing ledge layer has invalid ownership or IDs')
                existing_ids[old] = ident
                replaced_ids.add(str(ident))
            for name in names:
                if name == 'objects_mission0.xml' or (name.lower().startswith('layers/') and name.lower().endswith('.xml') and name != layer_path):
                    for entity in ET.fromstring(archive.read(name)).iter('Entity'):
                        if entity.get('EntityId') in replaced_ids:
                            raise ValueError('Existing ledge entity ID collides with another layer or mission')
        converted, entities, id_map, report = plan(
            blob, next_entity_id(pak), existing_ids=existing_ids, source_entities=source_entities)
        if 'whdata_1' in names:
            # Replace the ledge manager only. Existing travel/quest profiles and
            # any other target-native managers belong to their own converters.
            root = _parse_whdata(archive.read('whdata_1'))
            managers = root.findall('LedgeManager')
            if len(managers) > 1:
                raise ValueError('Duplicate destination LedgeManager')
            index = list(root).index(managers[0]) if managers else 0
            for manager in managers:
                root.remove(manager)
            root.insert(index, ET.fromstring(converted).find('LedgeManager'))
            if root.find('GameProfileManager') is None:
                root.append(ET.fromstring(converted).find('GameProfileManager'))
            converted = xml(root)
        members = {
            'whdata_1': converted,
            layer_path: _layer_xml(entities),
            'extractedlayerentityids.xml': _reserved_xml(
                entities, archive.read('extractedlayerentityids.xml') if 'extractedlayerentityids.xml' in names else None,
                replaced_ids=replaced_ids),
            'leveldata.xml': _leveldata_xml(archive.read('leveldata.xml'), replace=layer_path in names),
        }
        report['existing_game_profiles_preserved'] = 'whdata_1' in names
        if 'whdata_1' in names:
            report['limitations'] = [note for note in report['limitations'] if not note.startswith('GameProfileManager is emitted empty')]
    report['entity_ids'] = {str(old): new for old, new in id_map.items()}
    return members, report


def rewrite_level(level_dir, members):
    """Stream an atomic archive replacement; preserve unmodified members."""
    pak = level_dir / "level.pak"
    with tempfile.NamedTemporaryFile(dir=level_dir, prefix='ledges-', suffix='.pak', delete=False) as handle:
        replacement = Path(handle.name)
    try:
        with zipfile.ZipFile(pak) as archive, zipfile.ZipFile(replacement, 'w', zipfile.ZIP_STORED, allowZip64=True) as output:
            names = archive.namelist()
            if len({n.lower() for n in names}) != len(names):
                raise ValueError('Duplicate level package members')
            output.comment = archive.comment
            for info in archive.infolist():
                if info.filename in members:
                    output.writestr(copy.copy(info), members[info.filename])
                else:
                    with archive.open(info) as source, output.open(copy.copy(info), 'w', force_zip64=info.file_size >= zipfile.ZIP64_LIMIT) as target:
                        shutil.copyfileobj(source, target, length=1024*1024)
            for name in sorted(set(members) - set(names)):
                output.writestr(name, members[name])
        replacement.replace(pak)
    finally:
        replacement.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=GameLibrary, default=Path(r"D:\SteamLibrary\steamapps\common"))
    parser.add_argument("--data", type=Path, required=True, help="Build data root containing Levels/<level>")
    parser.add_argument("--level", default="setup_world", help="Converted level directory name to patch")
    parser.add_argument("--apply", action="store_true", help="Write the converted members into the level package")
    parser.add_argument("--replace", action="store_true", help="Allow overwriting an existing whdata_1 member")
    args = parser.parse_args()
    if not args.level.replace("_", "").isalnum():
        parser.error("Invalid level name")
    level_dir = args.data / "Levels" / args.level
    pak = level_dir / "level.pak"
    if not pak.is_file():
        parser.error("Level package not found: " + str(pak))
    with zipfile.ZipFile(args.library / SOURCE) as source:
        blob = read(source, "whdata_1")
        source_entities, origins = read_source_entities(source, blob)
    members, report = prepare_level(blob, pak, replace=args.replace, source_entities=source_entities)
    report['source_entity_members'] = {str(ident): name for ident, name in origins.items()}
    report["level"] = args.level
    report["source"] = SOURCE
    report_path = level_dir / "ledge-conversion-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    if not args.apply:
        print("Dry run: pass --apply to patch " + str(pak), flush=True)
        return
    rewrite_level(level_dir, members)
    print("Patched " + str(pak), flush=True)


if __name__ == "__main__":
    main()
