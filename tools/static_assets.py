"""Isolated CGF/material/texture packaging shared by static-object probes."""
from pathlib import Path, PurePosixPath
import copy
import os
import shutil
import struct
import xml.etree.ElementTree as ET
import zipfile
import hashlib
import re
from audit_maps import texture_families
from upgrade_map import read, xml
from build_water_probe import shader_features, translate_features
from puddle_materials import convert_puddle_proxies
from water_animation import convert_waterfall_speeds
from vegetation_materials import native_vine_deformation


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


def convert_blended_detail(element, retained):
    """KCD1 applies one detail map to both layers; KCD2 samples two maps.

    Native Illum binds detail2Tex to TM_Emittance (KCD2-130352). Leaving it
    unbound samples the engine fallback instead of the authored detail map.
    Its UV transform comes from TM_Custom, not the emittance texture slot.
    """
    if element.get('Shader','').lower()!='illum' or not retained & {'%BLENDLAYER','%DIR_OVERLAY'} or not retained & {'%DETAIL_MAPPING','%DETAIL_ATLAS'}:
        return False
    textures=element.find('Textures')
    if textures is None:return False
    detail=textures.find("Texture[@Map='Detail']")
    if detail is None or textures.find("Texture[@Map='Emittance']") is not None:return False
    secondary=copy.deepcopy(detail);secondary.set('Map','Emittance');textures.append(secondary)
    params=element.find('PublicParams')
    if params is None:params=ET.SubElement(element,'PublicParams')
    for component in ('Bump','Diffuse','Gloss'):
        params.set('Detail2'+component+'Scale',params.get('Detail'+component+'Scale','0.5'))
    custom=textures.find("Texture[@Map='Custom']")
    if custom is not None:
        old=custom.find('TexMod')
        if old is not None:custom.remove(old)
        transform=detail.find('TexMod')
        if transform is not None:custom.append(copy.deepcopy(transform))
    return True


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
                if '%BLENDLAYER' in retained and textures.find("Texture[@Map='Opacity']") is not None:
                    raise ValueError('Bake the primary-UV blend mask before native SNDUVS slot conversion')
                # KCD2 binds SNDUVSDiffuseTex to TM_Opacity. The old separate
                # tiled blend mask cannot also occupy this slot in that shader.
                for old in list(textures):
                    if old is not secondary and old.get("Map") == "Opacity":
                        textures.remove(old)
                secondary.set("Map", "Opacity")
    convert_blended_detail(element,retained)
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

    def reserve(self, name):
        """Register the staged path for a payload a background job writes later."""
        path = self[name] if name in self else self.directory / str(len(self))
        super().__setitem__(name, path)
        return path


