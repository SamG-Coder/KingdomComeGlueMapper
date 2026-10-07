"""Isolated CGF/material/texture packaging shared by static-object probes."""
from pathlib import Path, PurePosixPath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from upgrade_map import read, xml
from build_water_probe import shader_features, translate_features


def convert_material_features(element, source_shader, target_shader):
    """Translate named permutations and the observed Illum second-UV slot."""
    # Serialized material masks use remapped global bits; .ext masks are local.
    # Named features are authoritative when present, so don't OR unrelated bits
    # from a serialized GenMask into the requested target permutation.
    if "StringGenMask" in element.attrib:
        element.set("GenMask", "0")
    retained, removed = translate_features(element, source_shader, target_shader)
    if element.get("Shader", "").lower() == "illum" and "%SNDUVS" in retained:
        textures = element.find("Textures")
        if textures is not None:
            secondary = textures.find("Texture[@Map='[1] Diffuse']")
            if secondary is not None:
                # KCD2 binds SNDUVSDiffuseTex to TM_Opacity. The old separate
                # tiled blend mask cannot also occupy this slot in that shader.
                for old in list(textures):
                    if old is not secondary and old.get("Map") == "Opacity":
                        textures.remove(old)
                secondary.set("Map", "Opacity")
    return retained, removed


class StagedAssets(dict):
    """Keep large world builds on disk instead of retaining every texture in RAM."""
    def __init__(self, directory):
        super().__init__()
        self.directory = Path(directory)

    def __setitem__(self, name, payload):
        path = self.directory / str(len(self))
        if name in self:
            path = self[name]
        path.write_bytes(payload)
        super().__setitem__(name, path)


def asset_pack(stack, library, prefix, cache_dir=None):
    shader_maps = []
    for game in ("KingdomComeDeliverance", "KingdomComeDeliverance2"):
        features = {}
        for path in (library / game / "Engine").glob("*.pak"):
            archive = stack.enter_context(zipfile.ZipFile(path))
            for name in archive.namelist():
                if name.lower().endswith(".ext"):
                    features[PurePosixPath(name.replace("\\", "/")).stem.lower()] = shader_features(read(archive, name).decode(errors="replace"))
        shader_maps.append(features)
    index = {}
    paths = [p for p in (library / "KingdomComeDeliverance/Data").glob("*.pak") if p.name.startswith(("GameData", "Textures", "Objects", "Buildings", "Characters", "Cloth", "Heads"))]
    paths.extend((library / "KingdomComeDeliverance/Engine").glob("*.pak"))
    paths = sorted(paths, key=lambda p: ("_HD" in p.name,p.name.lower()))
    paths.extend(sorted((library / "KingdomComeDeliverance/Data/patch").glob("*.pak")))
    for path in paths:
        archive = stack.enter_context(zipfile.ZipFile(path))
        for entry in archive.infolist():
            name = entry.filename.replace("\\", "/").lower()
            if name.endswith((".cgf", ".mtl")) or ".dds" in name:
                index[name] = (archive,entry)

    def asset_read(name):
        archive,entry = index[name]
        return read(archive,entry.filename)

    emitted = StagedAssets(cache_dir) if cache_dir is not None else {}
    families = {}
    for member in index:
        match = re.fullmatch(r"(.+\.dds)(\.(?:a|[0-9]+a?))?", member)
        if match:
            families.setdefault(match[1], []).append((member, match[2] or ""))
    texture_names = {}
    material_names = {}

    def material(name):
        name = name.replace("\\", "/").lower().removesuffix(".mtl")
        if name in material_names:
            return material_names[name]
        target = prefix + "m" + str(len(material_names))
        material_names[name] = target
        doc = ET.fromstring(asset_read(name+".mtl"))
        for element in doc.iter("Material"):
            shader = element.get("Shader", "").lower()
            if shader in shader_maps[0] and shader in shader_maps[1]:
                convert_material_features(element, shader_maps[0][shader], shader_maps[1][shader])
            else:
                element.attrib.pop("GenMask", None)
        for tex in doc.iter("Texture"):
            original = tex.get("File", "").replace("\\", "/").lower()
            if not original or original.startswith("$"):
                continue
            original = str(PurePosixPath(original).with_suffix(".dds"))
            if original not in index and original.startswith("data/") and original[5:] in index:
                original = original[5:]
            if original not in texture_names:
                # CryEngine uses semantic suffixes such as _ddna to
                # recognize normal/gloss textures; preserve the basename.
                texture_names[original] = prefix + "t" + str(len(texture_names)) + "_" + PurePosixPath(original).name
                if original not in index:
                    raise FileNotFoundError(original)
                for member, suffix in families.get(original, []):
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

    return index, emitted, material, mesh_bytes
