"""Source-person appearance import using the existing male/female skin upgrader."""
from contextlib import ExitStack
import copy
from pathlib import Path
import re
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile

from audit_character_bodies import Assets
from build_npc_probe import resolve_parts, asset_path, bind_material
from build_npc_clothing_probe import native_soul_archetype
from campaign_dependency_adapters import SourceTables
from campaign_sources import RetailSourceReader
from character_skin_upgrade import upgrade_skin, limit_skin_influences, canonicalize_skin_skeletons
from clothing_appearance import apply_armor_colorization
from clothing_assembly import convert_fixed_outfit
from clothing_regions import assign_body_regions, assign_upper_garment_regions, split_skin_regions
from static_assets import asset_pack
from upgrade_map import read, xml
from region_travel_services import native_xml


def build_appearance(source, target, merchant, namespace, output):
    """Stage a namespaced fixed outfit. Never change existing NPC definitions."""
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a fresh merchant character directory')
    output.mkdir(parents=True)
    prefix = 'objects/characters/gluetravel/' + namespace + '/'
    folder = prefix.removeprefix('objects/characters/')
    with ExitStack() as stack:
        reader = stack.enter_context(RetailSourceReader(source))
        tables = SourceTables(source, reader)
        source_soul = dict(merchant['soul'])
        instance = merchant['instance'].find('StaticData')
        body = instance.find('CharacterBodyDescription')
        for kind in ('body', 'head', 'hair', 'beard'):
            value = body.findtext(kind.title() + 'Id')
            if value and value != str(uuid.UUID(int=0)):
                source_soul['character_' + kind + '_id'] = value
        source_soul['initial_clothing_preset_id'] = instance.findtext('InitialClothingDescription/PresetId')
        parts = resolve_parts(source_soul, lambda table: tables.get(table)['rows'])
        sex = {'1': 'male', '2': 'female'}[next(p['gender_id'] for p in parts if p['kind'] == 'body')]
        native = Assets(Path(target), stack)
        skeleton, rig = native.get(f'objects/characters/humans/{sex}/skeleton/{sex}.chr')
        cache = stack.enter_context(tempfile.TemporaryDirectory(prefix='merchant-assets-', dir=output.parent))
        index, emitted, material, mesh = asset_pack(stack, Path(source).parent, prefix, cache)
        document = ET.Element('database', name='barbora')
        component = ET.SubElement(ET.SubElement(document, 'CharacterComponents', version='6'), 'Component',
                                 Name=namespace + '_root', Race='Human', Gender=sex.title(), FilePath=folder)
        derived = ET.SubElement(component, 'DerivedComponents')
        appearance, garments, evidence = {}, [], []
        for number, part in enumerate(parts):
            kind = part['kind']; stem = 'part' + str(number)
            model = asset_path(index, part, 'model', '.skin')
            mat = material(asset_path(index, part, 'material', '.mtl'))
            if kind == 'cloth':
                tinted = prefix + stem + '_tinted'
                emitted[tinted + '.mtl'] = xml(apply_armor_colorization(ET.parse(emitted[mat + '.mtl']).getroot(), part['armor_settings']))
                mat = tinted
            blob, mat = bind_material(mesh(model), mat, emitted, prefix + stem + '_slots')
            blob, detail = upgrade_skin(blob, skeleton, variant=part.get('morph_target') or None,
                discard_unused_variants=kind == 'cloth', geometry_mode='preserve', clear_hiding=kind != 'cloth')
            blob, _ = limit_skin_influences(blob)
            emitted[prefix + stem + '.mtl'] = emitted[mat + '.mtl'].read_bytes()
            if kind == 'cloth':
                match = re.match(r's[12]_p([1-4])_', Path(part['model']).name, re.I)
                if not match:
                    raise ValueError('Unmapped source garment body part: ' + part['model'])
                region = {'1': 'head', '2': 'torso', '3': 'legs', '4': 'feet'}[match[1]]
                garments.append(dict(name=part['clothing_name'], skin=blob, region=region,
                    material=ET.parse(emitted[prefix + stem + '.mtl']).getroot()))
            else:
                name = namespace + '_' + kind; appearance[kind] = name
                node = ET.SubElement(derived, kind.title(), Name=name)
                elements = ET.SubElement(node, 'Elements')
                regions = split_skin_regions(blob, assign_body_regions(blob)) if kind == 'body' and sex == 'male' else {
                    {'body': 'torso', 'head': 'face', 'hair': 'head', 'beard': 'beard'}[kind]: blob}
                for region, skin in regions.items():
                    filename = stem + '_' + region + '.skin'; emitted[prefix + filename] = skin
                    ET.SubElement(elements, 'SkinElement', EquipmentPart=region, Model=filename,
                                  Material=stem + '.mtl', BodyLayerId='0', KeepBodyLayer='true', IsFinalLayer='false')
            evidence.append(dict(kind=kind, model=model, clothing=part.get('clothing_name'), upgrade=detail))
        if 'beard' not in appearance:
            appearance['beard'] = namespace + '_empty_beard'
            ET.SubElement(derived, 'Beard', Name=appearance['beard'])
        # Canonical palette and native body regions are shared with the accepted
        # generic character upgrade, preserving source geometry across all parts.
        canonical = canonicalize_skin_skeletons([g['skin'] for g in garments])
        from character_skin_upgrade import CompiledSkin, matrix
        bones = CompiledSkin(canonical[0]).info['bones']
        waist = float(matrix(next(b for b in bones if b['name'] == 'Hips')['bind'])[2, 3])
        for garment, blob in zip(garments, canonical):
            garment['skin'] = blob
            if garment['region'] == 'torso':
                garment['face_regions'] = assign_upper_garment_regions(blob, waist_z=waist)
        outfit = convert_fixed_outfit(garments, prefix + 'outfit_')
        with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
            components = ET.fromstring(read(archive, 'Libs/Tables/Character/CharacterComponent.xml'))
            archetypes = ET.fromstring(read(archive, 'Libs/Tables/rpg/soul_archetype.xml'))
            template = next(n for n in components.iter('Clothing') if n.get('Name') == 'Coat' and n.get('ArmorType') == 'Coat')
            parents = {child: parent for parent in components.iter() for child in parent}
            ancestor = template
            while not ancestor.get('ArmorArchetypeId'):
                ancestor = parents[ancestor]
            armor_archetype = ancestor.get('ArmorArchetypeId')
            # New item rows have no base row to inherit required RPG fields
            # from. Copy a complete native coat definition before assigning
            # the imported outfit's unique identity and component.
            native_items = native_xml(read(archive, 'Libs/Tables/item/item.xml'))
            outfit_item_template = next(n for n in native_items.iter('Armor')
                                        if n.get('Clothing', '').startswith('Coat'))
        outfit_name = namespace + '_outfit'
        node = ET.SubElement(derived, 'Clothing', Name=outfit_name, ArmorType=template.get('ArmorType'),
                             ArmorArchetypeId=armor_archetype)
        elements = ET.SubElement(node, 'Elements')
        for region, value in outfit.items():
            stem = 'outfit_' + region
            emitted[prefix + stem + '.skin'] = value['skin']; emitted[prefix + stem + '.mtl'] = xml(value['material'])
            ET.SubElement(elements, 'SkinElement', EquipmentPart=region, Model=stem + '.skin', Material=stem + '.mtl',
                          BodyLayerId='9', KeepBodyLayer=str(sex == 'female').lower(), IsFinalLayer='true')
        item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/travel/merchant-outfit/' + merchant['soul']['soul_id']))
        item_root = ET.Element('database', name='barbora')
        item = copy.deepcopy(outfit_item_template)
        item.attrib.update(Id=item_id, Name=outfit_name, Clothing=outfit_name,
                           MaxStatus='100', Weight='1', Price='1')
        ET.SubElement(item_root, 'ItemClasses', version='8').append(item)
        preset_root = ET.Element('database', name='barbora')
        preset = ET.SubElement(ET.SubElement(preset_root, 'InventoryPresets', version='2'), 'InventoryPreset',
                              Name=namespace + '_clothes', Mode='All', Health='1')
        ET.SubElement(preset, 'ClothingPresetRef', Name=namespace + '_outfit')
        clothing_root = ET.Element('database', name='barbora')
        clothing_preset = ET.SubElement(ET.SubElement(clothing_root, 'clothing_presets', version='2'),
            'clothing_preset', clothing_preset_id=str(uuid.uuid5(uuid.NAMESPACE_URL, item_id)),
            clothing_preset_name=namespace + '_outfit', gender=sex.title(), prefers_hood_on='false')
        ET.SubElement(ET.SubElement(clothing_preset, 'Items'), 'Guid').text = item_id
        for name, data in {**emitted, 'Libs/Tables/Character/CharacterComponent__' + namespace + '.xml': xml(document),
            'Libs/Tables/item/item__' + namespace + '.xml': xml(item_root),
            'Libs/Tables/item/clothing_preset__' + namespace + '.xml': xml(clothing_root),
            'Libs/Tables/item/InventoryPreset__' + namespace + '_clothes.xml': xml(preset_root)}.items():
            dest = output / name; dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data.read_bytes() if isinstance(data, Path) else data)
    return dict(appearance=appearance, inventory=namespace + '_clothes', gender=sex,
                archetype=native_soul_archetype(archetypes, sex.title()), parts=evidence, rig=rig,
                outfit_mode='Fixed source outfit, using the existing skin upgrader', runtime_verified=False)
