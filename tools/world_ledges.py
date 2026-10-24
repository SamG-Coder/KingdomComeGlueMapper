"""Convert KCD1 authored vault and climb points into the KCD2 level format.

KCD2 enables fence and wall vaulting through the `whdata_1` archive member:
a `<Root version="1">` carrying a `LedgeManager` (ledges and their markers)
followed by a `GameProfileManager`. The converted level currently ships no
`whdata_1` at all, so vaulting is absent map-wide (issue #5).

KCD1's `rataje/level.pak` already stores the same `LedgeManager` schema with
world-space markers in the coordinates the converter keeps. Native KCD2 levels
also use `entityId="0"` ledges for world-authored vault points (1,893 of 2,105
in Trosky), so every KCD1 ledge is converted to that form: the entity link is
dropped and the world bit (`Flags0` bit 0) is set, everything else verbatim.
This avoids dangling references to layer entities that the conversion does not
ship, and loses no authored flag data (`bIsThin`, window and flip semantics are
already folded into `Flags0`/`Flags1` by the export).

The KCD1-only `WeatherProfileManager` is dropped, and the converted archive
emits the minimal empty `GameProfileManager` in the native child order.
Runtime vaulting still requires an in-game check; this converter establishes
the structural conversion only.
"""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from game_paths import GameLibrary
from upgrade_map import xml


SOURCE = "KingdomComeDeliverance/Data/Levels/rataje/level.pak"


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


def convert(blob):
    """Return (whdata_1 bytes, report) for a KCD1 whdata_1 blob.

    Every ledge becomes a world ledge: `entityId` is set to 0 and `Flags0` bit 0
    is set. All other record fields and every marker are preserved byte for
    byte. The KCD1 WeatherProfileManager is dropped and an empty
    GameProfileManager is emitted in the native position.
    """
    source = _parse_whdata(blob)
    manager = read_ledges(blob)
    ledges, markers = manager.find("Ledges"), manager.find("Markers")
    entity_links, flags_changed = [], 0
    for record in ledges:
        if int(_child_text(record, "entityId").text) != 0:
            entity_links.append(int(record.find("entityId").text))
            record.find("entityId").text = "0"
            flags = record.find("Flags0")
            value = int(flags.text)
            if not value & 1:
                flags.text = str(value | 1)
                flags_changed += 1
    root = ET.Element("Root", version="1")
    converted = ET.SubElement(root, "LedgeManager")
    converted.append(ledges)
    converted.append(markers)
    profiles = ET.SubElement(root, "GameProfileManager")
    ET.SubElement(profiles, "GameProfiles")
    ET.SubElement(ET.SubElement(profiles, "GameProfileGroups"), "Groups")
    report = dict(
        ledges=len(ledges), markers=len(markers),
        entity_linked_zeroed=sorted(entity_links), flags_world_bit_set=flags_changed,
        weather_profile_manager_dropped=source.find("WeatherProfileManager") is not None,
        game_profiles_emitted=0,
        status="Structural conversion verified; vaulting behavior must be checked in game",
        limitations=[
            "World-ledges only: authored layer entity links are dropped and the world bit is set",
            "KCD1 WeatherProfileManager is not converted",
            "GameProfileManager is emitted empty; region arrival profiles are added later",
            "Runtime vault, window and flip behavior requires an in-game check",
        ])
    return xml(root), report


def rewrite_level(level_dir, whdata_1):
    """Add or replace the whdata_1 member of level_dir/level.pak in place."""
    pak = level_dir / "level.pak"
    with zipfile.ZipFile(pak) as archive:
        members = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    members["whdata_1"] = whdata_1
    replacement = pak.with_name("level.pak.new")
    with zipfile.ZipFile(replacement, "x", zipfile.ZIP_STORED) as output:
        for name, payload in members.items():
            output.writestr(name, payload)
    replacement.replace(pak)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=GameLibrary, default=Path(r"D:\SteamLibrary\steamapps\common"))
    parser.add_argument("--data", type=Path, required=True, help="Build data root containing Levels/<level>")
    parser.add_argument("--level", default="setup_world", help="Converted level directory name to patch")
    parser.add_argument("--apply", action="store_true", help="Write the converted whdata_1 into the level package")
    args = parser.parse_args()
    if not args.level.replace("_", "").isalnum():
        parser.error("Invalid level name")
    level_dir = args.data / "Levels" / args.level
    pak = level_dir / "level.pak"
    if not pak.is_file():
        parser.error("Level package not found: " + str(pak))
    with zipfile.ZipFile(args.library / SOURCE) as source:
        blob = source.read("whdata_1")
    converted, report = convert(blob)
    report["level"] = args.level
    report["source"] = SOURCE
    report_path = level_dir / "ledge-conversion-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    if not args.apply:
        print("Dry run: pass --apply to patch " + str(pak), flush=True)
        return
    if "whdata_1" in zipfile.ZipFile(pak).namelist():
        raise ValueError("Destination already has whdata_1; rebuild the level or restore the previous package")
    rewrite_level(level_dir, converted)
    print("Patched " + str(pak), flush=True)


if __name__ == "__main__":
    main()
