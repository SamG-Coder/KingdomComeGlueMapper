"""Source-person appearance import using the existing male/female skin upgrader."""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import re
import struct
import tempfile
import time
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
from character_clothing_variant import resolve_variant
from character_source_clothing import female_role, native_role, source_item
from character_native_materials import convert_character_materials
from character_lods import source_lods, filename as lod_filename, emit_regions, validate_chain
from bake_pool import Bakes
from setup_progress import work_progress


def write_staged(output, files):
    """Place a conversion's files; staged textures are linked rather than copied.

    The staging cache is a per-person temporary folder on the same volume and is
    discarded afterwards, so a link costs one directory entry instead of a
    scanned read and write per texture. Materials and meshes are rewritten in
    place by the material conversion, so they are always real copies.
    """
    for name, data in files.items():
        dest = Path(output) / name; dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, Path) and '.dds' in name.lower():
            try:
                os.link(data, dest)
                continue
            except OSError:
                pass
        dest.write_bytes(data.read_bytes() if isinstance(data, Path) else data)


def keep_body_under_outfit(sex, region):
    # Equipment regions do not prove skin coverage (necklines, cuffs, hems).
    # Converted opaque surfaces supply conditional body masks for every region.
    return True


def source_default_body(actor, assets):
    """Retain the actor's authored CDF body when no modular body is specified."""
    properties = actor.find('Properties')
    if properties is None:
        raise ValueError('Missing source character properties for default body')
    config = properties.get('esClothingConfig')
    if config not in ('male', 'female'):
        raise ValueError('Unresolved source default body gender')
    path = properties.get('fileModel', '')
    if not path.lower().endswith('.cdf'):
        raise ValueError('Missing source default character definition')
    blob, provenance = assets.get(path)
    root = ET.fromstring(blob)
    bodies = [n for n in root.findall('AttachmentList/Attachment')
              if n.get('Type') == 'CA_SKIN' and n.get('AName', '').casefold() == 'body']
    if len(bodies) != 1 or not bodies[0].get('Binding') or not bodies[0].get('Material'):
        raise ValueError('Missing or ambiguous authored default body attachment')
    body = bodies[0]
    if any(k in body.attrib for k in ('Position', 'Rotation', 'RelPosition', 'RelRotation')):
        raise ValueError('Default body attachment requires transform conversion')
    return dict(kind='body', gender_id='1' if config == 'male' else '2', model=body.get('Binding'),
                material=body.get('Material'), source_cdf=path, source_cdf_provenance=provenance)


def align_internal_translation(blob, *, max_translation=.01):
    """Normalize a small uniform source export offset without moving its surface.

    Some source garments store internal bind positions a few millimetres away
    from every corresponding render position. Only a uniform translation is
    supported; deformed, ambiguous or large discrepancies still fail closed.
    """
    chunks = read_chunks(blob)
    # Older 0x801 exports omit the final reserved word. The streams and bounds
    # have the same layout; normalize only the known zero-reserved extension.
    meshes = [c for c in chunks if c.kind == 0x1000]
    if len(meshes) == 1 and meshes[0].version == 0x801 and len(meshes[0].data) == 260:
        mesh = meshes[0]
        if any(mesh.data[136:]):
            raise ValueError('Unsupported legacy mesh reserved data')
        blob = write_chunks(chunks, {mesh.id: mesh.data + bytes(4)})
        chunks = read_chunks(blob)
    _, vertices, _ = inspect_mesh(blob, diagnostics=False)
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
    if not np.isfinite(max_translation) or not 0 < max_translation <= .25:
        raise ValueError('Invalid internal translation limit')
    if not np.isfinite(offset).all() or np.linalg.norm(offset) > max_translation or not np.allclose(difference, offset, rtol=0, atol=1e-6):
        raise ValueError('Source render/internal difference is not a small uniform translation')
    payload = bytearray(internal.data)
    for i, pos in enumerate(positions + offset):
        struct.pack_into('<3f', payload, 32+64*i+12, *pos)
    return write_chunks(chunks, {internal.id: bytes(payload)}), offset.tolist()


# Advance whenever the source geometry, attachments, masks or material policy
# requires existing saved appearances to be rebuilt by normal Setup.
APPEARANCE_POLICY_VERSION = 2


