"""Source-person appearance import using the existing male/female skin upgrader."""
from contextlib import ExitStack
import copy
from pathlib import Path
import re
import struct
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
import numpy as np

from audit_character_bodies import Assets, inspect_mesh
from build_npc_probe import resolve_parts, asset_path, bind_material
from build_npc_clothing_probe import native_soul_archetype
from campaign_dependency_adapters import SourceTables
from campaign_sources import RetailSourceReader
from character_skin_upgrade import upgrade_skin, limit_skin_influences, canonicalize_skin_skeletons, read_morphs, spine_mapping
from character_landmark_warp import anatomical_warp
from audit_character_bodies import bone_table
from clothing_appearance import apply_armor_colorization
from clothing_assembly import convert_fixed_outfit
from clothing_regions import assign_body_regions, assign_upper_garment_regions, split_skin_regions, read_chunks, write_chunks
from static_assets import asset_pack
from upgrade_map import read, xml
from character_person import native_xml
from character_source_clothing import female_role, native_role, source_item, garment_layout
from character_native_materials import convert_character_materials


def keep_body_under_outfit(sex, region):
    # A region is not a coverage mask: sleeves occupy arms but do not cover
    # bare forearms. Keep source arms/hands underneath the fixed outfit.
    return sex == 'female' or region in ('arms', 'hands')


def align_internal_translation(blob):
    """Normalize a small uniform source export offset without moving its surface.

    Some source garments store internal bind positions a few millimetres away
    from every corresponding render position. Only a uniform translation is
    supported; deformed, ambiguous or large discrepancies still fail closed.
    """
    _, vertices, _ = inspect_mesh(blob)
    chunks = read_chunks(blob)
    internal = [c for c in chunks if c.kind == 0x2005]
    remaps = [c for c in chunks if c.kind == 0x2006]
    if len(internal) != 1 or len(remaps) != 1:
        raise ValueError('Expected one internal position buffer and remap')
    internal = internal[0]
    count = (len(internal.data)-32)//64
    remap = np.frombuffer(remaps[0].data, dtype='<u2')
    if internal.version != 0x800 or (len(internal.data)-32)%64 or len(remap) != len(vertices) or np.any(remap >= count):
        raise ValueError('Unsupported internal position mapping')
    positions = np.array([struct.unpack_from('<3f', internal.data, 32+64*i+12) for i in range(count)])
    difference = np.asarray(vertices) - positions[remap]
    if np.allclose(difference, 0, rtol=0, atol=1e-5):
        return blob, [0., 0., 0.]
    offset = np.median(difference, axis=0)
    if not np.isfinite(offset).all() or np.linalg.norm(offset) > .01 or not np.allclose(difference, offset, rtol=0, atol=1e-6):
        raise ValueError('Source render/internal difference is not a small uniform translation')
    payload = bytearray(internal.data)
    for i, pos in enumerate(positions + offset):
        struct.pack_into('<3f', payload, 32+64*i+12, *pos)
    return write_chunks(chunks, {internal.id: bytes(payload)}), offset.tolist()


