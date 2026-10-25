"""Convert KCD1 authored vault and climb points into the KCD2 level format.

KCD2 enables fence and wall vaulting through two coupled pieces: the
`whdata_1` archive member (a `<Root version="1">` carrying a `LedgeManager`
plus a `GameProfileManager`) and the `LedgeObject` layer entities whose
`EntityId` the nonzero `Ledge.entityId` records reference. The converted
level shipped neither, so vaulting was absent map-wide (issue #5).

KCD1's `rataje/level.pak` stores the same `LedgeManager` schema (3,589
ledges, 30,140 markers, world-space coordinates the converter keeps 1:1).
KCD1's 228 authored `LedgeObject` entities exist only in the Modding Tools
export, which is not required for the conversion: every linked record
already carries its first marker as the native entity position, and the
authored bIsThin/window/flip semantics are folded into Flags0/Flags1.

The converter therefore:
- copies the whole LedgeManager verbatim, re-numbering the 228 linked
  records to fresh ids reserved against the converted level's own entities;
- synthesizes one minimal LedgeObject entity per link (Name, Pos = first
  marker, reserved EntityId/EntityGuid) in `layers/ledges_import.xml`,
  registered in leveldata.xml and extractedlayerentityids.xml, matching
  the native layer pattern used by region_travel_entry;
- drops the KCD1-only WeatherProfileManager and emits the minimal empty
  GameProfileManager in the native child order.

The entityId=0 world-ledge bucket is preserved unchanged. Runtime vaulting
still requires an in-game check; this converter establishes the structural
conversion only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import native_guid
from game_paths import GameLibrary
from upgrade_map import xml


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


def plan(blob, next_id, layer=LAYER):
    """Return (whdata_1 bytes, entities, id_map, report) for a KCD1 whdata_1.

    Every record and marker is preserved verbatim except the entityId of the
    linked ledges, which is re-numbered to fresh ids (next_id upward) that the
    synthesized LedgeObject entities use. The world bucket (entityId 0) and
    all flag values are copied unchanged.
    """
    import copy
    source = _parse_whdata(blob)
    manager = read_ledges(blob)
    ledges, markers = manager.find("Ledges"), manager.find("Markers")
    points = list(markers)
    id_map = {}
    entities = []
    for record in ledges:
        old = int(_child_text(record, "entityId").text)
        if not old:
            continue
        if old not in id_map:
            first = points[int(_child_text(record, "markersStartIdx").text)]
            position = first.find("worldPosition")
            if position is None or position.text is None:
                raise ValueError("Linked ledge marker has no world position")
            guid = native_guid(int.from_bytes(hashlib.sha256(
                ('ledge-import/' + str(old)).encode()).digest()[:8], 'little'))
            entity = ET.Element('Entity', Name='gm_ledge_' + str(old),
                                EntityClass='LedgeObject', EntityId=str(next_id),
                                EntityGuid=guid, Pos=position.text,
                                CastShadowMinSpec='1', Layer=layer)
            ET.SubElement(entity, 'Properties', bSaved_by_game='0')
            entities.append(entity)
            id_map[old] = next_id
            next_id += 1
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
        weather_profile_manager_dropped=source.find("WeatherProfileManager") is not None,
        game_profiles_emitted=0,
        status="Structural conversion verified; vaulting behavior must be checked in game",
        limitations=[
            "Linked ledges reference synthesized LedgeObject entities; authored layer names are not imported",
            "KCD1 WeatherProfileManager is not converted",
            "GameProfileManager is emitted empty; region arrival profiles are added later",
            "Runtime vault, window and flip behavior requires an in-game check",
        ])
    return xml(root), entities, id_map, report


def convert(blob, next_id):
    """Backwards-compatible wrapper; returns (whdata_1 bytes, report) only."""
    result, _, _, report = plan(blob, next_id)
    return result, report


def _layer_xml(entities, layer=LAYER):
    root = ET.Element('Objects')
    for entity in entities:
        root.append(entity)
    return xml(root)


def _reserved_xml(entities):
    root = ET.Element('ReservedEntityIDsFromLayers')
    for entity in entities:
        ET.SubElement(root, 'ReservedEntityID', ID=entity.get('EntityId'))
    return xml(root)


def _leveldata_xml(blob, layer=LAYER):
    data = ET.fromstring(blob)
    layers = data.find('Layers')
    if layers is None:
        raise ValueError('leveldata.xml has no Layers container')
    if any(e.get('Name') == layer for e in layers):
        raise ValueError('Ledge layer already registered')
    layer_id = max((int(e.get('Id')) for e in layers), default=0) + 1
    ET.SubElement(layers, 'Layer', Name=layer, Id=str(layer_id), Specs='-1')
    return xml(data)


def next_entity_id(pak):
    """Lowest unused EntityId across the mission and reserved layer ids."""
    with zipfile.ZipFile(pak) as archive:
        names = set(archive.namelist())
        mission = ET.fromstring(archive.read('objects_mission0.xml'))
        ids = {int(e.get('EntityId', '0')) for e in mission.iter('Entity') if e.get('EntityId')}
        if 'extractedlayerentityids.xml' in names:
            reserved = ET.fromstring(archive.read('extractedlayerentityids.xml'))
            ids |= {int(e.get('ID')) for e in reserved if e.get('ID')}
    return max(ids, default=0) + 1


def rewrite_level(level_dir, members):
    """Add or replace archive members of level_dir/level.pak in place."""
    pak = level_dir / "level.pak"
    with zipfile.ZipFile(pak) as archive:
        existing = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    existing.update(members)
    replacement = pak.with_name("level.pak.new")
    with zipfile.ZipFile(replacement, "x", zipfile.ZIP_STORED) as output:
        for name, payload in existing.items():
            output.writestr(name, payload)
    replacement.replace(pak)


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
        blob = source.read("whdata_1")
    start = next_entity_id(pak)
    converted, entities, id_map, report = plan(blob, start)
    report["level"] = args.level
    report["source"] = SOURCE
    report["reserved_ids_start"] = start
    members = {
        "whdata_1": converted,
        "layers/" + LAYER + ".xml": _layer_xml(entities),
        "extractedlayerentityids.xml": _reserved_xml(entities),
    }
    with zipfile.ZipFile(pak) as archive:
        if 'leveldata.xml' in archive.namelist():
            members['leveldata.xml'] = _leveldata_xml(archive.read('leveldata.xml'))
        else:
            raise ValueError('leveldata.xml missing from the level package')
    report_path = level_dir / "ledge-conversion-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    if not args.apply:
        print("Dry run: pass --apply to patch " + str(pak), flush=True)
        return
    with zipfile.ZipFile(pak) as archive:
        if "whdata_1" in archive.namelist() and not args.replace:
            raise ValueError("Destination already has whdata_1; rebuild the level, restore the previous package, or pass --replace")
    rewrite_level(level_dir, members)
    print("Patched " + str(pak), flush=True)


if __name__ == "__main__":
    main()
