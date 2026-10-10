"""Source-person appearance import using the existing male/female skin upgrader."""
from contextlib import ExitStack
import copy
import os
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
    """Normalize a uniform source export offset without moving its surface.

    Some source garments store internal bind positions a few millimetres away
    from every corresponding render position (some gauntlets exceed 8 cm).
    Only a uniform translation is supported; deformed or ambiguous mappings
    still require a separate adapter. Render vertices remain byte-identical.
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
    if not np.isfinite(offset).all() or not np.allclose(difference, offset, rtol=0, atol=1e-6):
        raise ValueError('Source render/internal difference is not a uniform translation')
    payload = bytearray(internal.data)
    for i, pos in enumerate(positions + offset):
        struct.pack_into('<3f', payload, 32+64*i+12, *pos)
    return write_chunks(chunks, {internal.id: bytes(payload)}), offset.tolist()


class AppearanceSession:
    """Shared readers/indexes for bulk conversion with the same accepted upgrader."""
    def __init__(self, source, target, cache):
        self.source, self.target = Path(source), Path(target)
        self.cache = cache if hasattr(cache, 'source_family') else Path(cache)
        self.library_cache = {}
        self.skeletons, self.geometry_fields, self.upgraded_skins, self.source_skins = {}, {}, {}, {}
    def __enter__(self):
        self.stack = ExitStack()
        self.reader = self.stack.enter_context(RetailSourceReader(self.source))
        self.tables = SourceTables(self.source, self.reader)
        self.native = Assets(self.target, self.stack)
        return self
    def __exit__(self, *args):
        return self.stack.__exit__(*args)


def write_asset(dest, data):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, Path) and '.dds' in dest.name.lower():
        # Textures are immutable. Hard links let the material converter read its
        # normal local paths without duplicating the shared population cache.
        os.link(data, dest)
    else:
        dest.write_bytes(data.read_bytes() if isinstance(data, Path) else data)


def resolve_default_body(soul, tables):
    """Expand an omitted human body into the source game's generic body record.

    Special bodies, including Theresa/Henry, are never selected by this default.
    Explicit body IDs always win. Resolve the GUID from source data, not a fixed
    actor ID or a KCD2 body, and verify race/gender against the source archetype.
    """
    if soul.get('character_body_id'): return dict(soul), None
    archetype = tables.row('rpg/soul_archetype', 'soul_archetype_id', soul['soul_archetype_id'])
    name = {'1': 'male_body_npc', '2': 'female_body'}.get(archetype['gender_id'])
    candidates = [r for r in tables.get('character_body')['rows']
                  if r['character_body_name'] == name and r['race_id'] == archetype['race_id']
                  and r['gender_id'] == archetype['gender_id']]
    if len(candidates) != 1: raise ValueError('Cannot resolve source archetype default body')
    result = dict(soul); result['character_body_id'] = candidates[0]['character_body_id']
    return result, dict(reason='omitted source body', archetype=archetype, body=candidates[0])


def resolve_exported_variant(blob, requested):
    """Resolve CryEngine's case-insensitive names and fixed-shape exports."""
    if not requested: return None, None
    names = [m['name'] for c in read_chunks(blob) if c.kind == 0x2002 for m in read_morphs(c)]
    matches = [name for name in names if name.casefold() == requested.casefold()]
    if len(matches) == 1: return matches[0], None
    if not matches and requested.casefold().startswith('#v_'):
        # Source tables include stale channels (e.g. #V_025 in a mesh ending at
        # #V_024) and fixed plate pieces with only adjustment channels. A missing
        # channel has no deltas to apply: preserve the actual exported surface,
        # record the mismatch, and never substitute some other named variant.
        return None, dict(requested=requested, exported_channels=names, geometry='fixed source export',
                          missing_source_channel=True)
    raise ValueError('Missing or ambiguous source variant: ' + requested + '; exported=' + repr(names))


