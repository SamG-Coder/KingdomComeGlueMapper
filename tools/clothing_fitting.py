"""Fit source underlayers only beneath opaque, higher source layers.

This is a fixed-outfit composition stage, before native region merging and
body visibility. It uses authored fitting deltas, never an actor-specific scale.
Exposed cuffs, hems and cutouts retain their selected source variant shape.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from build_npc_probe import asset_path, bind_material
from character_clothing_variant import resolve_variant
from character_lods import source_lods
from character_skin_upgrade import CompiledSkin, read_morphs, upgrade_skin
from clothing_coverage import alpha_intersections, covered_points, normals, unculled_garment_visibility
from clothing_regions import read_chunks
from npc_effects import MAX_FLOAT_SIZE, decode_dds_family


def alpha_mips(image):
    """The same half-float alpha chain written by the native material bake."""
    levels = [np.asarray(image.getchannel('A'), np.float32).astype(np.float16) / np.float16(255)]
    while levels[-1].shape != (1, 1):
        h, w = levels[-1].shape
        levels.append(np.asarray(Image.fromarray(levels[-1].astype(np.float32)).resize(
            (max(1, w//2), max(1, h//2)), Image.Resampling.BOX), np.float16))
    while max(levels[0].shape) > MAX_FLOAT_SIZE:
        levels.pop(0)
    return levels


def source_alpha(material, emitted):
    slots = material.find('SubMaterials')
    surfaces = []
    for slot in list(slots) if slots is not None else [material]:
        shader = slot.get('Shader', '').lower()
        if shader == 'nodraw':
            surfaces.append((0., [np.zeros((1, 1))]))
            continue
        threshold = float(slot.get('AlphaTest', '0'))
        if shader != 'illum' or not 0 <= threshold < 1:
            raise ValueError('Unsupported source clothing fitting opacity')
        texture = slot.findall("Textures/Texture[@Map='Diffuse']")
        if len(texture) != 1 or len(texture[0]) or any(
                texture[0].get(k, '1') != '1' for k in ('IsTileU', 'IsTileV')):
            raise ValueError('Fitting requires an unmodified source diffuse UV mapping')
        name = texture[0].get('File')
        def payload(key):
            value = emitted[key]
            return value.read_bytes() if isinstance(value, Path) else value
        members = {int(k[len(name)+1:]): payload(k) for k in emitted
                   if k.startswith(name+'.') and k[len(name)+1:].isdigit()}
        surfaces.append((threshold, alpha_mips(decode_dds_family(payload(name), members))))
    return surfaces


def fit_mask(skin, covers):
    """Return coverage weights, agreeing on every shared internal vertex.

    A seam is exposed if any render copy is exposed. This deliberately leaves
    an uncertain boundary unshrunk rather than pulling its visible cuff inward.
    """
    covered = np.zeros(skin.n, bool)
    direction = normals(skin)
    for upper, opaque, accepts in covers:
        covered |= covered_points(skin.vertices, direction,
                                  upper.vertices[upper.faces[opaque]], accept_hit=accepts)
    shared = np.ones(skin.ninternal, bool)
    np.logical_and.at(shared, skin.remap, covered)
    return shared[skin.remap].astype(float)


def source_layer_contracts(parts, tables):
    """Resolve source coverage and order through KCD1's actual RPG tables.

    A long upper garment can cover trouser subparts too. Filename part/layer
    numbers cannot express that, and numeric layer IDs are not sorting ranks.
    """
    order = {r['body_layer_id']: int(r['layer_order']) for r in tables.get('item/body_layer')['rows']}
    assignments = {}
    for row in tables.get('item/armor_archetype2body_subpart')['rows']:
        key = row['armor_archetype_id']
        subpart = row['body_subpart_id']
        rank = order[row['body_layer_id']]
        previous = assignments.setdefault(key, {}).setdefault(subpart, rank)
        if previous != rank:
            raise ValueError('Ambiguous source archetype layer on body subpart')
    result = {}
    for i, part in enumerate(parts):
        if part['kind'] != 'cloth': continue
        identity = part['armor_archetype_id']
        if identity not in assignments:
            raise ValueError('Missing source archetype body-layer contract: '+identity)
        result[i] = assignments[identity]
    return result


def higher_layers(number, parts, contracts):
    lower = contracts[number]
    result = []
    for i, upper in contracts.items():
        if i == number or parts[i]['gender_id'] != parts[number]['gender_id']: continue
        common = set(lower) & set(upper)
        differences = [upper[p] - lower[p] for p in common]
        # Mixed ordering requires per-subpart fitting; do not guess a global
        # winner for such a pair. The geometry audit retains those conflicts.
        if differences and min(differences) >= 0 and max(differences) > 0:
            result.append(i)
    return result


class OutfitFitting:
    """Resolve source layer geometry/alpha once, independently for each LOD."""
    def __init__(self, parts, index, mesh, material, emitted, tables):
        self.parts, self.index, self.mesh = parts, index, mesh
        self.material, self.emitted, self.tables = material, emitted, tables
        self.covers, self.alpha, self.memberships = {}, {}, {}
        self.contracts = source_layer_contracts(parts, tables)
        self.resolving = set()

    def evidence(self):
        return dict(policy='Authored fitting beneath opaque higher layers from source body-subpart contracts',
            body_policy='Preserve source body shape; equipment controls skin visibility without permanent outfit-dependent fitting',
            source_tables={name: self.tables.get(name)['provenance'] for name in
                           ('item/body_layer', 'item/armor_archetype2body_subpart')},
            relationships=[dict(inner=self.parts[i]['clothing_id'], outer=self.parts[j]['clothing_id'])
                           for i in self.contracts for j in higher_layers(i, self.parts, self.contracts)],
            fitting_opacity='Complete converted surface with finest converted-equivalent alpha mip at each source LOD; legacy triangle caches are not visibility evidence',
            exposed_vertex_policy='Keep the selected source variant; no fitting displacement',
            native_runtime_verified=False)

    def cover(self, number, lod):
        part = self.parts[number]
        model = asset_path(self.index, part, 'model', '.skin')
        lods = source_lods(self.index, model)
        level = max((n for n in lods if n <= lod), default=0)
        model = lods.get(level, model)
        key = number, level
        if key in self.covers:
            return self.covers[key]
        if key in self.resolving:
            raise ValueError('Cyclic source clothing layer dependencies')
        self.resolving.add(key)
        from character_person_appearance import align_internal_translation
        mat = asset_path(self.index, part, 'material', '.mtl')
        staged = self.material(mat)
        blob, staged = bind_material(self.mesh(model), staged, self.emitted, staged+'_fit_slots')
        blob, _ = align_internal_translation(blob)
        shapes = [m['name'] for c in read_chunks(blob) if c.kind == 0x2002 for m in read_morphs(c)]
        variant, _ = resolve_variant(part, shapes, self.tables.get('item/clothing')['rows'])
        weights, masks = self.weights(number, blob, variant, level)
        baked, _ = upgrade_skin(blob, blob, variant=variant, discard_unused_variants=True, bone_mapping={},
                                morph_weights=weights, morph_masks=masks)
        upper = CompiledSkin(baked)
        if number not in self.alpha:
            self.alpha[number] = source_alpha(ET.parse(self.emitted[staged+'.mtl']).getroot(), self.emitted)
        # upgrade_skin clears legacy hiding. KCD1's cached opaque/not-culled
        # triangle masks describe a different visibility state and can exclude
        # opaque rendered surfaces at cuffs, collars and hems. Test the complete
        # converted surface at every LOD; UV alpha, not an old bit, proves a hit.
        opaque = unculled_garment_visibility(upper)
        # Fitting is geometric, not a decision to delete/hide skin. The finest
        # rendered surface defines the garment's physical cut. Requiring every
        # distant mip to agree incorrectly exposes whole bands near UV islands.
        surfaces = [(threshold, levels[:1]) for threshold, levels in self.alpha[number]]
        result = upper, opaque, alpha_intersections(upper, surfaces, opaque)
        self.covers[key] = result
        self.resolving.remove(key)
        return result

    def weights(self, number, blob, variant, lod=0):
        part = self.parts[number]
        # A body outlives its equipment. Baking an outfit-dependent shrink into
        # it leaves thin limbs and a neck seam when the outfit is unequipped or
        # unavailable. Conditional hiding handles skin beneath clothes; only
        # the clothing itself may retain fixed-outfit fitting deltas.
        if part['kind'] != 'cloth':
            return {}, {}
        higher = higher_layers(number, self.parts, self.contracts)
        if not higher:
            return {}, {}
        shapes = [m['name'] for c in read_chunks(blob) if c.kind == 0x2002 for m in read_morphs(c)]
        shrink = [n for n in shapes if n.split(':')[-1].lstrip('#').casefold() == 'a_shrink']
        if len(shrink) > 1:
            raise ValueError('Ambiguous authored garment fitting shape')
        baked, _ = upgrade_skin(blob, blob, variant=variant, discard_unused_variants=True, bone_mapping={})
        skin = CompiledSkin(baked)
        by_cover = {i: fit_mask(skin, [self.cover(i, lod)]) for i in higher}
        self.memberships[number, lod] = by_cover
        mask = np.maximum.reduce(list(by_cover.values()))
        return ({shrink[0]: 1.}, {shrink[0]: mask}) if shrink else ({}, {})
