"""Build isolated native effect assets for an already registered imported NPC.

Requires Pillow and numpy, and the user's installed game assets. Nothing from
the games is bundled. --install backs up the exact development table overlays
before selecting the generated assets; a game restart is then required.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image
from npc_effects import (read_dds_family, legacy_bg_mask, native_skin_material,
                         native_vertex_features, bake_macro_diffuse, native_clothing_material, write_float_dds)
from upgrade_map import read, xml


def build(data, namespace, revision, install=False):
    if not all(re.fullmatch(r'[A-Za-z0-9_]+', v) for v in (namespace,revision)):
        raise ValueError('Invalid namespace or revision')
    package = data/'objects/characters/gluenpc'/namespace
    output = package/revision
    if output.exists(): raise FileExistsError(output)
    component_path = data/'Libs/Tables/Character/CharacterComponent.xml'
    materials_path = data/'Libs/Tables/Character/ClothingMaterial.xml'
    components = ET.parse(component_path).getroot()
    component = components.find(f'.//Component[@Name="Glue_{namespace}"]')
    if component is None: raise ValueError('Registered imported component not found')
    if any('/' in n.get('Material','') for n in component.iter('SkinElement')):
        raise ValueError('Restore the original component overlay before rebuilding; do not convert effects twice')
    with zipfile.ZipFile(data/'Tables.pak') as z:
        materials = (ET.parse(materials_path).getroot() if materials_path.exists()
                     else ET.fromstring(read(z,'Libs/Tables/Character/ClothingMaterial.xml')))
        feature_database = ET.fromstring(read(z,'Libs/Tables/Character/ClothingFeature.xml'))
        if not any(f.get('Name')=='GarmentBase' for f in feature_database.iter('Feature')):
            raise ValueError('Installed feature database lacks GarmentBase')
    reference_archive = data/'IPL_Characters-part1.pak'
    with zipfile.ZipFile(reference_archive) as z:
        skin_ref = ET.fromstring(read(z,'Objects/characters/humans/male/body/male_body_mat.mtl'))
        cloth_ref = ET.fromstring(read(z,'Objects/characters/humans/male/clothing/arms/arms_splited/tuniclong01_arms/tuniclong01_m01_arms.mtl'))
    with zipfile.ZipFile(data/'IPL_Heads-part0.pak') as z:
        native_head = ET.fromstring(read(z,'Objects/characters/humans/male/head/m_head_000/m_head_000_b00.mtl'))
        head_ref = next(m for m in native_head.iter('Material') if m.get('Shader','').lower()=='humanskin')
    feature_name = 'Glue_'+namespace+'_'+revision
    if any(m.get('Name') == feature_name for m in materials.iter('Material')):
        raise ValueError('Effect feature already registered')
    # Installed atlas entry 0 is neutral grey, flat normal and white BGS. A
    # neutral feature keeps the legacy macro maps visible. Wear currently uses
    # a darker native secondary feature, not source damage-map reproduction.
    ET.SubElement(materials.find('ClothingMaterials'),'Material',
                  Name=feature_name,TextureId='0',Hue='0',Saturation='1',Brightness='2.316',
                  Specular='#FFFFFF',Smoothness='128',ScratchTextureId='0',
                  ScratchHue='0',ScratchSaturation='1',ScratchBrightness='1.25',
                  GrimeDiffuse='#493528',GrimeSpecular='#161616',GrimeGloss='0.04')
    output.mkdir()
    prefix = 'objects/characters/gluenpc/'+namespace+'/'+revision+'/'
    rows, converted = [], {}

    def texture_path(texture):
        file = texture.get('File').replace('\\','/')
        if file.startswith('./'): return package/file[2:]
        path = data/file
        if path.suffix.lower() != '.dds': path = path.with_suffix('.dds')
        return path

    for node in component.iter('SkinElement'):
        filename = node.get('Material')
        if not filename: continue
        if filename not in converted:
            src = package/filename
            root = ET.parse(src).getroot()
            slots = root.find('SubMaterials')
            slots = list(slots) if slots is not None else [root]
            converted_slots = []
            feature_slot_declared = False
            for number, material in enumerate(slots):
                shader = material.get('Shader','').lower()
                if shader not in ('illum','humanskin'):
                    converted_slots.append(copy.deepcopy(material)); continue
                stem = Path(filename).stem+'_'+str(number)
                textures = {t.get('Map'): t for t in material.findall('Textures/Texture')}
                legacy = textures.get('Decal')
                if legacy is not None:
                    mask = legacy_bg_mask(read_dds_family(texture_path(legacy)))
                    mask_source = legacy.get('File')
                else:
                    # The mouth uses HumanSkin too, but teeth are not exposed
                    # body skin. Do not paint them with a generic body fallback.
                    mouth = material.get('Name','').lower() == 'mouth'
                    mask = Image.new('RGBA',(4,4),(0,0,0,255) if mouth else (192,192,0,255))
                    mask_source = ('Mouth excluded from exterior skin effects' if mouth else
                                   'Explicit uniform fallback: no authored source BG map')
                if shader == 'illum':
                    channels = list(mask.split())
                    channels[2] = Image.new('L', mask.size, 192)
                    mask = Image.merge('RGBA',channels)
                mask_name = stem+'_bgs.dds'
                mask.save(output/mask_name)
                if shader == 'humanskin':
                    reference = head_ref if material.get('Name','').lower()=='head' else skin_ref
                    converted_material = native_skin_material(material,prefix+mask_name,reference)
                else:
                    diffuse = textures.get('Diffuse')
                    if diffuse is None: raise ValueError('Illum surface has no diffuse texture')
                    spec = textures.get('Specular')
                    spec_image = read_dds_family(texture_path(spec)) if spec is not None else None
                    diffuse_name = stem+'_macro_diff.dds'
                    write_float_dds(output/diffuse_name,bake_macro_diffuse(read_dds_family(texture_path(diffuse)),material,spec_image))
                    converted_material = native_clothing_material(material,prefix+diffuse_name,prefix+mask_name,cloth_ref,'GarmentBase')
                    if spec_image is None:
                        spec_image = Image.new('RGBA',(4,4),(255,255,255,255))
                    if 'COLORIZING_WITH_MASK' in material.get('StringGenMask',''):
                        r,_,_,a = spec_image.split()
                        spec_image = Image.merge('RGBA',(r,r,r,a))
                    spec_name = stem+'_macro_spec.dds'
                    spec_settings = ET.Element('Material',Diffuse=material.get('Specular','1,1,1'))
                    write_float_dds(output/spec_name,bake_macro_diffuse(spec_image,spec_settings))
                    spec_binding = converted_material.find('Textures/Texture[@Map="Specular"]')
                    if spec_binding is None:
                        spec_binding = ET.SubElement(converted_material.find('Textures'),'Texture',Map='Specular')
                        converted_material.set('StringGenMask',converted_material.get('StringGenMask')+'%SPECULAR_MAP')
                    spec_binding.set('File',prefix+spec_name)
                    # Feature indices belong to the whole multi-material, not
                    # each submaterial. Repeated declarations trigger native
                    # validation errors even when they name the same feature.
                    if feature_slot_declared:
                        converted_material.remove(converted_material.find('FeatureSlots'))
                    feature_slot_declared = True
                converted_slots.append(converted_material)
                rows.append(dict(material=filename,slot=number,shader=shader,mask_source=mask_source,
                                 wear_mask='Uniform fallback' if shader=='illum' else None))
            if root.find('SubMaterials') is None:
                root = converted_slots[0]
            else:
                root.find('SubMaterials')[:] = converted_slots
            (output/Path(filename).name).write_bytes(xml(root))
            converted[filename] = revision+'/'+Path(filename).name
        node.set('Material',converted[filename])
        if filename.startswith('outfit_'):
            model = node.get('Model')
            destination = output/Path(model).name
            if not destination.exists(): destination.write_bytes(native_vertex_features((package/model).read_bytes()))
            node.set('Model',revision+'/'+destination.name)
    outfit = component.find(f'.//Clothing[@Name="Glue_{namespace}_outfit"]')
    if outfit is None: raise ValueError('Assembled outfit component required')
    features = outfit.find('Features')
    if features is None: features = ET.SubElement(outfit,'Features')
    ET.SubElement(features,'Feature',Name='GarmentBase',Material=feature_name)
    (output/'CharacterComponent.xml').write_bytes(xml(components))
    (output/'ClothingMaterial.xml').write_bytes(xml(materials))
    report = dict(namespace=namespace,revision=revision,materials=rows,installed=install,
                  runtime_verified=False, limitations=[
                      'Fixed outfit shares one native condition value',
                      'Uniform wear mask and neutral darker wear feature are provisional',
                      'No geometric tears are generated',
                      'Native detail atlas replaces legacy tiled detail maps'])
    (output/'effects-report.json').write_text(json.dumps(report,indent=2))
    if install:
        (output/'CharacterComponent.before.xml').write_bytes(component_path.read_bytes())
        if materials_path.exists():
            (output/'ClothingMaterial.before.xml').write_bytes(materials_path.read_bytes())
        component_path.write_bytes(xml(components))
        materials_path.write_bytes(xml(materials))
    return output


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tools',type=Path,default=Path('D:/SteamLibrary/steamapps/common/KCD2Mod'))
    p.add_argument('--namespace',default='martin_v1')
    p.add_argument('--revision',required=True)
    p.add_argument('--install',action='store_true')
    a = p.parse_args()
    print(build(a.tools/'Data',a.namespace,a.revision,a.install))
