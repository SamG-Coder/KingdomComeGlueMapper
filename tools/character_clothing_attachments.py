"""Retain armour-linked skinned details within their owning clothing element.

KCD1 registers bands, knots, laces and other details separately from the main
garment. They must join that garment's native draw mesh, not compete for its
equipment slot or become an extra inventory item.
"""
import xml.etree.ElementTree as ET
import copy
import struct

import numpy as np

from build_npc_probe import asset_path, bind_material
from character_lods import source_lods, validate_chain
from character_skin_upgrade import upgrade_skin, limit_skin_influences, canonicalize_skin_skeletons, CompiledSkin
from clothing_appearance import apply_armor_colorization
from clothing_assembly import convert_fixed_outfit
from clothing_regions import Skin, write_chunks
from upgrade_map import xml


def coalesce_material_slots(blobs, material):
    """Bind cloned single-material slots once, retaining every face's surface.

    Source attachments often use high slot IDs against one material override.
    bind_material expands that override to preserve the source layout. Joining
    those aliases must not create dozens of identical textures and GPU maps.
    """
    slots = material.find('SubMaterials')
    if slots is None:
        return blobs, material
    unique = []; keys = {}; mapping = {}
    for number, slot in enumerate(slots):
        key = ET.tostring(slot)
        if key not in keys:
            keys[key] = len(unique); unique.append(copy.deepcopy(slot))
        mapping[number] = keys[key]
    if len(unique) == len(slots):
        return blobs, material
    converted = {}
    for level, blob in blobs.items():
        skin = Skin(blob); records = []
        for original in skin.subset_records:
            record = bytearray(original); old = struct.unpack_from('<I',record,16)[0]
            if old not in mapping:
                raise ValueError('Attachment subset has no material')
            struct.pack_into('<I',record,16,mapping[old]); records.append(bytes(record))
        converted[level] = write_chunks(skin.chunks, {skin.subsets.id:skin.subsets.data[:16]+b''.join(records)})
    result = copy.deepcopy(material); result.find('SubMaterials')[:] = unique
    return converted, result


def resolve_attachments(parts, tables):
    definitions = {}
    for row in tables.get('item/clothing_attachment')['rows']:
        identity = row['clothing_attachment_id']
        if identity in definitions:
            raise ValueError('Duplicate source clothing attachment: '+identity)
        definitions[identity] = row
    links = tables.get('item/armor2clothing_attachment')['rows']
    result = {}; seen = set()
    for index, part in enumerate(parts):
        for link in links:
            if link['item_id'] != part['armor_id']:
                continue
            identity = link['clothing_attachment_id']
            if identity not in definitions:
                raise ValueError('Missing source clothing attachment: '+identity)
            row = definitions[identity]
            if row['gender_id'] != part['gender_id']:
                continue
            key = part['armor_id'], identity
            if key in seen:
                continue  # One armour can also declare a second main garment.
            seen.add(key)
            if not row.get('model') or not row.get('material'):
                raise ValueError('Clothing attachment lacks source geometry or material: '+identity)
            result.setdefault(index, []).append(dict(row, kind='cloth', armor_settings=link))
    return result


def merge_details(garment, details, material_name):
    """Combine all LODs without altering parent masks or source triangle counts."""
    entries = [garment, *details]
    levels = sorted({0, *(level for entry in entries for level in entry['lods'])})
    merged = {}; masks = {}; material = None
    for level in levels:
        selected = []
        for entry in entries:
            family = {0:entry['skin'], **entry['lods']}
            selected.append(family[max(n for n in family if n <= level)])
        parent_count = CompiledSkin(selected[0]).n
        palette = canonicalize_skin_skeletons(selected)
        assembled = convert_fixed_outfit([
            dict(name=e['name'], skin=skin, region=garment['region'], material=e['material'])
            for e, skin in zip(entries, palette)], material_name+'_')
        region = assembled[garment['region']]
        if material is not None and xml(material) != xml(region['material']):
            raise ValueError('Clothing attachment LOD changes material ownership')
        material = region['material']; merged[level] = region['skin']
        parent_level = max(n for n in {0, *garment['lods']} if n <= level)
        membership = (garment.get('source_coverage', {}) if parent_level == 0 else
                      garment.get('lod_coverage', {}).get(parent_level, {}))
        count = CompiledSkin(region['skin']).n
        masks[level] = {}
        for name, values in membership.items():
            values = np.asarray(values)
            if values.shape != (parent_count,):
                raise ValueError('Attachment parent coverage topology differs')
            # Parent fitting rays do not prove coverage of a separate strap.
            masks[level][name] = np.pad(values, (0, count-parent_count))
    slots = material.find('SubMaterials')
    validate_chain(merged[0], {n:b for n,b in merged.items() if n}, material_slots=len(slots))
    garment.update(skin=merged.pop(0), lods=merged, material=material,
                   source_coverage=masks.pop(0), lod_coverage=masks)


def attach_source_details(garments, tables, index, mesh, material, emitted, prefix, skeleton, geometry_field):
    from character_person_appearance import align_internal_translation
    resolved = resolve_attachments([g['part'] for g in garments], tables)
    report = []
    for number, attachments in resolved.items():
        owner = garments[number]; details = []
        for attachment in attachments:
            name = attachment['clothing_attachment_id']
            stem = prefix+owner['stem']+'_detail_'+name.replace('-', '')
            model = asset_path(index, attachment, 'model', '.skin')
            source_material = asset_path(index, attachment, 'material', '.mtl')
            staged = material(source_material)
            emitted[stem+'.mtl'] = xml(apply_armor_colorization(ET.parse(emitted[staged+'.mtl']).getroot(), attachment['armor_settings']))
            mat = stem; family = {}
            for level, path in {0:model, **source_lods(index, model)}.items():
                raw, slot_material = bind_material(mesh(path), mat, emitted, stem+'_slots')
                if level and mat != slot_material:
                    raise ValueError('Clothing attachment LOD changes material slots')
                mat = slot_material
                raw, _ = align_internal_translation(raw)
                converted, _ = upgrade_skin(raw, skeleton, discard_unused_variants=True, clear_hiding=True,
                                             geometry_mode='preserve', geometry_field=geometry_field)
                family[level], _ = limit_skin_influences(converted)
            family, detail_material = coalesce_material_slots(family, ET.parse(emitted[mat+'.mtl']).getroot())
            details.append(dict(name=name, skin=family.pop(0), lods=family, material=detail_material))
            report.append(dict(armor_id=owner['part']['armor_id'], clothing_attachment_id=name,
                               name=attachment['clothing_attachment_name'], model=model, material=source_material,
                               owner=owner['stem'], lods=sorted({0,*family}), runtime_verified=False))
        merge_details(owner, details, prefix+owner['stem'])
        emitted[prefix+owner['stem']+'.mtl'] = xml(owner['material'])
    return report
