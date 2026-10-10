"""Isolated CGF/material/texture packaging shared by static-object probes."""
from pathlib import Path, PurePosixPath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from upgrade_map import read, xml
from build_water_probe import shader_features, translate_features


def preserve_legacy_decal_shadows(element, retained):
    """Optional comparison for KCD1 decal self-shadowing; not a default conversion."""
    if (element.get("Shader", "").lower() == "illum" and "%DECAL" in retained
            and retained & {"%PARALLAX_OCCLUSION_MAPPING", "%OFFSET_BUMP_MAPPING",
                            "%SILHOUETTE_PARALLAX_OCCLUSION_MAPPING"}):
        # KCD1 CommonZPass excludes decals from POM self-shadowing. KCD2
        # intentionally enables it (KCD2-130666), so old, previously ignored
        # strengths become active. Preserve the source behavior while keeping
        # its height map, displacement, normals and ordinary scene shadows.
        params = element.find("PublicParams")
        if params is None:
            params = ET.SubElement(element, "PublicParams")
        params.set("SelfShadowStrength", "0")
        return True
    return False


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


def _asset_library(stack, library):
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
    paths = [p for p in (library / "KingdomComeDeliverance/Data").glob("*.pak") if p.name.startswith(("GameData", "Textures", "Objects", "Buildings", "Characters", "Cloth", "Heads", "IPL_Heads"))]
    paths.extend((library / "KingdomComeDeliverance/Engine").glob("*.pak"))
    paths = sorted(paths, key=lambda p: ("_HD" in p.name,p.name.lower()))
    paths.extend(sorted((library / "KingdomComeDeliverance/Data/patch").glob("*.pak")))
    for path in paths:
        archive = stack.enter_context(zipfile.ZipFile(path))
        for entry in archive.infolist():
            name = entry.filename.replace("\\", "/").lower()
            if name.endswith((".cgf", ".cga", ".cdf", ".chr", ".skin", ".mtl")) or ".dds" in name:
                index[name] = (archive,entry)
    level_archive = stack.enter_context(zipfile.ZipFile(library / 'KingdomComeDeliverance/Data/Levels/rataje/level.pak'))
    for entry in level_archive.infolist():
        name = entry.filename.replace('\\','/').lower()
        if name.endswith(('.cgf','.mtl')) or '.dds' in name:
            index['%level%/'+name] = (level_archive,entry)

    families = {}
    for member in index:
        match = re.fullmatch(r"(.+\.dds)(\.(?:a|[0-9]+a?))?", member)
        if match:
            families.setdefault(match[1], []).append((member, match[2] or ""))
    return index, shader_maps, families


def asset_pack(stack, library, prefix, cache_dir=None, *, library_cache=None, texture_cache=None):
    """Optionally reuse archive indexes and texture files across a population.

    The owner must keep stack and the shared texture directory alive until every
    character has been written. Material files remain local to each conversion.
    """
    if library_cache is None:
        index, shader_maps, families = _asset_library(stack, library)
    else:
        if 'library' not in library_cache:
            library_cache['library'] = _asset_library(stack, library)
        index, shader_maps, families = library_cache['library']
    def asset_read(name):
        archive, entry = index[name]
        return read(archive, entry.filename)
    emitted = StagedAssets(cache_dir) if cache_dir is not None else {}
    texture_names = {}
    material_names = {}

    def material(name):
        name = name.replace("\\", "/").lower().removesuffix(".mtl")
        if name+'.mtl' not in index and 'objects/'+name+'.mtl' in index:
            name = 'objects/'+name
        if name in material_names:
            return material_names[name]
        target = prefix + "m" + str(len(material_names))
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
            # CryEngine's nearest probe is a runtime texture token, not an
            # asset filename (some KCD1 character materials omit the '$').
            if original in ('nearest_cubemap', 'nearest_cubemap.dds'):
                continue
            original = str(PurePosixPath(original).with_suffix(".dds"))
            if original not in index and original.startswith("data/") and original[5:] in index:
                original = original[5:]
            if original not in texture_names:
                # CryEngine uses semantic suffixes such as _ddna to
                # recognize normal/gloss textures; preserve the basename.
                if original not in index:
                    raise FileNotFoundError(original)
                if texture_cache is None:
                    texture_names[original] = prefix + "t" + str(len(texture_names)) + "_" + PurePosixPath(original).name
                    for member, suffix in families.get(original, []):
                        emitted[texture_names[original]+suffix] = asset_read(member)
                elif hasattr(texture_cache, 'source_family'):
                    target_texture, payloads = texture_cache.source_family(original, families.get(original, []), asset_read)
                    texture_names[original] = target_texture
                    for target_name, data in payloads.items():
                        emitted[target_name] = data
                else:
                    import hashlib
                    target_texture = 'objects/characters/gluepopulation/textures/' + hashlib.sha256(original.encode()).hexdigest()[:20] + '_' + PurePosixPath(original).name
                    texture_names[original] = target_texture
                    for member, suffix in families.get(original, []):
                        dest = texture_cache / (target_texture + suffix)
                        if not dest.exists():
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(asset_read(member))
                        # A shared disk reference, not another texture allocation.
                        dict.__setitem__(emitted, target_texture + suffix, dest)
            tex.set("File",texture_names[original])
        emitted[target+".mtl"] = xml(doc)
        material_names[name] = target
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


def character_definition(index, emitted, material, mesh_bytes, name, target):
    """Package a simple rigid-prop CDF and its bind-pose geometry, not gameplay.

    Bone attachments and nested definitions need explicit local transforms;
    reject them instead of silently flattening or dropping parts.
    """
    key = name.replace('\\', '/').lower()
    archive, entry = index[key]
    doc = ET.fromstring(read(archive, entry.filename))
    if doc.tag != 'CharacterDefinition':
        raise ValueError('Expected character definition')
    model = doc.find('Model')
    if model is None or not model.get('File'):
        raise ValueError('Character definition has no model')
    refs = [(model, 'File')]
    for attachment in doc.findall('AttachmentList/Attachment'):
        if attachment.get('Type') == 'CA_PROX' and not attachment.get('Binding'):
            # Inline bone proxy parameters have no external asset dependency.
            # Keep their authored bone, rotation and position unchanged.
            continue
        if attachment.get('Type') != 'CA_SKIN' or not attachment.get('Binding'):
            raise ValueError('Unsupported non-skin character attachment')
        refs.append((attachment, 'Binding'))
    for number, (element, attribute) in enumerate(refs):
        original = element.get(attribute).replace('\\', '/').lower()
        if original.startswith('./'):
            original = str(PurePosixPath(key).parent / original[2:])
        if not original.endswith(('.chr', '.skin', '.cga')):
            raise ValueError('Unsupported character geometry reference')
        renamed = target[:-4] + '_part' + str(number) + PurePosixPath(original).suffix
        emitted[renamed] = mesh_bytes(original)
        element.set(attribute, renamed)
        override = element.get('Material')
        if override:
            element.set('Material', material(override))
    emitted[target] = xml(doc)
