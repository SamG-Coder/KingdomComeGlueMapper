"""Developer-only body repair without rebaking textures or the world.

Rebuild persistent bodies from source geometry, then regenerate conditional
coverage against the installed outfit. Produces a small inspection overlay;
never writes to an installation. Native visual verification is still required.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from audit_character_bodies import Assets as SourceAssets, bone_table
from build_npc_probe import resolve_parts, asset_path
from campaign_dependency_adapters import SourceTables
from campaign_sources import RetailSourceReader
from character_clothing_variant import resolve_variant
from character_female_body import complete_source_body
from character_instance import effective_soul
from character_landmark_warp import anatomical_warp
from character_lods import source_lods
from character_person import resolve_person
from character_person_appearance import align_internal_translation, source_default_body
from character_skin_upgrade import CompiledSkin, upgrade_skin, limit_skin_influences, read_morphs, spine_mapping
from clothing_coverage import apply_membership, normals, triangle_safe_membership
from clothing_lab.catalog import Catalog
from clothing_lab.validation import active_masks, element_replaced
from clothing_regions import assign_body_regions, split_skin_regions, read_chunks, write_chunks


def retain_body_registration(blob, previous):
    """Keep material/feature bindings only after proving identical topology."""
    skin, old = CompiledSkin(blob), CompiledSkin(previous)
    for label, a, b in (('faces', skin.faces, old.faces), ('remap', skin.remap, old.remap),
                        ('joints', skin.ids, old.ids), ('weights', skin.weights, old.weights)):
        if not np.array_equal(a, b):
            raise ValueError('Body repair changes '+label+'; use a full appearance rebuild')
    if skin.n != old.n or skin.ninternal != old.ninternal or [b['name'] for b in skin.info['bones']] != [b['name'] for b in old.info['bones']]:
        raise ValueError('Body repair changes vertex or skeleton identity')
    if skin.streams[2].data != old.streams[2].data:
        raise ValueError('Body repair changes source texture coordinates')
    replacements = {}
    bindings = [c for c in skin.chunks if c.kind == 0x1014]
    old_bindings = [c for c in old.chunks if c.kind == 0x1014]
    if len(bindings) != len(old_bindings):
        raise ValueError('Body material bindings differ')
    for new, prior in zip(bindings, old_bindings):
        if new.version != prior.version:
            raise ValueError('Body material binding version differs')
        replacements[new.id] = prior.data
    colors = bytearray(old.streams[3].data)
    colors[27::4] = b'\xff'*skin.n
    replacements[skin.streams[3].id] = bytes(colors)
    internal = bytearray(skin.internal.data)
    for i in range(skin.ninternal):
        internal[32+i*64+60:32+i*64+63] = old.internal.data[32+i*64+60:32+i*64+63]
        internal[32+i*64+63] = 255
    replacements[skin.internal.id] = bytes(internal)
    return write_chunks(skin.chunks, replacements)


def body_coverage(catalog, selected, element, blob, lod):
    """Use currently registered garment groups; never permanently hide skin."""
    skin = CompiledSkin(blob)
    region = element['EquipmentPart']
    active = active_masks(catalog, selected)
    memberships = {}
    direction = normals(skin)
    for identity in selected:
        part = catalog.parts[identity]
        if part['kind'] != 'Clothing':
            continue
        bits = {catalog.groups[g]['index'] for g in part['attributes'].get('HidingGroups', '').split()
                if g in catalog.groups and catalog.groups[g]['region'] == region}
        if not bits:
            continue
        covered = np.zeros(skin.n, bool)
        for cover in part['elements']:
            if element_replaced(catalog, selected, part, cover):
                continue
            model, _ = catalog.model_at_lod(cover['ModelResolved'], lod)
            surface = catalog.coverage_surface(model, cover['MaterialResolved'], active.get(cover['EquipmentPart'], 0))
            covered |= surface.covers(skin.vertices, direction)
        covered = triangle_safe_membership(skin, covered)
        for bit in bits:
            memberships[bit] = memberships.get(bit, np.zeros(skin.n, bool)) | covered
    return apply_membership(blob, memberships), {str(bit): int(mask.sum()) for bit, mask in memberships.items()}


def rebuild(source, target, installed, output, people=None, notify=print):
    source, target, installed, output = map(Path, (source, target, installed, output))
    if output.exists() or output.resolve().is_relative_to(installed.resolve()):
        raise ValueError('Choose a fresh output outside the installation')
    catalog = Catalog(installed)
    checks = {c['namespace']: c for c in catalog.inventory_checks}
    selection = [p for p in catalog.presets.values() if p['id'].removesuffix('_root') in checks
                 and (people is None or p['label'] in people)]
    if not selection or (people and {p['label'] for p in selection} != set(people)):
        raise ValueError('Missing imported source people')
    (output/'Data').mkdir(parents=True)
    report = dict(scope='source_body_shape_repair', base_signature=catalog.assets.signature(),
                  runtime_verified=False, complete=False, people=[])
    def save():
        (output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    save()
    with ExitStack() as stack:
        reader = stack.enter_context(RetailSourceReader(source))
        tables = SourceTables(source, reader)
        assets, native = SourceAssets(source, stack), SourceAssets(target, stack)
        for number, preset in enumerate(selection, 1):
            namespace = preset['id'].removesuffix('_root')
            notify(f'Body {number}/{len(selection)}: {preset["label"]}')
            person = resolve_person(source, preset['label'], include_ai=False)
            parts = resolve_parts(effective_soul(person['soul'], person['instance']), lambda t: tables.get(t)['rows'])
            bodies = [p for p in parts if p['kind'] == 'body']
            body = bodies[0] if len(bodies) == 1 else source_default_body(person['actor'], assets) if not bodies else None
            if body is None:
                raise ValueError('Ambiguous source body')
            sex = {'1': 'male', '2': 'female'}[body['gender_id']]
            model = asset_path(assets.index, body, 'model', '.skin')
            original, provenance = assets.get(model)
            skeleton, _ = native.get(f'objects/characters/humans/{sex}/skeleton/{sex}.chr')
            field = None
            if sex == 'female':
                field = anatomical_warp(original, skeleton, spine_mapping(
                    bone_table(read_chunks(original)), bone_table(read_chunks(skeleton))))
                model = complete_source_body(model, assets.index)
            actual = [catalog.parts[p] for p in preset['parts'] if catalog.parts[p]['kind'] == 'Body']
            if len(actual) != 1:
                raise ValueError('Expected one registered body')
            elements = {e['EquipmentPart']: e for e in actual[0]['elements']}
            files, evidence = {}, []
            family = {0: model, **source_lods(assets.index, model)}
            for lod, source_model in family.items():
                blob, provenance = assets.get(source_model)
                blob, _ = align_internal_translation(blob)
                shapes = [m['name'] for c in read_chunks(blob) if c.kind == 0x2002 for m in read_morphs(c)]
                variant, _ = resolve_variant(body, shapes, [])
                blob, detail = upgrade_skin(blob, skeleton, variant=variant, geometry_mode='preserve', geometry_field=field)
                blob, _ = limit_skin_influences(blob)
                regions = split_skin_regions(blob, assign_body_regions(blob)) if sex == 'male' else {'torso': blob}
                if set(regions) != set(elements):
                    raise ValueError('Source body regions differ from installed body')
                for region, raw in regions.items():
                    element = elements[region]
                    name, effective_lod = catalog.model_at_lod(element['ModelResolved'], lod)
                    if effective_lod != lod:
                        raise ValueError('Source body LOD chain differs from installed body')
                    old = catalog.assets.read(name)
                    raw = retain_body_registration(raw, old)
                    new, membership = body_coverage(catalog, preset['parts'], element, raw, lod)
                    before, after = CompiledSkin(old), CompiledSkin(new)
                    distances = np.linalg.norm(after.vertices-before.vertices, axis=1)*1000
                    files[name] = new
                    evidence.append(dict(asset=name, lod=lod, vertices=after.n, triangles=len(after.faces),
                        changed_vertices=int((distances>0.001).sum()), maximum_shape_restoration_mm=float(distances.max()),
                        baked_morphs=detail['baked_morphs'], coverage=membership, source=provenance,
                        topology_preserved=True, unequipped_shape_preserved=True))
            pending = output/'Data'/(namespace+'.pending')
            with zipfile.ZipFile(pending, 'x', zipfile.ZIP_DEFLATED) as archive:
                for name, blob in files.items():
                    archive.writestr(name, blob)
            pending.replace(output/'Data'/(namespace+'.pak'))
            report['people'].append(dict(person=preset['label'], namespace=namespace, gender=sex, files=evidence))
            save()
            catalog.coverage_surface.cache_clear()
            catalog.materials.cache_clear()
            catalog.mesh.cache_clear()
    report['complete'] = True
    save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'target', 'installed', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--person', action='append')
    args = parser.parse_args()
    rebuild(args.source, args.target, args.installed, args.output, args.person,
            notify=lambda message: print(message, flush=True))
