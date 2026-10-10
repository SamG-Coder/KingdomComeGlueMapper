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


def convert_character_materials(output, components, assets, namespace, sex):
    """Convert only materials selected by this character; no global replacements."""
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
    converted = {}; meshes = set(); report = []
    for component in components.findall('DerivedComponents/*'):
        for element in component.findall('Elements/SkinElement'):
            filename = element.get('Material')
            source_path = output / folder / filename
            if filename not in converted:
                root = ET.parse(source_path).getroot()
                slots = root.find('SubMaterials')
                originals = list(slots) if slots is not None else [root]
                results = []; has_cloth = False
                for i, material in enumerate(originals):
                    shader = material.get('Shader', '').lower()
                    stem = PurePosixPath(filename).stem + '_' + str(i)
                    prefix = folder + 'native_' + stem
                    textures = {t.get('Map'): t for t in material.findall('Textures/Texture')}
                    def pixels(slot):
                        return read_dds_family(output / textures[slot].get('File'))
                    if shader in ('humanskin', 'illum'):
                        mask = legacy_bg_mask(pixels('Decal')) if 'Decal' in textures else Image.new('RGBA', (4, 4), (0, 0, 0, 255))
                        if shader == 'illum':
                            r, g, _, a = mask.split(); mask = Image.merge('RGBA', (r, g, Image.new('L', mask.size, 192), a))
                        mask.save(output / (prefix + '_bgs.dds'))
                    if shader == 'illum':
                        spec = pixels('Specular') if 'Specular' in textures else None
                        write_float_dds(output / (prefix + '_diff.dds'), bake_macro_diffuse(pixels('Diffuse'), material, spec))
                        new = native_clothing_material(material, prefix + '_diff.dds', prefix + '_bgs.dds', cloth_ref, 'GarmentBase')
                        # Use the shipped permutation exactly, including its
                        # serialized global mask; local .ext bits are not that mask.
                        for key in ('Shader', 'GenMask', 'StringGenMask', 'MtlFlags', 'Opacity', 'vertModifType'):
                            new.set(key, cloth_ref.get(key))
                        if spec is None:
                            spec = Image.new('RGBA', (4, 4), (255, 255, 255, 255))
                        if 'COLORIZING_WITH_MASK' in material.get('StringGenMask', ''):
                            r, _, _, a = spec.split(); spec = Image.merge('RGBA', (r, r, r, a))
                        write_float_dds(output / (prefix + '_spec.dds'), bake_macro_diffuse(spec, ET.Element('Material', Diffuse=material.get('Specular', '1,1,1'))))
                        spec_slot = new.find("Textures/Texture[@Map='Specular']")
                        if spec_slot is None:
                            spec_slot = ET.SubElement(new.find('Textures'), 'Texture', Map='Specular')
                        spec_slot.set('File', prefix + '_spec.dds')
                        if has_cloth:
                            new.remove(new.find('FeatureSlots'))
                        has_cloth = True
                    elif shader == 'humanskin' and material.get('Name', '').lower() != 'mouth':
                        ref = head_ref if '%WRINKLE_BLENDING' in material.get('StringGenMask', '') else body_ref
                        new = native_skin_material(native_surface(material, ref), prefix + '_bgs.dds', ref)
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
                if model not in meshes:
                    model.write_bytes(native_vertex_features(model.read_bytes())); meshes.add(model)
    path = output / ('Libs/Tables/Character/ClothingMaterial__' + namespace + '.xml')
    path.write_bytes(xml(feature_root))
    return report