def build_appearance(source, target, person, namespace, output, *, notify=lambda message: None):
    timings = {}
    previous, started = None, time.perf_counter()
    def phase(message):
        nonlocal previous, started
        now = time.perf_counter()
        if previous is not None:
            timings[previous] = timings.get(previous, 0.) + now - started
        previous, started = message, now
        if message is not None:
            notify(message)
    result = _build_appearance(source, target, person, namespace, output, notify=phase)
    accessories = result.pop('nonvisual_equipment', [])
    if accessories:
        phase('Converting accessories')
        from character_accessories import register_accessories
        result['accessories'] = register_accessories(source, target, Path(output), namespace, accessories)
        result['localization'] = 'Localization/English/text_ui_items.xml'
    result['appearance_policy'] = APPEARANCE_POLICY_VERSION
    phase(None)
    result['timings_seconds'] = timings
    return result


def _build_appearance(source, target, person, namespace, output, *, notify=lambda message: None):
    """Stage a namespaced fixed outfit. Never change existing NPC definitions."""
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a fresh person character directory')
    output.mkdir(parents=True)
    notify('Resolving source parts and asset archives')
    prefix = 'objects/characters/gluetravel/' + namespace + '/'
    folder = prefix.removeprefix('objects/characters/')
    with ExitStack() as stack:
        from character_build_cache import character_inputs
        tables, native = character_inputs(source, target, stack)
        from character_instance import effective_soul
        source_soul = effective_soul(person['soul'], person['instance'])
        parts = resolve_parts(source_soul, lambda table: tables.get(table)['rows'])
        if not any(p['kind'] == 'body' for p in parts):
            parts.insert(0, source_default_body(person['actor'], Assets(Path(source), stack)))
        from character_accessories import partition_accessories
        parts, accessories = partition_accessories(parts, tables)
        bodies = [p for p in parts if p['kind'] == 'body']
        if len(bodies) != 1 or bodies[0].get('gender_id') not in ('1', '2'):
            raise ValueError('Source person requires a resolved body definition: ' + person['name'])
        sex = {'1': 'male', '2': 'female'}[bodies[0]['gender_id']]
        skeleton, rig = native.get(f'objects/characters/humans/{sex}/skeleton/{sex}.chr')
        cache = stack.enter_context(tempfile.TemporaryDirectory(prefix='person-assets-', dir=output.parent))
        index, emitted, material, mesh = asset_pack(stack, Path(source).parent, prefix, cache)
        from clothing_fitting import OutfitFitting
        outfit_fitting = OutfitFitting(parts, index, mesh, material, emitted, tables)
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
            notify(f'Converting {kind} mesh and LODs ({number + 1}/{len(parts)})')
            work_progress('Body and clothing parts', number, len(parts), kind + ' mesh and LODs')
            model = asset_path(index, part, 'model', '.skin')
            if sex == 'female' and kind == 'body':
                from character_female_body import complete_source_body
                model = complete_source_body(model, index)
            source_material = asset_path(index, part, 'material', '.mtl')
            mat = material(source_material)
            if kind == 'cloth':
                tinted = prefix + stem + '_tinted'
                emitted[tinted + '.mtl'] = xml(apply_armor_colorization(ET.parse(emitted[mat + '.mtl']).getroot(), part['armor_settings']))
                mat = tinted
            blob, mat = bind_material(mesh(model), mat, emitted, prefix + stem + '_slots')
            blob, internal_offset = align_internal_translation(blob)
            fitting = None
            morphs = [m['name'] for c in read_chunks(blob) if c.kind == 0x2002 for m in read_morphs(c)]
            # Bodies must also work without their initial outfit. Do not bake
            # clothing-dependent A_Chest/A_Shrink fits into persistent skin.
            variant, variant_evidence = resolve_variant(part, morphs,
                tables.get('item/clothing')['rows'] if kind == 'cloth' else [])
            fitting_masks = None
            if kind in ('body', 'cloth'):
                authored_fit, fitting_masks = outfit_fitting.weights(number, blob, variant)
                fitting = {**(fitting or {}), **authored_fit}
            blob, detail = upgrade_skin(blob, skeleton, variant=variant,
                discard_unused_variants=kind == 'cloth', geometry_mode='preserve', morph_weights=fitting,
                morph_masks=fitting_masks,
                clear_hiding=True, geometry_field=geometry_field)
            blob, _ = limit_skin_influences(blob)
            lods = {}
            for level, lod_model in source_lods(index, model).items():
                lower, lower_mat = bind_material(mesh(lod_model), mat, emitted, prefix + stem + '_slots')
                if lower_mat != mat:
                    raise ValueError('Character LOD requires another material binding: ' + lod_model)
                lower, _ = align_internal_translation(lower)
                if kind in ('body', 'cloth'):
                    lower_fit, lower_masks = outfit_fitting.weights(number, lower, variant, level)
                    # Non-shrink source body fits (such as the authored chest
                    # fit) retain their existing weights at each source LOD.
                    lower_fit = {**{k: v for k, v in (fitting or {}).items()
                                    if k.split(':')[-1].lstrip('#').casefold() != 'a_shrink'}, **lower_fit}
                else:
                    lower_fit, lower_masks = fitting, None
                lower, _ = upgrade_skin(lower, skeleton, variant=variant,
                    discard_unused_variants=kind == 'cloth', geometry_mode='preserve', morph_weights=lower_fit,
                    morph_masks=lower_masks,
                    clear_hiding=True, geometry_field=geometry_field)
                lower, _ = limit_skin_influences(lower)
                lods[level] = lower
            try:
                slots = ET.parse(emitted[mat + '.mtl']).getroot().find('SubMaterials')
                validate_chain(blob, lods, material_slots=len(slots) if slots is not None else 1)
            except ValueError as exc:
                raise ValueError(f'{person["name"]}: {model}: {exc}') from exc
            emitted[prefix + stem + '.mtl'] = emitted[mat + '.mtl'].read_bytes()
            if kind == 'cloth':
                match = re.match(r's[12]_p([1-4])_', Path(part['model']).name, re.I)
                if sex == 'female':
                    region = female_role(part, tables)[1]
                elif not match:
                    raise ValueError('Unmapped source garment body part: ' + part['model'])
                else:
                    region = {'1': 'head', '2': 'torso', '3': 'legs', '4': 'feet'}[match[1]]
                garments.append(dict(name=part['clothing_name'], skin=blob, lods=lods, region=region, part=part, stem=stem,
                    source_coverage={'part'+str(i): mask for i, mask in outfit_fitting.memberships.get((number, 0), {}).items()},
                    lod_coverage={level: {'part'+str(i): mask for i, mask in
                                  outfit_fitting.memberships.get((number, level), {}).items()} for level in lods},
                    source_model=model, source_material=source_material,
                    material=ET.parse(emitted[prefix + stem + '.mtl']).getroot()))
            else:
                name = namespace + '_' + kind; appearance[kind] = name
                node = ET.SubElement(derived, kind.title(), Name=name)
                elements = ET.SubElement(node, 'Elements')
                regions = split_skin_regions(blob, assign_body_regions(blob)) if kind == 'body' and sex == 'male' else {
                    {'body': 'torso', 'head': 'face', 'hair': 'head', 'beard': 'beard'}[kind]: blob}
                lower_regions = {level: (split_skin_regions(lower, assign_body_regions(lower))
                    if kind == 'body' and sex == 'male' else {next(iter(regions)): lower})
                    for level, lower in lods.items()}
                emit_regions(emitted, prefix + stem, regions, lower_regions)
                for region, skin in regions.items():
                    filename = stem + '_' + region + '.skin'; emitted[prefix + filename] = skin
                    ET.SubElement(elements, 'SkinElement', EquipmentPart=region, Model=filename,
                                  Material=stem + '.mtl', BodyLayerId='0', KeepBodyLayer='true', IsFinalLayer='false')
            evidence.append(dict(kind=kind, model=model, clothing=part.get('clothing_name'), upgrade=detail,
                                 source_cdf=part.get('source_cdf'), source_cdf_provenance=part.get('source_cdf_provenance'),
                                 variant=variant_evidence,
                                 lods=source_lods(index, model),
                                 internal_translation=internal_offset))
        work_progress('Body and clothing parts', len(parts), len(parts), 'Meshes and LODs converted')
        if 'beard' not in appearance:
            appearance['beard'] = namespace + '_empty_beard'
            ET.SubElement(derived, 'Beard', Name=appearance['beard'])
        from character_clothing_attachments import attach_source_details
        notify('Converting clothing attachments and layers')
        attachment_report = attach_source_details(garments, tables, index, mesh, material, emitted,
                                                  prefix, skeleton, geometry_field)
        if sex == 'female':
            result = finish_female(source, target, tables, namespace, output, prefix, emitted,
                                 document, derived, appearance, garments, evidence, rig, native, notify=notify)
            result['nonvisual_equipment'] = accessories
            result['fitting'] = outfit_fitting.evidence()
            result['clothing_attachments'] = attachment_report
            return result
        # Canonical palette and native body regions are shared with the accepted
        # generic character upgrade, preserving source geometry across all parts.
        from clothing_coverage import apply_fitted_membership
        for garment in garments:
            membership = garment['source_coverage']
            if membership:
                garment['skin'] = apply_fitted_membership(garment['skin'], membership)
            for level, membership in garment['lod_coverage'].items():
                if membership:
                    garment['lods'][level] = apply_fitted_membership(garment['lods'][level], membership)
        canonical = canonicalize_skin_skeletons([g['skin'] for g in garments])
        from character_skin_upgrade import CompiledSkin, matrix
        bones = CompiledSkin(canonical[0]).info['bones']
        waist = float(matrix(next(b for b in bones if b['name'] == 'Hips')['bind'])[2, 3])
        for garment, blob in zip(garments, canonical):
            garment['skin'] = blob
            if garment['region'] == 'torso':
                garment['face_regions'] = assign_upper_garment_regions(blob, waist_z=waist)
        outfit = convert_fixed_outfit(garments, prefix + 'outfit_')
        outfit_lods = {}
        for level in sorted({level for g in garments for level in g['lods']}):
            lower = [dict(g, skin=g['lods'].get(min(level, max(g['lods'])), g['skin'])
                          if g['lods'] else g['skin']) for g in garments]
            palette = canonicalize_skin_skeletons([g['skin'] for g in lower])
            for garment, lower_blob in zip(lower, palette):
                garment['skin'] = lower_blob
                if garment['region'] == 'torso':
                    garment['face_regions'] = assign_upper_garment_regions(lower_blob, waist_z=waist)
            assembled = convert_fixed_outfit(lower, prefix + 'outfit_')
            # Slot offsets depend on garment membership. A changed region must
            # not silently bind another garment's material at a lower LOD.
            if set(assembled) != set(outfit) or any(
                    xml(assembled[r]['material']) != xml(outfit[r]['material']) for r in outfit):
                raise ValueError('Authored outfit LOD changes region/material membership')
            outfit_lods[level] = {r: v['skin'] for r, v in assembled.items()}
        emit_regions(emitted, prefix + 'outfit', {r: v['skin'] for r, v in outfit.items()}, outfit_lods)
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
        hiding_root = ET.Element('database', name='barbora')
        hiding_groups = ET.SubElement(hiding_root, 'ClothingHidingGroups', version='2')
        group_names = []
        for region, value in outfit.items():
            stem = 'outfit_' + region
            emitted[prefix + stem + '.skin'] = value['skin']; emitted[prefix + stem + '.mtl'] = xml(value['material'])
            ET.SubElement(elements, 'SkinElement', EquipmentPart=region, Model=stem + '.skin', Material=stem + '.mtl',
                          BodyLayerId='9', KeepBodyLayer=str(keep_body_under_outfit(sex, region)).lower(), IsFinalLayer='true')
            group = namespace + '_outfit_covers_' + region
            ET.SubElement(hiding_groups, 'HidingGroup', Name=group, Index='4', EquipmentPart=region)
            group_names.append(group)
        node.set('HidingGroups', ' '.join(group_names))
        emitted['Libs/Tables/Character/ClothingHidingGroup__' + namespace + '.xml'] = xml(hiding_root)
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
        notify('Writing meshes and source textures')
        write_staged(output, {**emitted, 'Libs/Tables/Character/CharacterComponent__' + namespace + '.xml': xml(document),
            'Libs/Tables/item/item__' + namespace + '.xml': xml(item_root),
            'Libs/Tables/item/clothing_preset__' + namespace + '.xml': xml(clothing_root),
            'Libs/Tables/item/InventoryPreset__' + namespace + '_clothes.xml': xml(preset_root)})
        # Same native clothing shader/vertex-feature upgrade as female garments:
        # the fixed outfit otherwise keeps legacy Illum colourisation (issue #1).
        # The float bakes run in worker processes; leaving the block waits for them
        # (or, on failure, cancels and joins them) before anything reads the outputs.
        notify('Baking native materials and textures')
        with Bakes(reuse=True) as bakes:
            material_report = convert_character_materials(output, document.find('CharacterComponents/Component'),
                                                          native, namespace, sex, bakes, preserve_hiding=True)
        from clothing_coverage import register_fixed_body_coverage
        notify('Checking body, hair and clothing coverage')
        coverage = register_fixed_body_coverage(output, component, namespace)
        notify('Writing character definitions')
        (output / ('Libs/Tables/Character/CharacterComponent__' + namespace + '.xml')).write_bytes(xml(document))
    return dict(appearance=appearance, inventory=namespace + '_clothes', gender=sex,
                archetype=native_soul_archetype(archetypes, sex.title()), parts=evidence, rig=rig,
                nonvisual_equipment=accessories, materials=material_report,
                texture_bakes=bakes.stats,
                coverage=coverage,
                fitting=outfit_fitting.evidence(), clothing_attachments=attachment_report,
                outfit_mode='Fixed source outfit, using the existing skin upgrader', runtime_verified=False)