def build_appearance(source, target, person, namespace, output):
    """Stage a namespaced fixed outfit. Never change existing NPC definitions."""
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a fresh person character directory')
    output.mkdir(parents=True)
    prefix = 'objects/characters/gluetravel/' + namespace + '/'
    folder = prefix.removeprefix('objects/characters/')
    with ExitStack() as stack:
        reader = stack.enter_context(RetailSourceReader(source))
        tables = SourceTables(source, reader)
        source_soul = dict(person['soul'])
        instance = person['instance'].find('StaticData')
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
        cache = stack.enter_context(tempfile.TemporaryDirectory(prefix='person-assets-', dir=output.parent))
        index, emitted, material, mesh = asset_pack(stack, Path(source).parent, prefix, cache)
        geometry_field = None
        if sex == 'female':
            source_body = next(p for p in parts if p['kind'] == 'body')
            body_blob = mesh(asset_path(index, source_body, 'model', '.skin'))
            mapping = spine_mapping(bone_table(read_chunks(body_blob)), bone_table(read_chunks(skeleton)))
            geometry_field = anatomical_warp(body_blob, skeleton, mapping)
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
            blob, internal_offset = align_internal_translation(blob)
            fitting = None
            if sex == 'female' and kind == 'cloth':
                _, source_region, source_layer = garment_layout(part)
                covered = any(p['kind'] == 'cloth' and garment_layout(p)[1] == source_region
                              and garment_layout(p)[2] > source_layer for p in parts)
                morphs = [m['name'] for c in read_chunks(blob) if c.kind == 0x2002 for m in read_morphs(c)]
                if covered and '#A_Shrink' in morphs:
                    fitting = {'#A_Shrink': 1.0}
            blob, detail = upgrade_skin(blob, skeleton, variant=part.get('morph_target') or None,
                discard_unused_variants=kind == 'cloth', geometry_mode='preserve', morph_weights=fitting,
                clear_hiding=kind != 'cloth' or sex == 'female', geometry_field=geometry_field)
            blob, _ = limit_skin_influences(blob)
            emitted[prefix + stem + '.mtl'] = emitted[mat + '.mtl'].read_bytes()
            if kind == 'cloth':
                match = re.match(r's[12]_p([1-4])_', Path(part['model']).name, re.I)
                if not match:
                    raise ValueError('Unmapped source garment body part: ' + part['model'])
                region = {'1': 'head', '2': 'torso', '3': 'legs', '4': 'feet'}[match[1]]
                garments.append(dict(name=part['clothing_name'], skin=blob, region=region, part=part, stem=stem,
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
            evidence.append(dict(kind=kind, model=model, clothing=part.get('clothing_name'), upgrade=detail,
                                 internal_translation=internal_offset))
        if 'beard' not in appearance:
            appearance['beard'] = namespace + '_empty_beard'
            ET.SubElement(derived, 'Beard', Name=appearance['beard'])
        if sex == 'female':
            return finish_female(source, target, tables, namespace, output, prefix, emitted,
                                 document, derived, appearance, garments, evidence, rig, native)
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
                          BodyLayerId='9', KeepBodyLayer=str(keep_body_under_outfit(sex, region)).lower(), IsFinalLayer='true')
        item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/travel/merchant-outfit/' + person['soul']['soul_id']))
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


def finish_female(source, target, tables, namespace, output, prefix, emitted,
                  document, derived, appearance, garments, evidence, rig, native):
    """Register female source garments separately using native female roles."""
    with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
        components = native_xml(read(archive, 'Libs/Tables/Character/CharacterComponent.xml'))
        native_items = native_xml(read(archive, 'Libs/Tables/item/item.xml'))
        archetypes = native_xml(read(archive, 'Libs/Tables/rpg/soul_archetype.xml'))
    with zipfile.ZipFile(Path(source) / 'Localization/English_xml.pak') as archive:
        source_strings = ET.fromstring(read(archive, 'text_ui_items.xml'))
    item_root = ET.Element('database', name='barbora')
    items = ET.SubElement(item_root, 'ItemClasses', version='8')
    clothing_root = ET.Element('database', name='barbora')
    preset = ET.SubElement(ET.SubElement(clothing_root, 'clothing_presets', version='2'), 'clothing_preset',
        clothing_preset_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/person/' + namespace + '/outfit')),
        clothing_preset_name=namespace + '_outfit', gender='Female', prefers_hood_on='false')
    ids = ET.SubElement(preset, 'Items')
    localization = ET.Element('Table'); item_report = []; registered = set()
    for garment in garments:
        part, stem = garment['part'], garment['stem']
        if part['armor_id'] in registered:
            raise ValueError('Multi-component source garment requires an explicit grouped registration')
        registered.add(part['armor_id'])
        armor_type, region, layer = female_role(part)
        archetype, template = native_role(components, native_items, armor_type)
        name = namespace + '_' + stem
        node = ET.SubElement(derived, 'Clothing', Name=name, ArmorType=armor_type, ArmorArchetypeId=archetype)
        emitted[prefix + stem + '.skin'] = garment['skin']
        ET.SubElement(ET.SubElement(node, 'Elements'), 'SkinElement', EquipmentPart=region,
            Model=stem + '.skin', Material=stem + '.mtl', BodyLayerId=str(layer),
            KeepBodyLayer='true', IsFinalLayer='false')
        item, texts = source_item(tables, part['armor_id'], namespace, name, template, source_strings)
        items.append(item); localization.extend(texts)
        ET.SubElement(ids, 'Guid').text = item.get('Id')
        item_report.append(dict(source_id=part['armor_id'], item_id=item.get('Id'), component=name,
            source_model=part['model'], source_material=part['material'], native_role=armor_type))
    # KCD1's female base is limbs only. Its innermost dress supplies the torso
    # underneath the outer garment, including the neckline. Use that source
    # geometry for native underwear instead of borrowing a KCD2 torso.
    torso = [g for g in garments if garment_layout(g['part'])[1] == 2]
    if torso:
        inner = min(torso, key=lambda g: garment_layout(g['part'])[2])
        appearance['underwear'] = namespace + '_underwear'
        underwear = ET.SubElement(derived, 'Clothing', Name=appearance['underwear'])
        ET.SubElement(ET.SubElement(underwear, 'Elements'), 'SkinElement', EquipmentPart='torso',
            BodyLayerId='8', Model=inner['stem'] + '.skin', Material=inner['stem'] + '.mtl',
            KeepBodyLayer='true', IsFinalLayer='false')
    preset_root = ET.Element('database', name='barbora')
    inventory = ET.SubElement(ET.SubElement(preset_root, 'InventoryPresets', version='2'), 'InventoryPreset',
                              Name=namespace + '_clothes', Mode='All', Health='1')
    ET.SubElement(inventory, 'ClothingPresetRef', Name=namespace + '_outfit')
    additions = {
        'Libs/Tables/Character/CharacterComponent__' + namespace + '.xml': xml(document),
        'Libs/Tables/item/item__' + namespace + '.xml': xml(item_root),
        'Libs/Tables/item/clothing_preset__' + namespace + '.xml': xml(clothing_root),
        'Libs/Tables/item/InventoryPreset__' + namespace + '_clothes.xml': xml(preset_root),
        'Localization/English/text_ui_items.xml': xml(localization),
    }
    for name, data in {**emitted, **additions}.items():
        dest = output / name; dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data.read_bytes() if isinstance(data, Path) else data)
    material_report = convert_character_materials(output, document.find('CharacterComponents/Component'),
                                                  native, namespace, 'female')
    (output / ('Libs/Tables/Character/CharacterComponent__' + namespace + '.xml')).write_bytes(xml(document))
    return dict(appearance=appearance, inventory=namespace + '_clothes', gender='female',
        archetype=native_soul_archetype(archetypes, 'Female'), parts=evidence, rig=rig,
        items=item_report, outfit_mode='Source female items and complete garment meshes', runtime_verified=False,
        localization='Localization/English/text_ui_items.xml', materials=material_report)