def install_staged(emitted, root, namespace):
    """Place staged assets under their isolated namespace without writing each one twice.

    A hard link refuses an existing target like open('xb'); other volumes copy.
    """
    namespace = Path(namespace).resolve()
    for name, staged in emitted.items():
        target = (Path(root) / name).resolve()
        if not target.is_relative_to(namespace):
            raise ValueError('Asset escapes isolated namespace')
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(staged, target)
        except FileExistsError:
            raise
        except OSError:
            with target.open('xb') as out, staged.open('rb') as inp:
                shutil.copyfileobj(inp, out)


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

    def asset_read(name):
        archive,entry = index[name]
        return read(archive,entry.filename)

    emitted = StagedAssets(cache_dir) if cache_dir is not None else {}
    families = texture_families(index)
    texture_names = {}
    material_names = {}
    converted_geometry = {}
    native_vine = None

    def material(name, geometry=None, source_mesh=None):
        nonlocal native_vine
        name = name.replace("\\", "/").lower().removesuffix(".mtl")
        if name+'.mtl' not in index and 'objects/'+name+'.mtl' in index:
            name = 'objects/'+name
        from building_materials import blend_inputs, convert as convert_blend
        doc = ET.fromstring(asset_read(name+".mtl"))
        if name == 'objects/vegetation/vines/vine':
            if native_vine is None:
                native_path = 'objects/natural/vegetation/vines/vine.mtl'
                for pak in sorted((library / 'KingdomComeDeliverance2/Data').glob('*.pak')):
                    with zipfile.ZipFile(pak) as archive:
                        entry = next((n for n in archive.namelist()
                                      if n.replace('\\', '/').lower() == native_path), None)
                        if entry is not None:
                            native_vine = ET.fromstring(read(archive, entry))
                            break
                if native_vine is None:
                    raise FileNotFoundError(native_path)
            native_vine_deformation(doc, native_vine)
        convert_puddle_proxies(doc)
        convert_waterfall_speeds(doc)
        from build_recipe import reviewed_blend_mesh
        blend_slots = list(blend_inputs(doc))
        needs_geometry = bool(blend_slots) and reviewed_blend_mesh(source_mesh)
        if blend_slots and not needs_geometry:
            # Match the accepted base conversion outside the reviewed repair.
            # KCD2 has one Opacity slot: retain its secondary diffuse binding.
            # Do not broaden the two-model atlas experiment to the entire map.
            for _, element, _, mask in blend_slots:
                element.find('Textures').remove(mask)
        key = (name, hashlib.sha256(geometry).hexdigest() if geometry is not None else None) if needs_geometry else name
        if key in material_names:
            return material_names[key]
        target = prefix + "m" + str(len(material_names))
        baked = {}
        if needs_geometry:
            converted, baked = convert_blend(doc, geometry, target, asset_read, index)
            converted_geometry[target] = converted
            for path, payload in baked.items():emitted[path] = payload
        for element in doc.iter("Material"):
            shader = element.get("Shader", "").lower()
            if shader in shader_maps[0] and shader in shader_maps[1]:
                convert_material_features(element, shader_maps[0][shader], shader_maps[1][shader])
            else:
                element.attrib.pop("GenMask", None)
        for tex in doc.iter("Texture"):
            original = tex.get("File", "").replace("\\", "/").lower()
            if original in baked:
                continue
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
                texture_names[original] = prefix + "t" + str(len(texture_names)) + "_" + PurePosixPath(original).name
                for member, suffix in families.get(original, []):
                    emitted[texture_names[original]+suffix] = asset_read(member)
            tex.set("File",texture_names[original])
        emitted[target+".mtl"] = xml(doc)
        material_names[key] = target
        return target

    def mesh_bytes(name, material_override=None):
        from clothing_regions import read_chunks, write_chunks
        blob = bytearray(asset_read(name))
        base_name = re.sub(r'_lod[1-6](?=\.cgf$)', '', name, flags=re.I)
        if base_name != name and base_name in index:
            from static_lod_transforms import align_external_lod
            blob = bytearray(align_external_lod(asset_read(base_name), bytes(blob))[0])
        if blob[:4] != b"CrCh" or struct.unpack_from("<I",blob,4)[0] != 0x746:
            raise ValueError("Unsupported CGF container")
        count,table = struct.unpack_from("<II",blob,8)
        if table+count*16 > len(blob):
            raise ValueError("Invalid CGF chunk table")
        renames = {}
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
                    new = material(material_override or resolved, geometry=bytes(blob), source_mesh=name).encode()
                    if len(new)>=128:
                        raise ValueError("Material path exceeds fixed CGF field")
                    renames[chunk_id] = new
        # Repacking the vertex streams changes chunk offsets. Apply names by
        # chunk identity afterwards, never at offsets from the original file.
        changed = [converted_geometry[n.decode()] for n in set(renames.values()) if n.decode() in converted_geometry]
        if len(changed)>1:raise ValueError('Multiple blended root materials need separate mesh conversion')
        result = changed[0] if changed else bytes(blob)
        chunks = read_chunks(result)
        return write_chunks(chunks,{c.id:renames[c.id].ljust(128,b'\0')+c.data[128:] for c in chunks if c.id in renames})

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
        if attachment.get('Type') == 'CA_BONE' and not attachment.get('Binding') and attachment.get('BoneName'):
            # Authored attachment socket, e.g. a bow's Arrow bone. It has no
            # external geometry; removing it breaks runtime arrow attachment.
            continue
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