def finish_female(source, target, tables, namespace, output, prefix, emitted,
                  document, derived, appearance, garments, evidence, rig, native, *, notify=lambda message: None):
    """Resolve native roles, then bundle source layers sharing a draw region."""
    with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
        from clothing_equipment import NativeEquipment
        equipment = NativeEquipment.from_archive(archive)
        components = native_xml(read(archive, 'Libs/Tables/Character/CharacterComponent.xml'))
        native_items = native_xml(read(archive, 'Libs/Tables/item/item.xml'))
        archetypes = native_xml(read(archive, 'Libs/Tables/rpg/soul_archetype.xml'))
        layer_order = {n.get('body_layer_id'): int(n.get('layer_order')) for n in
                       native_xml(read(archive, 'Libs/Tables/item/body_layer.xml')).iter('body_layer')}
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
        armor_type, region = female_role(part, tables)
        default_archetype, template = native_role(components, native_items, armor_type)
        contract = equipment.source_contract(part, tables, default_archetype, region)
        archetype, layer = contract['archetype'], contract['layer']
        name = namespace + '_' + stem
        node = ET.SubElement(derived, 'Clothing', Name=name, ArmorType=armor_type, ArmorArchetypeId=archetype)
        emitted[prefix + stem + '.skin'] = garment['skin']
        for level, lower in garment['lods'].items():
            emitted[lod_filename(prefix + stem + '.skin', level)] = lower
        ET.SubElement(ET.SubElement(node, 'Elements'), 'SkinElement', EquipmentPart=region,
            Model=stem + '.skin', Material=stem + '.mtl', BodyLayerId=str(layer),
            KeepBodyLayer='true', IsFinalLayer='false')
        item, texts = source_item(tables, part['armor_id'], namespace, name, template, source_strings)
        items.append(item); localization.extend(texts)
        ET.SubElement(ids, 'Guid').text = item.get('Id')
        item_report.append(dict(source_id=part['armor_id'], item_id=item.get('Id'), component=name,
            source_model=part['model'], source_material=part['material'], native_role=armor_type,
            equipment=contract))
    # Retain the source innermost dress as underwear. The complete source body
    # is converted with the same field as the garments; underwear must not be
    # relied upon to supply skin exposed by an outer garment's neckline.
    torso = [g for g in garments if g['region'] == 'torso']
    if torso:
        from clothing_fitting import source_layer_contracts
        layers = source_layer_contracts([g['part'] for g in torso], tables)
        inner = torso[min(layers, key=lambda i: max(layers[i].values()))]
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
    notify('Writing meshes and source textures')
    write_staged(output, {**emitted, **additions})
    notify('Baking native materials and textures')
    with Bakes(reuse=True) as bakes:
        material_report = convert_character_materials(output, document.find('CharacterComponents/Component'),
                                                      native, namespace, 'female', bakes)
    from clothing_coverage import register_coverage
    notify('Checking body and clothing coverage')
    coverage = register_coverage(output, document.find('CharacterComponents/Component'), garments, tables, namespace)
    from clothing_native_composition import compose_source_regions
    notify('Composing native clothing layers')
    composition = compose_source_regions(output, document.find('CharacterComponents/Component'), items, preset, layer_order)
    (output / ('Libs/Tables/Character/CharacterComponent__' + namespace + '.xml')).write_bytes(xml(document))
    (output / ('Libs/Tables/item/item__' + namespace + '.xml')).write_bytes(xml(item_root))
    (output / ('Libs/Tables/item/clothing_preset__' + namespace + '.xml')).write_bytes(xml(clothing_root))
    return dict(appearance=appearance, inventory=namespace + '_clothes', gender='female',
        archetype=native_soul_archetype(archetypes, 'Female'), parts=evidence, rig=rig,
        items=item_report, composition=composition,
        texture_bakes=bakes.stats,
        outfit_mode='Source female outfits composed per native draw region', runtime_verified=False,
        localization='Localization/English/text_ui_items.xml', materials=material_report, coverage=coverage)