def build_appearance(source, target, person, namespace, output, *, session=None):
    """Stage a namespaced fixed outfit. Never change existing NPC definitions."""
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a fresh person character directory')
    output.mkdir(parents=True)
    prefix = 'objects/characters/gluetravel/' + namespace + '/'
    folder = prefix.removeprefix('objects/characters/')
    with ExitStack() as stack:
        reader = session.reader if session else stack.enter_context(RetailSourceReader(source))
        tables = session.tables if session else SourceTables(source, reader)
        source_soul = dict(person['soul'])
        instance = person['instance'].find('StaticData')
        body = instance.find('CharacterBodyDescription')
        for kind in ('body', 'head', 'hair', 'beard'):
            value = body.findtext(kind.title() + 'Id')
            if value and value != str(uuid.UUID(int=0)):
                source_soul['character_' + kind + '_id'] = value
            elif value == str(uuid.UUID(int=0)):
                source_soul.pop('character_' + kind + '_id', None)
        source_soul['initial_clothing_preset_id'] = instance.findtext('InitialClothingDescription/PresetId')
        source_soul, default_body = resolve_default_body(source_soul, tables)
        parts = resolve_parts(source_soul, lambda table: tables.get(table)['rows'])
        archetypes_by_id = {r['armor_archetype_id']: r['armor_archetype_name']
                            for r in tables.get('item/armor_archetype')['rows']}
        modeless = []
        for part in parts:
            if part['kind'] == 'cloth':
                part['armor_archetype_name'] = archetypes_by_id[part['armor_archetype_id']]
                if not part.get('model') and not part.get('material'): modeless.append(part)
        parts = [p for p in parts if p not in modeless]
        sex = {'1': 'male', '2': 'female'}[next(p['gender_id'] for p in parts if p['kind'] == 'body')]
        native = session.native if session else Assets(Path(target), stack)
        if session:
            if sex not in session.skeletons:
                session.skeletons[sex] = native.get(f'objects/characters/humans/{sex}/skeleton/{sex}.chr')
            skeleton, rig = session.skeletons[sex]
        else:
            skeleton, rig = native.get(f'objects/characters/humans/{sex}/skeleton/{sex}.chr')
        cache = stack.enter_context(tempfile.TemporaryDirectory(prefix='person-assets-', dir=output.parent))
        index, emitted, material, mesh = asset_pack(session.stack if session else stack, Path(source).parent, prefix, cache,
            library_cache=session.library_cache if session else None, texture_cache=session.cache if session else None)
        if session:
            def mesh(name):
                if name not in session.source_skins:
                    archive, entry = index[name]
                    session.source_skins[name] = read(archive, entry.filename)
                return session.source_skins[name]
        geometry_field = None
        if sex == 'female':
            source_body = next(p for p in parts if p['kind'] == 'body')
            body_model = asset_path(index, source_body, 'model', '.skin')
            if session and body_model in session.geometry_fields:
                geometry_field = session.geometry_fields[body_model]
            else:
                body_blob = mesh(body_model)
                mapping = spine_mapping(bone_table(read_chunks(body_blob)), bone_table(read_chunks(skeleton)))
                geometry_field = anatomical_warp(body_blob, skeleton, mapping)
                if session: session.geometry_fields[body_model] = geometry_field
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
            blob = mesh(model)
            fitting = None
            if sex == 'female' and kind == 'cloth':
                _, source_region, source_layer = garment_layout(part)
                covered = any(p['kind'] == 'cloth' and garment_layout(p)[1] == source_region
                              and garment_layout(p)[2] > source_layer for p in parts)
                morphs = [m['name'] for c in read_chunks(blob) if c.kind == 0x2002 for m in read_morphs(c)]
                if covered and '#A_Shrink' in morphs:
                    fitting = {'#A_Shrink': 1.0}
            skin_key = (sex, source_soul['character_body_id'], model, kind, part.get('morph_target'),
                        tuple(sorted((fitting or {}).items())))
            cached = session.upgraded_skins.get(skin_key) if session else None
            if cached:
                blob, detail, internal_offset, variant_resolution = cached
            else:
                blob, internal_offset = align_internal_translation(blob)
                variant, variant_resolution = resolve_exported_variant(blob, part.get('morph_target'))
                blob, detail = upgrade_skin(blob, skeleton, variant=variant,
                    discard_unused_variants=kind == 'cloth', geometry_mode='preserve', morph_weights=fitting,
                    clear_hiding=kind != 'cloth' or sex == 'female', geometry_field=geometry_field)
                blob, _ = limit_skin_influences(blob)
                if session: session.upgraded_skins[skin_key] = (blob, detail, internal_offset, variant_resolution)
            blob, mat = bind_material(blob, mat, emitted, prefix + stem + '_slots')
            emitted[prefix + stem + '.mtl'] = emitted[mat + '.mtl'].read_bytes()
            if kind == 'cloth':
                _, body_region, _ = garment_layout(part)
                region = {1: 'head', 2: 'torso', 3: 'legs', 4: 'feet'}[body_region]
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
                                 internal_translation=internal_offset, variant_resolution=variant_resolution,
                                 default_body=default_body if kind == 'body' else None))
        if 'beard' not in appearance:
            appearance['beard'] = namespace + '_empty_beard'
            ET.SubElement(derived, 'Beard', Name=appearance['beard'])
        if sex == 'female':
            result = finish_female(source, target, tables, namespace, output, prefix, emitted,
                                 document, derived, appearance, garments, evidence, rig, native)
            add_modeless_clothing(source, target, tables, namespace, output, modeless, result)
            return result
        if not garments:
            # Source ownership/fight helpers may deliberately have no clothes.
            # They still need a real soul/body and an empty native inventory,
            # not a fabricated coat or a failed empty skeleton merge.
            with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
                archetypes = native_xml(read(archive, 'Libs/Tables/rpg/soul_archetype.xml'))
            preset_root = ET.Element('database', name='barbora')
            inventory = ET.SubElement(ET.SubElement(preset_root, 'InventoryPresets', version='2'),
                                     'InventoryPreset', Name=namespace + '_clothes', Mode='All', Health='1')
            if modeless: ET.SubElement(inventory, 'ClothingPresetRef', Name=namespace + '_outfit')
            additions = {'Libs/Tables/Character/CharacterComponent__' + namespace + '.xml': xml(document),
                         'Libs/Tables/item/InventoryPreset__' + namespace + '_clothes.xml': xml(preset_root)}
            if modeless:
                items = ET.Element('database', name='barbora'); ET.SubElement(items, 'ItemClasses', version='8')
                clothing = ET.Element('database', name='barbora')
                outfit = ET.SubElement(ET.SubElement(clothing, 'clothing_presets', version='2'), 'clothing_preset',
                    clothing_preset_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/person/' + namespace + '/outfit')),
                    clothing_preset_name=namespace + '_outfit', gender=sex.title(), prefers_hood_on='false')
                ET.SubElement(outfit, 'Items')
                additions['Libs/Tables/item/item__' + namespace + '.xml'] = xml(items)
                additions['Libs/Tables/item/clothing_preset__' + namespace + '.xml'] = xml(clothing)
            for name, data in {**emitted, **additions}.items(): write_asset(output / name, data)
            result = dict(appearance=appearance, inventory=namespace + '_clothes', gender=sex,
                        archetype=native_soul_archetype(archetypes, sex.title()), parts=evidence, rig=rig,
                        outfit_mode='Source actor has no garment meshes', runtime_verified=False)
            add_modeless_clothing(source, target, tables, namespace, output, modeless, result)
            return result
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
            write_asset(output / name, data)
    result = dict(appearance=appearance, inventory=namespace + '_clothes', gender=sex,
                archetype=native_soul_archetype(archetypes, sex.title()), parts=evidence, rig=rig,
                outfit_mode='Fixed source outfit, using the existing skin upgrader', runtime_verified=False)
    # tables belongs to the reader above; population sessions keep it alive.
    with RetailSourceReader(source) as reader:
        add_modeless_clothing(source, target, SourceTables(source, reader), namespace, output, modeless, result)
    return result


