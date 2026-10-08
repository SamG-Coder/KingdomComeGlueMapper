"""Add original water surfaces and physics contours to a working vegetation level."""
import argparse
from game_paths import GameLibrary
import copy
from contextlib import ExitStack
import json
from pathlib import Path, PurePosixPath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile

from compiled_terrain import parse
from upgrade_map import read, xml
from water_volumes import read_water, convert_water, append_water


def shader_features(contents):
    return {name: int(mask, 16) for name, mask in re.findall(
        r"Name\s*=\s*(%\w+)\s+Mask\s*=\s*(0x[0-9a-fA-F]+)", contents)}


def translate_features(doc, source_shader, target_shader):
    numeric = int(doc.get("GenMask", "0"), 16)
    enabled = {name for name, mask in source_shader.items() if numeric & mask}
    enabled.update(re.findall(r"%\w+", doc.get("StringGenMask", "")))
    retained = enabled & target_shader.keys()
    # Hardware-dependent tessellation is selected by the target runtime.
    retained.discard("%WATER_TESSELLATION_DX11")
    mask = 0
    for name in retained:
        mask |= target_shader[name]
    # This runtime compiled the no-feature permutation with StringGenMask alone.
    # Rebuild the numeric mask from TARGET definitions rather than copying it.
    doc.set("GenMask", format(mask, "x"))
    doc.set("StringGenMask", "".join(sorted(retained)))
    return retained, enabled - retained


