"""Use shipped character shader configurations with imported surface data."""
import copy
from pathlib import PurePosixPath
import xml.etree.ElementTree as ET

from PIL import Image

from npc_effects import (read_dds_family, legacy_bg_mask, native_skin_material,
                         native_clothing_material, native_vertex_features,
                         bake_macro_diffuse, write_float_dds)
from upgrade_map import xml


def reference(assets, path):
    root = ET.fromstring(assets.get(path)[0])
    # Native material texture paths are often relative to the original file.
    for texture in root.iter('Texture'):
        name = texture.get('File', '').replace('\\', '/')
        if name.startswith('./'):
            texture.set('File', str(PurePosixPath(path).parent / name[2:]))
    return root


def native_surface(source, template):
    """Native shader/render flags; source colour and source surface textures."""
    result = copy.deepcopy(template)
    for key in ('Name', 'Diffuse', 'Specular', 'Shininess', 'AlphaTest'):
        if key in source.attrib:
            result.set(key, source.get(key))
    textures = result.find('Textures')
    if textures is None:
        textures = ET.SubElement(result, 'Textures')
    for texture in source.findall('Textures/Texture'):
        slot = texture.get('Map')
        # Legacy Detail on skin contains dirt-zone IDs, not native pore detail.
        if source.get('Shader', '').lower() == 'humanskin' and slot in ('Detail', 'Decal', 'Opacity'):
            continue
        previous = textures.find(f"Texture[@Map='{slot}']")
        if previous is not None:
            textures.remove(previous)
        textures.append(copy.deepcopy(texture))
    params = result.find('PublicParams')
    if params is None:
        params = ET.SubElement(result, 'PublicParams')
    original = source.find('PublicParams')
    if original is not None:
        for key in ('Melanin', 'SSSIndex', 'TranslucencyMultiplier'):
            if key in original.attrib:
                params.set(key, original.get(key))
    return result


def bake_float_dds(path, image, material, specular=None):
    write_float_dds(path, bake_macro_diffuse(image, material, specular))


