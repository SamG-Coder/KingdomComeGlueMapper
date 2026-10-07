"""Build a small original-vegetation compatibility probe on a working terrain level."""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict

from compiled_terrain import parse
from upgrade_map import read, xml
from vegetation import read_instances, verify_target_hlods, convert_instance
from merged_vegetation import inventory, convert_cell, build_tree
from vegetation_materials import convert_shadow_proxies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path(r"D:\SteamLibrary\steamapps\common"))
    parser.add_argument("--base-level", default="kcd1_terrain_v6")
    parser.add_argument("--level", default="kcd1_vegetation_v11")
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--all", action="store_true", help="Include every decoded individual vegetation instance across the world; merged vegetation is separate")
    parser.add_argument("--merged", action="store_true", help="Experimental: include all merged grass and ground-cover sector streams")
    args = parser.parse_args()
    if not all(name.replace("_", "").isalnum() for name in (args.level,args.base_level)) or not 1 <= args.limit <= 100:
        parser.error("Invalid level name or instance limit (1..100)")
    data_root = args.library / "KCD2Mod/Data"
    destination = data_root / "Levels" / args.level
    prefix = "glueveg/" + args.level + "/"
    assets = data_root / prefix
    if destination.exists() or assets.exists():
        parser.error("Output exists; choose a new level name")
    with ExitStack() as stack:
        source_pak = stack.enter_context(zipfile.ZipFile(args.library / "KingdomComeDeliverance/Data/Levels/rataje/level.pak"))
        source = parse(read(source_pak, "terrain/terrain.dat"))
        merged_cells, merged_groups, merged_instances = ({}, set(), 0)
        if args.merged:
            merged_cells, merged_groups, merged_instances = inventory(source_pak, len(source.tables["vegetation"]["paths"]))
            print(f"Found {merged_instances} merged samples in {len(merged_cells)} cells across {len(merged_groups)} groups", flush=True)
        native = stack.enter_context(zipfile.ZipFile(args.library / "KingdomComeDeliverance2/Data/Levels/trosecko/level.pak"))
        native_counts = verify_target_hlods(read(native, "terrain/hlods.dat"))
        records, skipped = read_instances(source)
        candidates = [r for r in records if "/vegetation/" in r["mesh"] and
                      (args.all or (r["pos"][0]-735)**2+(r["pos"][1]-3421)**2 < 250**2)]
        candidates.sort(key=lambda r: (r["pos"][0]-735)**2+(r["pos"][1]-3421)**2)
        if not candidates:
            raise ValueError("No vegetation candidates near Skalitz")
        # Begin with the nearest shrub species plus the nearest tree species.
        species = [candidates[0]["mesh"]]
        tree = next((r for r in candidates if "/trees/" in r["mesh"]), None)
        if tree and tree["mesh"] not in species:
            species.append(tree["mesh"])
        selected = []
        for mesh in species:
            selected.extend([r for r in candidates if r["mesh"] == mesh][:max(1,args.limit//len(species))])
        selected = selected[:args.limit]
        if args.all:
            selected = candidates
            species = sorted(set(r["mesh"] for r in selected))
        print(f"Selected {len(selected)} individual vegetation instances across {len(species)} meshes",flush=True)
        index = {}
        paths = [p for p in (args.library / "KingdomComeDeliverance/Data").glob("*.pak") if p.name.startswith(("GameData", "Textures", "Objects", "Buildings"))]
        paths.extend((args.library / "KingdomComeDeliverance/Engine").glob("*.pak"))
        for path in sorted(paths, key=lambda p: ("_HD" in p.name,p.name.lower())):
            archive = stack.enter_context(zipfile.ZipFile(path))
            for entry in archive.infolist():
                name = entry.filename.replace("\\", "/").lower()
                if name.endswith((".cgf", ".mtl")) or ".dds" in name:
                    index[name] = (archive,entry)

        def asset_read(name):
            archive,entry = index[name]
            return read(archive,entry.filename)

        emitted = {}
        texture_names = {}
        material_names = {}

        def material(name):
            name = name.replace("\\", "/").lower().removesuffix(".mtl")
            if name in material_names:
                return material_names[name]
            target = prefix + "m" + str(len(material_names))
            material_names[name] = target
            doc = ET.fromstring(asset_read(name+".mtl"))
            convert_shadow_proxies(doc)
            for element in doc.iter("Material"):
                element.attrib.pop("GenMask", None)
            for tex in doc.iter("Texture"):
                original = tex.get("File", "").replace("\\", "/").lower()
                if not original or original.startswith("$"):
                    continue
                original = str(PurePosixPath(original).with_suffix(".dds"))
                if original not in texture_names:
                    # CryEngine uses semantic suffixes such as _ddna to
                    # recognize normal/gloss textures; preserve the basename.
                    texture_names[original] = prefix + "t" + str(len(texture_names)) + "_" + PurePosixPath(original).name
                    if original not in index:
                        raise FileNotFoundError(original)
                    for member in index:
                        suffix = member[len(original):] if member.startswith(original) else "INVALID"
                        if suffix == "" or re.fullmatch(r"\.(?:a|[0-9]+a?)", suffix):
                            emitted[texture_names[original]+suffix] = asset_read(member)
                tex.set("File",texture_names[original])
            emitted[target+".mtl"] = xml(doc)
            return target

        def mesh_bytes(name):
            blob = bytearray(asset_read(name))
            if blob[:4] != b"CrCh" or struct.unpack_from("<I",blob,4)[0] != 0x746:
                raise ValueError("Unsupported CGF container")
            count,table = struct.unpack_from("<II",blob,8)
            if table+count*16 > len(blob):
                raise ValueError("Invalid CGF chunk table")
            for i in range(count):
                kind,version,chunk_id,size,offset = struct.unpack_from("<HHIII",blob,table+i*16)
                if offset+size > len(blob):
                    raise ValueError("CGF chunk exceeds file")
                if kind == 0x1014:
                    if version != 0x802 or size < 128:
                        raise ValueError("Unsupported material-name chunk")
                    old = bytes(blob[offset:offset+128]).split(b"\0",1)[0].decode()
                    # Only the root material names have a file path; retain
                    # descriptive submaterial names in the same CGF.
                    resolved = old.replace("\\", "/").lower().removesuffix(".mtl")
                    if "/" not in resolved:
                        relative = str(PurePosixPath(name).parent / resolved)
                        if relative+".mtl" in index:
                            resolved = relative
                    if resolved+".mtl" in index:
                        new = material(resolved).encode()
                        if len(new)>=128:
                            raise ValueError("Material path exceeds fixed CGF field")
                        blob[offset:offset+128] = new.ljust(128,b"\0")
            return bytes(blob)

        groups = sorted(set(r["group"] for r in selected) | merged_groups)
        group_map = {g:i for i,g in enumerate(groups)}
        group_bytes = bytearray()
        for group in groups:
            original = source.tables["vegetation"]["paths"][group].lower()
            target = prefix + "mesh" + str(group_map[group])
            emitted[target+".cgf"] = mesh_bytes(original)
            for lod in range(1,7):
                path = original[:-4]+f"_lod{lod}.cgf"
                if path in index:
                    emitted[target+f"_lod{lod}.cgf"] = mesh_bytes(path)
            start = source.tables["vegetation"]["offset"]+group*360
            record = bytearray(source.data[start:start+360])
            record[:256] = (target+".cgf").encode().ljust(256,b"\0")
            struct.pack_into("<i",record,320,group_map[group]+1)
            struct.pack_into("<i",record,336,-1)  # use embedded namespaced material
            group_bytes.extend(record)

        base = data_root / "Levels" / args.base_level
        with zipfile.ZipFile(base/"terrain.pak") as archive:
            baseline = parse(archive.read("terrain/terrain.dat"))
            terrain = bytearray(baseline.data[:32])
            terrain.extend(struct.pack("<I",len(groups))+group_bytes+bytes(8))
            terrain.extend(baseline.data[baseline.nodes[0]["offset"]:baseline.tree_offset])
            # Individual instances live in HLOD; merged render nodes remain
            # in the terrain octree and reference external compact sectors.
            merged_records = {}
            merged_streams = {}
            for cell, entries in sorted(merged_cells.items()):
                record, stream = convert_cell(source_pak, cell, entries, group_map)
                merged_records[cell] = record
                merged_streams[cell] = stream
            terrain.extend(build_tree(merged_records))
            struct.pack_into("<I",terrain,4,len(terrain))
            parsed = parse(bytes(terrain))
            if len(parsed.tables["vegetation"]["paths"]) != len(groups):
                raise ValueError("Vegetation table verification failed")
            destination.mkdir(parents=True)
            with zipfile.ZipFile(destination/"terrain.pak","x",zipfile.ZIP_STORED) as output:
                output.writestr("terrain/terrain.dat",terrain)
                output.writestr("terrain/indoor.dat",archive.read("terrain/indoor.dat"))
                for (x,y,z), stream in merged_streams.items():
                    output.writestr(f"terrain/merged_meshes_sectors/sector_{x}_{y}_{z}_0.dat",stream)
                if args.merged:
                    paths = [prefix + "mesh" + str(group_map[g]) + ".cgf" for g in sorted(merged_groups)]
                    output.writestr("terrain/merged_meshes_sectors/mmrm_used_meshes.lst", "\n".join(paths)+"\n")
            del merged_streams, merged_records
        hlod_data = bytearray(struct.pack("<III",2,0,0))
        hlod = ET.Element("HLod",DataOffset="4",DataSize="4",ProxyIndex="-1",Type="Cluster",Radius="8000",NearestObserverDistance="0",Name="root",Hash="0")
        vegetation_root = ET.SubElement(hlod,"HLod",DataOffset="8",DataSize="4",ProxyIndex="-1",Type="Vegetation",Pos="2048,2048,128",Radius="8000",NearestObserverDistance="0",Name="vegetation")
        sectors = defaultdict(list)
        for record in selected:
            sectors[(int(record["pos"][0])//64,int(record["pos"][1])//64)].append(record)
        for (x,y),instances in sorted(sectors.items()):
            payload = b"".join(convert_instance(source.data,r["offset"],group_map[r["group"]]) for r in instances)
            offset = len(hlod_data)
            hlod_data.extend(struct.pack("<I",len(payload))+payload)
            z = sum(r["pos"][2] for r in instances)/len(instances)
            ET.SubElement(vegetation_root,"HLod",DataOffset=str(offset),DataSize=str(len(payload)+4),ProxyIndex="-1",Type="Vegetation",Pos=f"{x*64+32},{y*64+32},{z}",Radius="90",NearestObserverDistance="0",Name=f"vegetation_{x}_{y}")
        if verify_target_hlods(hlod_data).get(2)!=len(selected):
            raise ValueError("Generated HLOD instance count mismatch")
        with zipfile.ZipFile(base/"level.pak") as archive, zipfile.ZipFile(destination/"level.pak","x",zipfile.ZIP_STORED) as output:
            for name in archive.namelist():
                payload = archive.read(name)
                if name == "levelinfo.xml":
                    doc = ET.fromstring(payload);doc.set("Name","data/levels/"+args.level);payload=xml(doc)
                    (destination/"levelinfo.xml").write_bytes(payload)
                elif name == "leveldata.xml":
                    doc = ET.fromstring(payload);doc.find("LevelInfo").set("Name",args.level);payload=xml(doc)
                output.writestr(name,payload)
            output.writestr("terrain/hlods.dat",hlod_data)
            output.writestr("terrain/hlods.xml",xml(hlod))
        for name,payload in emitted.items():
            target = (data_root/name).resolve()
            if not target.is_relative_to(assets.resolve()):
                raise ValueError("Asset outside isolated namespace")
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("xb") as stream:
                stream.write(payload)
            if hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(payload).digest():
                raise ValueError("Asset checksum mismatch")
        report = {"level":args.level,"base_level":args.base_level,"decoded_leading_vegetation_records":len(records),
                  "skipped_remainder_blocks_by_type":skipped,"native_hlod_records_validated":native_counts,
                  "selected_instances":selected,"species":species,"files":len(emitted),"material_map":material_names,
                  "instance_storage":"KCD2 HLOD version2 with64m vegetation sectors, no proxies",
                  "merged_instances":merged_instances,"merged_cells":len(merged_cells),"merged_groups":len(merged_groups),
                  "status":"Experimental source-CGF compatibility probe; runtime acceptance pending",
                  "limitations":["Only leading individual vegetation records inspected",
                                 "Merged sector geometry identifiers and combined legacy parts require runtime validation" if args.merged else "Merged vegetation excluded",
                                 "CGF geometry chunks unchanged; embedded material paths rewritten",
                                 "Source trailing instance word omitted; target high render flag bits zero",
                                 "Source billboard atlases and advanced wind compatibility unverified"]}
        (destination/"vegetation-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        Path("reports").mkdir(exist_ok=True)
        Path("reports/vegetation-probe.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(f"Built {args.level}: {len(selected)} original instances, {len(groups)} groups, {len(emitted)} asset files",flush=True)


if __name__ == "__main__":
    main()
