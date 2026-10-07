"""Restore static structures near Skalitz or across the original world."""
import argparse
from collections import defaultdict, Counter
from contextlib import ExitStack
import json
import math
from pathlib import Path
import struct
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from building_brushes import read_brush, convert_brush, resource_table
from compiled_terrain import parse
from static_assets import asset_pack
from upgrade_map import read, xml
from vegetation import verify_target_hlods
from water_volumes import read_water


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path(r"D:\SteamLibrary\steamapps\common"))
    parser.add_argument("--base-level", default="kcd1_water_v17")
    parser.add_argument("--level", default="kcd1_buildings_v19")
    parser.add_argument("--radius", type=float, default=400)
    parser.add_argument("--all", action="store_true", help="Include shared and initial-state structures across the full map")
    parser.add_argument("--streams-only", action="store_true", help="Append stream and waterfall meshes to an existing building level")
    args = parser.parse_args()
    if not all(n and n.replace("_", "").isalnum() for n in (args.base_level, args.level)) or not math.isfinite(args.radius) or not 1 <= args.radius <= 1000:
        parser.error("Invalid level name or radius (1..1000 metres)")
    root = args.library / "KCD2Mod/Data"
    destination = root / "Levels" / args.level
    base = root / "Levels" / args.base_level
    prefix = "gluebuild/" + args.level + "/"
    assets = root / prefix
    if destination.exists() or assets.exists():
        parser.error("Output exists; choose a new level name")
    with ExitStack() as stack:
        source_pak = stack.enter_context(zipfile.ZipFile(args.library / "KingdomComeDeliverance/Data/Levels/rataje/level.pak"))
        source = parse(read(source_pak, "terrain/terrain.dat"))
        layers = ET.fromstring(read(source_pak, "leveldata.xml")).find("Layers")
        layer_names = {int(e.get("Id")): e.get("Name").split("{")[0] for e in layers}
        # Generic platform layers have no state ancestry in this flat export;
        # the nearby pc layer includes hundreds of burned-town debris meshes.
        # Only enable explicitly identified intact states and shared layer zero.
        allowed = {0} | {i for i, name in layer_names.items() if name in ("sv_state0", "sv_state0_prefabs")
                        or (args.all and (name.endswith("_state0") or name.endswith("_state0_prefabs")))}
        selected = []
        excluded_layers = Counter()
        def collect(data, offset, size, kind):
            if kind != 1:
                return
            record = read_brush(data, offset, source.tables["meshes"]["paths"], source.tables["materials"]["paths"])
            x, y, _ = record["position"]
            paths = ("/nature/stream_edge/", "/nature/waterfalls/") if args.streams_only else ("/buildings/", "/structures/", "/props/fences/")
            if ((args.all or (x-734.9)**2+(y-3421.4)**2 < args.radius**2)
                    and any(part in record["path"] for part in paths)):
                if record["layer"] in allowed:
                    selected.append(record)
                else:
                    excluded_layers[record["layer"]] += 1
        read_water(source, collect)
        if not selected:
            raise ValueError("No building placements selected")
        groups = sorted(set(r["mesh"] for r in selected))
        print(f"Selected {len(selected)} intact/shared static structures across {len(groups)} meshes", flush=True)
        Path("outputs").mkdir(exist_ok=True)
        cache = stack.enter_context(tempfile.TemporaryDirectory(prefix="buildings-", dir="outputs"))
        index, emitted, material, mesh_bytes = asset_pack(stack, args.library, prefix, cache)
        with zipfile.ZipFile(base / "terrain.pak") as archive:
            base_meshes = list(parse(archive.read("terrain/terrain.dat")).tables["meshes"]["paths"])
        if base_meshes and not args.streams_only:
            raise ValueError("Base mesh table is not empty")
        meshes = list(base_meshes)
        mesh_map = {}
        for number, group in enumerate(groups):
            original = source.tables["meshes"]["paths"][group].replace("\\", "/").lower()
            name = prefix + "mesh" + str(number)
            emitted[name + ".cgf"] = mesh_bytes(original)
            for lod in range(1,7):
                lod_path = original[:-4] + f"_lod{lod}.cgf"
                if lod_path in index:
                    emitted[name + f"_lod{lod}.cgf"] = mesh_bytes(lod_path)
            mesh_map[group] = len(meshes)
            meshes.append(name + ".cgf")
            if (number + 1) % 250 == 0:
                print(f"Packaged {number+1}/{len(groups)} meshes; {len(emitted)} asset files", flush=True)
        with zipfile.ZipFile(base / "terrain.pak") as archive:
            baseline = parse(archive.read("terrain/terrain.dat"))
            if baseline.tables["meshes"]["paths"] != base_meshes:
                raise ValueError("Base mesh table changed during build")
            materials = list(baseline.tables["materials"]["paths"])
            material_map = {-1: -1}
            for identifier in sorted(set(r["material"] for r in selected) - {-1}):
                original = source.tables["materials"]["paths"][identifier].lower()
                if not original:
                    material_map[identifier] = -1
                    continue
                target = material(original)
                if target not in materials:
                    materials.append(target)
                material_map[identifier] = materials.index(target)
            start = baseline.tables["meshes"]["offset"] - 4
            terrain = bytearray(baseline.data[:start] + resource_table(meshes) + resource_table(materials)
                                + baseline.data[baseline.nodes[0]["offset"]:])
            struct.pack_into("<I", terrain, 4, len(terrain))
            verified = parse(bytes(terrain))
            if verified.data[verified.nodes[0]["offset"]:] != baseline.data[baseline.nodes[0]["offset"]:]:
                raise ValueError("Base terrain/object payload changed")
            destination.mkdir(parents=True)
            with zipfile.ZipFile(destination / "terrain.pak", "x", zipfile.ZIP_STORED) as output:
                for name in archive.namelist():
                    output.writestr(name, terrain if name == "terrain/terrain.dat" else archive.read(name))
        sectors = defaultdict(list)
        for record in selected:
            x, y, _ = record["position"]
            sectors[(int(x)//64, int(y)//64)].append(record)
        with zipfile.ZipFile(base / "level.pak") as archive:
            hlod_data = bytearray(archive.read("terrain/hlods.dat"))
            before = verify_target_hlods(hlod_data)
            hlod = ET.fromstring(archive.read("terrain/hlods.xml"))
            for (x,y), records in sorted(sectors.items()):
                payload = b"".join(convert_brush(source.data, r["offset"], mesh_map[r["mesh"]], material_map[r["material"]]) for r in records)
                offset = len(hlod_data)
                hlod_data.extend(struct.pack("<I", len(payload)) + payload)
                z = sum(r["position"][2] for r in records) / len(records)
                ET.SubElement(hlod, "HLod", DataOffset=str(offset), DataSize=str(len(payload)+4),
                              ProxyIndex="-1", Type="Cluster", Pos=f"{x*64+32},{y*64+32},{z}",
                              Radius="400", NearestObserverDistance="0", Name=f"{'streams' if args.streams_only else 'buildings'}_{x}_{y}")
            after = verify_target_hlods(hlod_data)
            if after.get(1,0)-before.get(1,0) != len(selected) or after.get(2) != before.get(2):
                raise ValueError("HLOD placement count changed unexpectedly")
            with zipfile.ZipFile(destination / "level.pak", "x", zipfile.ZIP_STORED) as output:
                for name in archive.namelist():
                    payload = archive.read(name)
                    if name == "levelinfo.xml":
                        doc = ET.fromstring(payload); doc.set("Name", "data/levels/"+args.level); payload=xml(doc)
                        (destination / "levelinfo.xml").write_bytes(payload)
                    elif name == "leveldata.xml":
                        doc = ET.fromstring(payload); doc.find("LevelInfo").set("Name",args.level); payload=xml(doc)
                    elif name == "terrain/hlods.dat":
                        payload = hlod_data
                    elif name == "terrain/hlods.xml":
                        payload = xml(hlod)
                    output.writestr(name,payload)
        for name,payload in emitted.items():
            target = (root/name).resolve()
            if not target.is_relative_to(assets.resolve()):
                raise ValueError("Asset escapes isolated namespace")
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("xb") as stream, payload.open("rb") as source_stream:
                shutil.copyfileobj(source_stream, stream)
        report = {"level":args.level,"base_level":args.base_level,"placements":len(selected),"meshes":len(meshes)-len(base_meshes),
                  "inherited_meshes":len(base_meshes),"streams_only":args.streams_only,
                  "asset_files":len(emitted),"radius":None if args.all else args.radius,"all":args.all,
                  "layers":{str(i):layer_names.get(i,"shared") for i in sorted(allowed)},
                  "excluded_layers":{str(i):{"name":layer_names.get(i),"placements":n} for i,n in excluded_layers.items()},
                  "layer_counts":dict(Counter(r["layer"] for r in selected)),"records":selected,
                  "status":"Binary conversion verified; visual and collision checks pending",
                  "limitations":["Static structures only; dynamic doors and NPCs excluded",
                                 "CGF geometry and physics chunks retained; target neutral collision class used",
                                 "Intact/shared layers selected; game quest layer switching not implemented"]}
        report_name = "streams" if args.streams_only else "buildings"
        (destination/(report_name+"-report.json")).write_text(json.dumps(report,indent=2),encoding="utf-8")
        Path("reports").mkdir(exist_ok=True)
        Path("reports/"+report_name+"-probe.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(f"Built {args.level}: {len(selected)} added placements, {len(groups)} added meshes, {len(emitted)} asset files",flush=True)


if __name__ == "__main__":
    main()