def convert_character_materials(output, components, assets, namespace, sex, bake=bake_float_dds, *, preserve_hiding=False):
    """Convert only materials selected by this character; no global replacements.

    `bake(path, image, material, specular=None)` writes each float macro texture;
    callers may defer it, since nothing here reads those files back.
    """
    body_ref = reference(assets, f'objects/characters/humans/{sex}/body/' +
                         ('female_body_01.mtl' if sex == 'female' else 'male_body_mat.mtl'))
    head_doc = reference(assets, f'objects/characters/humans/{sex}/head/' +
                         ('f_head_000/f_head_000_m01.mtl' if sex == 'female' else 'm_head_000/m_head_000_b00.mtl'))
    head_ref = next(n for n in head_doc.iter('Material') if n.get('Shader', '').lower() == 'humanskin')
    hair_doc = reference(assets, 'objects/characters/humans/female/hair/f_hair_01/f_hair_01.mtl')
    hair_ref = next(n for n in hair_doc.iter('Material') if n.get('Name') == 'haircap_LOD')
    cloth_ref = reference(assets, 'objects/characters/humans/female/clothing/f_torso/f_dress/'
                          'f_simpledress/f_simpledress01_torso/f_simpledress01_m01.mtl')
    feature_name = namespace + '_source_fabric'
    feature_root = ET.Element('database', name='barbora')
    ET.SubElement(ET.SubElement(feature_root, 'ClothingMaterials', version='1'), 'Material',
        Name=feature_name, TextureId='0', Hue='0', Saturation='1', Brightness='2.316',
        Specular='#FFFFFF', Smoothness='128', ScratchTextureId='0', ScratchHue='0',
        ScratchSaturation='1', ScratchBrightness='1.25', GrimeDiffuse='#493528',
        GrimeSpecular='#161616', GrimeGloss='0.04')
    folder = 'objects/characters/' + components.get('FilePath')
    materials = {e.get('Material'): ET.parse(output / folder / e.get('Material')).getroot()
                 for e in components.findall('DerivedComponents/*/Elements/SkinElement')}
    if hasattr(bake, 'plan'):
        bake.plan(sum(2 for root in materials.values()
                      for material in (list(root.find('SubMaterials')) if root.find('SubMaterials') is not None else [root])
                      if material.get('Shader','').lower() == 'illum'))
    converted = {}; meshes = set(); report = []; effect_masks = {}
    # Region assembly can bind the same source surface in many slots. Decode
    # each source family once here; the files stay immutable during conversion.
    from collections import OrderedDict
    decoded, decoded_size = OrderedDict(), 0
    def pixels(path):
        nonlocal decoded_size
        if path in decoded:
            decoded.move_to_end(path)
            return decoded[path]
        image = read_dds_family(output / path)
        size = image.width * image.height * 4
        while decoded and decoded_size + size > 128 * 1024**2:
            _, previous = decoded.popitem(last=False)
            decoded_size -= previous.width * previous.height * 4
        if size <= 128 * 1024**2:
            decoded[path] = image; decoded_size += size
        return image
    def texture(path, image, material, specular=None):
        destination = output / path
        if hasattr(bake, 'reference'):
            return bake.reference(destination, image, material, specular).relative_to(output).as_posix()
        bake(destination, image, material, specular)
        return path
    for component in components.findall('DerivedComponents/*'):
        for element in component.findall('Elements/SkinElement'):
            filename = element.get('Material')
            source_path = output / folder / filename
            if filename not in converted:
                root = materials[filename]
                slots = root.find('SubMaterials')
                originals = list(slots) if slots is not None else [root]
                results = []; has_cloth = False
                for i, material in enumerate(originals):
                    shader = material.get('Shader', '').lower()
                    stem = PurePosixPath(filename).stem + '_' + str(i)
                    prefix = folder + 'native_' + stem
                    textures = {t.get('Map'): t for t in material.findall('Textures/Texture')}
                    def source_pixels(slot):
                        return pixels(textures[slot].get('File'))
                    if shader in ('humanskin', 'illum'):
                        # Region partitions and material aliases share these
                        # immutable inputs. Tint does not affect effect masks;
                        # write/filter/pack their identical pixels only once.
                        # Cloth and skin keep distinct blue-channel semantics.
                        mask_key = (textures['Decal'].get('File') if 'Decal' in textures else None,
                                    shader == 'illum')
                        if mask_key not in effect_masks:
                            mask = legacy_bg_mask(source_pixels('Decal')) if 'Decal' in textures else Image.new('RGBA', (4, 4), (0, 0, 0, 255))
                            if shader == 'illum':
                                r, g, _, a = mask.split(); mask = Image.merge('RGBA', (r, g, Image.new('L', mask.size, 192), a))
                            effect_masks[mask_key] = prefix + '_bgs.dds'
                            mask.save(output / effect_masks[mask_key])
                        mask_path = effect_masks[mask_key]
                    if shader == 'illum':
                        spec = source_pixels('Specular') if 'Specular' in textures else None
                        diffuse_path = texture(prefix + '_diff.dds', source_pixels('Diffuse'), material, spec)
                        new = native_clothing_material(material, diffuse_path, mask_path, cloth_ref, 'GarmentBase')
                        # Use the shipped permutation exactly, including its
                        # serialized global mask; local .ext bits are not that mask.
                        for key in ('Shader', 'GenMask', 'StringGenMask', 'MtlFlags', 'Opacity', 'vertModifType'):
                            new.set(key, cloth_ref.get(key))
                        # The donor's 0.75 macro scale leaves a 25% micro
                        # reflectance floor even for near-black source fabric.
                        # Source specular is baked below, so don't multiply it
                        # by the source material constant a second time.
                        new.find('PublicParams').set('MacroSpecularScale', '1')
                        new.set('Specular', '1,1,1')
                        if spec is None:
                            spec = Image.new('RGBA', (4, 4), (255, 255, 255, 255))
                        if 'COLORIZING_WITH_MASK' in material.get('StringGenMask', ''):
                            r, _, _, a = spec.split(); spec = Image.merge('RGBA', (r, r, r, a))
                        specular_path = texture(prefix + '_spec.dds', spec, ET.Element('Material', Diffuse=material.get('Specular', '1,1,1')))
                        spec_slot = new.find("Textures/Texture[@Map='Specular']")
                        if spec_slot is None:
                            spec_slot = ET.SubElement(new.find('Textures'), 'Texture', Map='Specular')
                        spec_slot.set('File', specular_path)
                        if has_cloth:
                            new.remove(new.find('FeatureSlots'))
                        has_cloth = True
                    elif shader == 'humanskin' and material.get('Name', '').lower() != 'mouth':
                        ref = head_ref if '%WRINKLE_BLENDING' in material.get('StringGenMask', '') else body_ref
                        new = native_skin_material(native_surface(material, ref), mask_path, ref)
                        # The native detail slot uses a new pore map. Disable its
                        # strength unless the source also authored that feature.
                        if '%DETAIL_MAPPING' not in material.get('StringGenMask', ''):
                            new.find('PublicParams').set('DetailBumpScale', '0')
                    elif shader == 'hair':
                        new = native_surface(material, hair_ref)
                    else:
                        new = copy.deepcopy(material)
                    results.append(new)
                    report.append(dict(material=filename, slot=i, source_shader=shader,
                                       target_flags=new.get('StringGenMask')))
                if slots is not None:
                    slots[:] = results
                else:
                    root = results[0]
                source_path.write_bytes(xml(root)); converted[filename] = has_cloth
            if converted[filename]:
                features = component.find('Features')
                if features is None:
                    features = ET.SubElement(component, 'Features')
                if not any(f.get('Name') == 'GarmentBase' for f in features):
                    ET.SubElement(features, 'Feature', Name='GarmentBase', Material=feature_name)
                model = output / folder / element.get('Model')
                from character_lods import source_lods
                family = [model, *(output / n for n in source_lods(
                    {p.relative_to(output).as_posix(): p for p in model.parent.glob('*.skin')},
                    model.relative_to(output).as_posix()).values())]
                for member in family:
                    if member not in meshes:
                        member.write_bytes(native_vertex_features(member.read_bytes(), preserve_hiding=preserve_hiding)); meshes.add(member)
    path = output / ('Libs/Tables/Character/ClothingMaterial__' + namespace + '.xml')
    path.write_bytes(xml(feature_root))
    return report
