"""Build a compiled terrain probe from installed games, without raw editor data.

This first stage converts elevation and surface indices, not outdoor objects.
"""
import argparse
from game_paths import GameLibrary
import copy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from audit_maps import open_entry
from compiled_terrain import parse, convert_elevation
from terrain_materials import package_materials


def read(archive, name):
    with open_entry(archive, archive.getinfo(name)) as stream:
        return stream.read()


def digest(path):
    """Hex SHA-256 of a file, streamed like the receipt digests."""
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest_bytes(data):
    """Hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def archive_signature(archive):
    """Identity of an archive file for per-build memos; None for in-memory archives."""
    name = getattr(archive, 'filename', None)
    if not isinstance(name, str):
        return None
    try:
        stat = Path(name).stat()
    except OSError:
        return None
    return Path(name).resolve(), stat.st_size, stat.st_mtime_ns


class _Namespaced(Exception):
    """The document needs ElementTree's namespace handling."""


def _serialize(push, elem):
    # The same decisions as ElementTree._serialize_xml for documents without
    # namespaces, with ElementTree's own escaping, so the bytes are identical.
    tag, text = elem.tag, elem.text
    if tag is ET.Comment:
        push("<!--%s-->" % text)
    elif tag is ET.ProcessingInstruction:
        push("<?%s?>" % text)
    elif tag is None:
        if text:
            push(ET._escape_cdata(text))
        for child in elem:
            _serialize(push, child)
    else:
        if not isinstance(tag, str) or tag[:1] == "{":
            raise _Namespaced
        push("<" + tag)
        for key, value in elem.items():
            if not isinstance(key, str) or key[:1] == "{" or isinstance(value, ET.QName):
                raise _Namespaced
            push(' %s="%s"' % (key, ET._escape_attrib(value)))
        if text or len(elem):
            push(">")
            if text:
                push(ET._escape_cdata(text))
            for child in elem:
                _serialize(push, child)
            push("</" + tag + ">")
        else:
            push(" />")
    if elem.tail:
        push(ET._escape_cdata(elem.tail))