def global_environment_probe(objects, cubemap=None):
    candidates = [e for e in objects.iter("Entity")
                  if e.get("EntityClass") == "EnvironmentLight" and e.get("Name") == "level_global_probe"]
    if len(candidates) != 1:
        raise ValueError("Expected one original global environment probe")
    source = candidates[0]
    props = source.find("Properties")
    advanced = props.find("OptionsAdvanced") if props is not None else None
    if advanced is None or advanced.get("bDynamic") != "1" or advanced.get("texture_deferred_cubemap"):
        raise ValueError("Unexpected global probe configuration")
    result = ET.Element("Objects")
    entity = ET.SubElement(result, "Entity", Name=source.get("Name"),
                           EntityClass="EnvironmentLight", Pos=source.get("Pos"),
                           PerInstanceStreamable="0")
    # Let KCD2 allocate the entity identity; no KCD1 GUID, layer or prefab links.
    converted = copy.deepcopy(props)
    converted.attrib.pop("_nVersion", None)
    if cubemap:
        advanced = converted.find("OptionsAdvanced")
        advanced.set("bDynamic", "0")
        advanced.set("texture_deferred_cubemap", cubemap)
    entity.append(converted)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=GameLibrary, default=Path(r"D:\SteamLibrary\steamapps\common"))
    parser.add_argument("--base-level", default="kcd1_groundcover_v13")
    parser.add_argument("--level", default="kcd1_water_v17")
    parser.add_argument("--native-water-materials", action="store_true", help="Use the visually confirmed KCD2 river material (recommended)")
    args = parser.parse_args()
    if not all(n and n.replace("_", "").isalnum() for n in (args.base_level, args.level)):
        parser.error("Invalid level name")
    root = args.library / "KCD2Mod/Data"
    base = root / "Levels" / args.base_level
    destination = root / "Levels" / args.level
    prefix = "gluewater/" + args.level + "/"
    assets = root / prefix
    if destination.exists() or assets.exists():
        parser.error("Output exists; choose a new level name")
    with ExitStack() as stack:
        source_archive = stack.enter_context(zipfile.ZipFile(args.library / "KingdomComeDeliverance/Data/Levels/rataje/level.pak"))
        native_archive = stack.enter_context(zipfile.ZipFile(args.library / "KingdomComeDeliverance2/Data/Levels/trosecko/terrain.pak"))
        source = parse(read(source_archive, "terrain/terrain.dat"))
        # An empty dynamic texture path derives a level/name/time asset path.
        # Those assets are not present for a newly generated level. Use an
        # explicit installed daytime fallback until the full probe schedule
        # is converted; never leave a visible ReplaceMe cubemap bound.
        probe_cubemap = "textures/cubemaps/trosecko/global_probe_trosecko/07_00_cm.dds"
        probe_objects = global_environment_probe(ET.fromstring(read(source_archive, "objects_mission0.xml")), probe_cubemap)
        native = parse(read(native_archive, "terrain/terrain.dat"))
        waters, source_counts = read_water(source)
        native_waters, native_counts = read_water(native)
        if not waters or not native_waters:
            raise ValueError("Water reference records missing")
        tails = {native.data[r["offset"] + 212:r["offset"] + 220] for r in native_waters}
        if len(tails) != 1 or struct.unpack("<2f", next(iter(tails))) != (1000.0, 1000.0):
            raise ValueError("Unexpected native water auxiliary defaults")
        auxiliary_tail = next(iter(tails))
        print(f"Decoded all object boundaries: {len(waters)} original and {len(native_waters)} native water volumes", flush=True)

        index = {}
        source_shader = target_shader = None
        paths = [p for p in (args.library / "KingdomComeDeliverance/Data").glob("*.pak")
                 if p.name.startswith(("GameData", "Textures", "Objects", "Buildings"))]
        paths.extend((args.library / "KingdomComeDeliverance/Engine").glob("*.pak"))
        for path in sorted(paths, key=lambda p: ("_HD" in p.name, p.name.lower())):
            archive = stack.enter_context(zipfile.ZipFile(path))
            for entry in archive.infolist():
                name = entry.filename.replace("\\", "/").lower()
                if name.endswith(".mtl") or ".dds" in name:
                    index[name] = (archive, entry.filename)
                if name.endswith("/watervolume.ext"):
                    source_shader = shader_features(read(archive, entry.filename).decode())
        for path in (args.library / "KingdomComeDeliverance2/Engine").glob("*.pak"):
            archive = stack.enter_context(zipfile.ZipFile(path))
            for name in archive.namelist():
                if name.replace("\\", "/").lower().endswith("/watervolume.ext"):
                    target_shader = shader_features(read(archive, name).decode())
        if not source_shader or not target_shader:
            raise ValueError("Installed water shader feature definitions missing")
        emitted = {}
        mapping = {}
        material_paths = []
        material_report = []
        for identifier in sorted(set(r["material"] for r in waters)):
            if not 0 <= identifier < len(source.tables["materials"]["paths"]):
                raise ValueError("Invalid source water material reference")
            original = source.tables["materials"]["paths"][identifier].replace("\\", "/").lower()
            if not original:
                mapping[identifier] = -1  # preserve material-less source volumes
                continue
            if args.native_water_materials:
                target = "materials/terrain/water/water_volume_river"
                if target not in material_paths:
                    material_paths.append(target)
                mapping[identifier] = material_paths.index(target)
                material_report.append({"source": original, "target": target, "diagnostic_native_material": True})
                continue
            archive, name = index[original + ".mtl"]
            doc = ET.fromstring(read(archive, name))
            if doc.get("Shader", "").lower() != "watervolume":
                raise ValueError("Unexpected water shader")
            retained, removed = translate_features(doc, source_shader, target_shader)
            doc.set("Shader", "Watervolume")
            for texture in doc.iter("Texture"):
                path = texture.get("File", "").replace("\\", "/").lower()
                if not path or path.startswith("$"):
                    continue
                compiled = str(PurePosixPath(path).with_suffix(".dds"))
                if compiled not in index:
                    raise FileNotFoundError(compiled)
                texture.set("File", prefix + compiled)
                for member, (archive, entry) in index.items():
                    suffix = member[len(compiled):] if member.startswith(compiled) else "INVALID"
                    if suffix == "" or re.fullmatch(r"\.(?:a|[0-9]+a?)", suffix):
                        target = prefix + member
                        if target not in emitted:
                            emitted[target] = read(archive, entry)
            target = prefix + "m" + str(len(material_paths))
            mapping[identifier] = len(material_paths)
            material_paths.append(target)
            emitted[target + ".mtl"] = xml(doc)
            material_report.append({"source": original, "target": target,
                                    "features": sorted(retained), "removed_features": sorted(removed),
                                    "target_feature_mask": doc.get("GenMask")})

        records = [convert_water(source.data, r, mapping[r["material"]], auxiliary_tail) for r in waters]
        with zipfile.ZipFile(base / "terrain.pak") as archive:
            baseline = parse(archive.read("terrain/terrain.dat"))
            base_water, base_counts = read_water(baseline)
            if base_water:
                raise ValueError("Base already contains water volumes")
            converted = append_water(baseline, records, material_paths)
            checked = parse(converted)
            checked_water, checked_counts = read_water(checked)
            expected_counts = dict(base_counts)
            expected_counts[9] = len(waters)
            if len(checked_water) != len(waters) or checked_counts != expected_counts:
                raise ValueError("Water or base merged-sector count changed unexpectedly")
            destination.mkdir(parents=True)
            with zipfile.ZipFile(destination / "terrain.pak", "x", zipfile.ZIP_STORED) as output:
                for name in archive.namelist():
                    output.writestr(name, converted if name == "terrain/terrain.dat" else archive.read(name))
        with zipfile.ZipFile(base / "level.pak") as archive, zipfile.ZipFile(destination / "level.pak", "x", zipfile.ZIP_STORED) as output:
            if any(n.lower() == "objects_mission0.xml" for n in archive.namelist()):
                raise ValueError("Base already contains entities; explicit entity merging required")
            for name in archive.namelist():
                payload = archive.read(name)
                if name == "levelinfo.xml":
                    doc = ET.fromstring(payload)
                    doc.set("Name", "data/levels/" + args.level)
                    payload = xml(doc)
                    (destination / "levelinfo.xml").write_bytes(payload)
                elif name == "leveldata.xml":
                    doc = ET.fromstring(payload)
                    doc.find("LevelInfo").set("Name", args.level)
                    payload = xml(doc)
                output.writestr(name, payload)
            output.writestr("objects_mission0.xml", xml(probe_objects))
        for name, payload in emitted.items():
            path = (root / name).resolve()
            if not path.is_relative_to(assets.resolve()):
                raise ValueError("Water asset escapes namespace")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                output.write(payload)
            if path.read_bytes() != payload:
                raise ValueError("Water asset verification failed")
        report = {"level": args.level, "base_level": args.base_level, "water_volumes": len(waters),
                  "source_object_counts": source_counts, "native_object_counts": native_counts,
                  "converted_object_counts": checked_counts, "materials": material_report,
                  "asset_files": len(emitted), "volumes": waters,
                  "global_environment_probe": {"entity": "level_global_probe", "cubemap": probe_cubemap,
                                               "dynamic": False, "limitation": "Fixed KCD2 daytime fallback; original probe time schedule not converted"},
                  "native_water_materials_diagnostic": args.native_water_materials,
                  "material_less_volumes": sum(mapping[r["material"]] == -1 for r in waters),
                  "status": "Binary conversion verified; visual water, flow and swimming acceptance pending",
                  "limitations": ["Source river shader parameters retained; KCD2 illumination may differ",
                                  "Two auxiliary defaults copied from installed native volumes",
                                  "Depends on existing base terrain and vegetation namespaces"]}
        (destination / "water-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        Path("reports").mkdir(exist_ok=True)
        Path("reports/water-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Built {args.level}: {len(waters)} volumes, {len(material_paths)} water materials, {len(emitted)} asset files", flush=True)


if __name__ == "__main__":
    main()