def add_modeless_clothing(source, target, tables, namespace, output, parts, report):
    """Inventory-only jewelry stays an item; no absent model is fabricated."""
    if not parts: return
    with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as z:
        components = native_xml(read(z, 'Libs/Tables/Character/CharacterComponent.xml'))
        native_items = native_xml(read(z, 'Libs/Tables/item/item.xml'))
    with zipfile.ZipFile(Path(source) / 'Localization/English_xml.pak') as z:
        strings = ET.fromstring(read(z, 'text_ui_items.xml'))
    item_path = output / ('Libs/Tables/item/item__' + namespace + '.xml')
    preset_path = output / ('Libs/Tables/item/clothing_preset__' + namespace + '.xml')
    items, preset = ET.parse(item_path), ET.parse(preset_path)
    text_path = output / 'Localization/English/text_ui_items.xml'
    texts = ET.parse(text_path).getroot() if text_path.exists() else ET.Element('Table')
    report['modeless_items'] = []
    for part in parts:
        role = part['armor_archetype_name']
        _, template = native_role(components, native_items, role)
        item, labels = source_item(tables, part['armor_id'], namespace, role, template, strings)
        items.getroot().find('ItemClasses').append(item)
        ET.SubElement(preset.getroot().find('clothing_presets/clothing_preset/Items'), 'Guid').text = item.get('Id')
        texts.extend(labels)
        report['modeless_items'].append(dict(source_id=part['armor_id'], item_id=item.get('Id'), role=role))
    write_asset(item_path, xml(items.getroot())); write_asset(preset_path, xml(preset.getroot()))
    write_asset(text_path, xml(texts))


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
        write_asset(output / name, data)
    material_report = convert_character_materials(output, document.find('CharacterComponents/Component'),
                                                  native, namespace, 'female')
    (output / ('Libs/Tables/Character/CharacterComponent__' + namespace + '.xml')).write_bytes(xml(document))
    return dict(appearance=appearance, inventory=namespace + '_clothes', gender='female',
        archetype=native_soul_archetype(archetypes, 'Female'), parts=evidence, rig=rig,
        items=item_report, outfit_mode='Source female items and complete garment meshes', runtime_verified=False,
        localization='Localization/English/text_ui_items.xml', materials=material_report)