def xml(element):
    """ET.tostring(element, encoding='utf-8', xml_declaration=True), built as one string.

    Combine serializes the 34 MB mission and other large documents thousands
    of times a build. ElementTree writes them through a text stream in hundreds
    of millions of small pieces; collecting the same pieces and encoding once
    is 1.6x faster and byte-identical. Namespaced documents keep the stdlib path.
    """
    parts = ["<?xml version='1.0' encoding='utf-8'?>\n"]
    try:
        _serialize(parts.append, element)
    except _Namespaced:
        return ET.tostring(element, encoding="utf-8", xml_declaration=True)
    return "".join(parts).encode("utf-8", "xmlcharrefreplace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=GameLibrary, default=Path(r"D:\SteamLibrary\steamapps\common"))
    parser.add_argument("--output", type=Path, default=Path(r"D:\SteamLibrary\steamapps\common\KCD2Mod\Data\Levels\kcd1_terrain"))
    parser.add_argument("--original-materials", action="store_true", help="Package original terrain materials, including installed HD mip overrides")
    parser.add_argument("--terrain-blend-factor", type=float, choices=[0.5, 1.0, 2.0, 4.0, 8.0], help="Optional A/B experiment for terrain transition shading")
    args = parser.parse_args()
    if args.terrain_blend_factor is not None and not args.original_materials:
        parser.error("--terrain-blend-factor requires --original-materials")
    if args.output.exists():
        parser.error("Output exists; use a new directory to preserve prior experiments")
    level_name = args.output.name
    if not level_name.replace("_", "").isalnum():
        parser.error("Use an alphanumeric level name")
    with zipfile.ZipFile(args.library / "KingdomComeDeliverance/Data/Levels/rataje/level.pak") as src, \
         zipfile.ZipFile(args.library / "KingdomComeDeliverance2/Data/Levels/trosecko/terrain.pak") as target_terrain, \
         zipfile.ZipFile(args.library / "KingdomComeDeliverance2/Data/Levels/trosecko/level.pak") as target_level:
        original = parse(read(src, "terrain/terrain.dat"))
        reference = parse(read(target_terrain, "terrain/terrain.dat"))
        if reference.version != 29:
            raise ValueError("Unexpected installed target format")
        converted, samples = convert_elevation(original)
        print(f"Validated {len(original.nodes)} nodes; converted and checked {samples} samples", flush=True)
        # Reconstruct minimum level documents from retail XML, not editor source.
        original_info = ET.fromstring(read(src, "levelinfo.xml"))
        original_info.set("Name", "data/levels/"+level_name)
        original_info.set("SandboxVersion", "1.0.0.0")
        old_data = ET.fromstring(read(src, "leveldata.xml"))
        native_data = ET.fromstring(read(target_level, "leveldata.xml"))
        data = ET.Element("LevelData", SandboxVersion="1.0.0.0")
        info = copy.deepcopy(old_data.find("LevelInfo"))
        info.set("Name", level_name)
        data.append(info)
        surfaces = copy.deepcopy(old_data.find("SurfaceTypes"))
        # All original surface IDs remain, but appearance uses one valid target material.
        fallback = native_data.find("SurfaceTypes/SurfaceType").get("DetailMaterial")
        material_report = None
        if args.original_materials:
            package = args.library / "KCD2Mod/Data" / ("gluemapper_" + level_name + ".pak")
            material_report = package_materials(args.library, surfaces, package, level_name, args.terrain_blend_factor)
            fallback = None
        else:
            for surface in surfaces:
                surface.set("DetailMaterial", fallback)
                surface.set("Name", fallback)
        data.append(surfaces)
        data.append(copy.deepcopy(native_data.find("CollisionClasses")))
        for tag in ("Layers", "Vegetation", "MaterialsLibrary", "ParticlesLibrary", "GameTokensLibraryReferences"):
            ET.SubElement(data, tag)
        mission = ET.fromstring(read(target_level, "mission_mission0.xml"))
        mission.set("CGFCount", "0")
        action = ET.Element("LevelDataAction", SandboxVersion="1.0.0.0")
        missions = ET.SubElement(action, "Missions")
        ET.SubElement(missions, "Mission", Name="Mission0", File="Mission_Mission0.xml", CGFCount="0", ProgressBarRange="0")
        env = mission.find("Environment/EnvState")
        if env is not None:
            env.set("UseLayersActivation", "0")
        args.output.mkdir(parents=True)
        with zipfile.ZipFile(args.output / "terrain.pak", "x", zipfile.ZIP_STORED) as pak:
            pak.writestr("terrain/terrain.dat", converted.data)
            pak.writestr("terrain/indoor.dat", read(target_terrain, "terrain/indoor.dat"))
        with zipfile.ZipFile(args.output / "level.pak", "x", zipfile.ZIP_STORED) as pak:
            pak.writestr("levelinfo.xml", xml(original_info))
            pak.writestr("leveldata.xml", xml(data))
            pak.writestr("leveldataaction.xml", xml(action))
            pak.writestr("mission_mission0.xml", xml(mission))
            pak.writestr("terrain/cover.ctc", read(src, "terrain/cover.ctc"))
        # Discovery metadata is also loose for development mode enumeration.
        (args.output / "levelinfo.xml").write_bytes(xml(original_info))
        report = {
            "status": "compiled terrain prototype; outdoor objects excluded; material runtime validation required",
            "source_version": original.version, "target_version": reference.version,
            "source_object_tree_nodes": original.tree_nodes, "target_reference_object_tree_nodes": reference.tree_nodes,
            "terrain_nodes": len(original.nodes), "samples_verified": samples,
            "source_sha256": hashlib.sha256(original.data).hexdigest(),
            "converted_sha256": hashlib.sha256(converted.data).hexdigest(),
            "source_resource_tables": original.tables,
            "target_resource_tables": reference.tables,
            "material_fallback": fallback, "output": str(args.output),
            "material_package": material_report,
            "runtime_test": "pending", "excluded": ["buildings", "vegetation instances", "roads", "water volumes", "NPCs", "navigation", "quests"]}
        (args.output / "upgrade-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Built {args.output}", flush=True)


if __name__ == "__main__":
    main()
