"""Translate source opaque garment surfaces into conditional native hiding masks.

Source masks describe triangle opacity, not body visibility. Generate coverage
against the converted, fitted meshes; do not copy those bits onto body vertices.
The bounded normal-ray policy is explicit and does not claim to reproduce the
closed-source KCD1 raycaster exactly.
"""
import base64
import hashlib
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import numpy as np

from character_skin_upgrade import CompiledSkin
from clothing_regions import write_chunks
from upgrade_map import xml


def triangle_mask(encoded, count):
    data = base64.b64decode(encoded, validate=True)
    if len(data) != 2 + (count + 7) // 8 or struct.unpack_from('<H', data)[0] != count:
        raise ValueError('Source triangle mask does not match mesh topology')
    return np.unpackbits(np.frombuffer(data[2:], dtype=np.uint8), bitorder='little')[:count].astype(bool)


def source_opacity(rows, model, material, count, rendered_visibility=None, generated_opacity=None):
    def key(path):
        return path.replace('\\', '/').lower()
    matches = [r for r in rows if key(r['skin']) == key(model) and key(r['material']) == key(material)]
    if len(matches) != 1:
        raise ValueError('Missing or ambiguous clothing raycast record: ' + model + ' / ' + material)
    row = matches[0]
    # An empty serialized field is unknown, not proof that every triangle is opaque.
    if not row.get('opaque_triangle_mask') and generated_opacity is None:
        raise ValueError('Source clothing raycast masks need generation: ' + material)
    if generated_opacity is not None:
        generated_opacity = np.asarray(generated_opacity)
        if generated_opacity.dtype != np.dtype(bool) or generated_opacity.shape != (count,):
            raise ValueError('Invalid generated garment opacity')
    if row.get('not_culled_triangle_mask'):
        visible = triangle_mask(row['not_culled_triangle_mask'], count)
    elif rendered_visibility is not None:
        visible = np.asarray(rendered_visibility)
        if visible.dtype != np.dtype(bool) or visible.shape != (count,):
            raise ValueError('Invalid converted garment visibility')
    else:
        raise ValueError('Source clothing raycast masks need generation: ' + material)
    opaque = triangle_mask(row['opaque_triangle_mask'], count) if row.get('opaque_triangle_mask') else generated_opacity
    return opaque & visible


def converted_coverage_surface(skin, rows, model, material, output, material_path, visibility):
    """A legacy triangle bit is a candidate, never proof of converted opacity."""
    surfaces, evidence = native_alpha_surfaces(output, material_path)
    opaque = source_opacity(rows, model, material, len(skin.faces),
                            rendered_visibility=visibility,
                            generated_opacity=np.ones(len(skin.faces), dtype=bool))
    return opaque, alpha_intersections(skin, surfaces, opaque), evidence


def native_alpha_surfaces(output, material_path, *, cache=None):
    """Read the supported converted cloth permutation and its complete alpha mips."""
    root = ET.parse(material_path).getroot()
    materials = list(root.find('SubMaterials')) if root.find('SubMaterials') is not None else [root]
    evidence, surfaces = [], []
    allowed = {'CLOTHING_SYSTEM', 'NORMAL_MAP', 'SPECULAR_MAP', 'SUBSURFACE_SCATTERING'}
    for material in materials:
        if material.get('Shader','').lower() == 'nodraw':
            evidence.append(dict(shader='Nodraw', opaque=False))
            surfaces.append((0., [np.zeros((1,1))]))
            continue
        flags = set(filter(None, material.get('StringGenMask', '').split('%')))
        threshold = float(material.get('AlphaTest', '0'))
        if (material.get('Shader') != 'Illum' or 'CLOTHING_SYSTEM' not in flags or flags - allowed
                or material.get('MtlFlags') != '524290' or float(material.get('Opacity', '0')) != 1
                or not 0 <= threshold < 1 or material.find('SubMaterials') is not None):
            raise ValueError('Unsupported generated clothing opacity material')
        textures = material.findall("Textures/Texture[@Map='Diffuse']")
        if len(textures) != 1:
            raise ValueError('Missing or ambiguous clothing diffuse alpha')
        if len(textures[0]) or textures[0].get('IsTileU', '1') != '1' or textures[0].get('IsTileV', '1') != '1':
            raise ValueError('Unsupported clothing alpha UV transform or addressing')
        path = (Path(output) / textures[0].get('File')).resolve()
        if not path.is_relative_to(Path(output).resolve()):
            raise ValueError('Clothing opacity texture outside converted output')
        if cache is not None and path in cache:
            levels, digest, mip_count = cache[path]
            evidence.append(dict(texture=textures[0].get('File'), sha256=digest, mips=mip_count))
            surfaces.append((threshold, levels))
            continue
        data = path.read_bytes()
        if (len(data) < 148 or data[:4] != b'DDS ' or data[84:88] != b'DX10'
                or struct.unpack_from('<I', data, 4)[0] != 124
                or struct.unpack_from('<I', data, 76)[0] != 32
                or struct.unpack_from('<5I', data, 128) != (10, 3, 0, 1, 0)):
            raise ValueError('Generated clothing alpha requires native RGBA16_FLOAT DDS')
        height, width = struct.unpack_from('<2I', data, 12)
        mip_count = struct.unpack_from('<I', data, 28)[0]
        if not width or not height or not 1 <= mip_count <= 32:
            raise ValueError('Invalid clothing opacity dimensions')
        offset = 148
        levels = []
        for mip in range(mip_count):
            size = width * height * 8
            if offset + size > len(data): raise ValueError('Truncated clothing alpha mip')
            pixels = np.frombuffer(data, dtype='<f2', count=width*height*4, offset=offset).reshape(-1,4)
            if not np.isfinite(pixels).all() or np.any(pixels[:,3] > 1) or np.any(pixels[:,3] < 0):
                raise ValueError('Invalid clothing alpha pixels')
            levels.append(pixels[:,3].reshape(height, width).copy())
            offset += size
            if width == height == 1 and mip != mip_count-1:
                raise ValueError('Extra clothing alpha mip')
            if mip < mip_count-1: width, height = max(1,width//2), max(1,height//2)
        if width != 1 or height != 1 or offset != len(data):
            raise ValueError('Incomplete clothing opacity mip chain')
        digest = hashlib.sha256(data).hexdigest()
        if cache is not None:
            cache[path] = levels, digest, mip_count
        evidence.append(dict(texture=textures[0].get('File'), sha256=digest, mips=mip_count))
        surfaces.append((threshold, levels))
    if not evidence: raise ValueError('Empty clothing material')
    return surfaces, evidence


def opaque_native_surface(output, material_path, count):
    """Prove full opacity across every texel and mip without a UV approximation."""
    surfaces, evidence = native_alpha_surfaces(output, material_path)
    if any(not np.all(level > threshold) for threshold, levels in surfaces for level in levels):
        raise ValueError('Cutout clothing needs triangle opacity generation')
    return np.ones(count, dtype=bool), evidence


def alpha_intersections(skin, surfaces, selected):
    """Filter ray hits using UVs, subset materials and conservative mip footprints.

    All four bilinear texels must survive in every mip. This can retain extra
    hidden skin near cutout edges, but never treats a transparent opening as
    an opaque cover. No missing cache is replaced with all-opaque triangles.
    """
    stream = skin.streams[2]
    if struct.unpack_from('<4I', stream.data) != (0, 2, skin.n, 8):
        raise ValueError('Unsupported clothing coverage UV stream')
    uv = np.frombuffer(stream.data[24:], '<f4').reshape(-1, 2)
    if len(uv) != skin.n or not np.isfinite(uv).all():
        raise ValueError('Invalid clothing coverage UVs')
    materials = np.full(len(skin.faces), -1, dtype=int)
    for subset in skin.info['subset_records']:
        start, count, slot = (subset[k] for k in ('first_index', 'num_indices', 'material_id'))
        if start % 3 or count % 3 or start < 0 or start + count > len(skin.faces)*3 or not 0 <= slot < len(surfaces):
            raise ValueError('Invalid clothing coverage material subset')
        target = materials[start//3:(start+count)//3]
        if np.any(target != -1): raise ValueError('Overlapping clothing coverage subsets')
        target[:] = slot
    if np.any(materials == -1): raise ValueError('Unassigned clothing coverage triangles')
    face_uv, materials = uv[skin.faces[selected]], materials[selected]

    def accepts(triangle, u, v):
        coords = face_uv[triangle]
        point = coords[:,0] * (1-u-v)[:,None] + coords[:,1]*u[:,None] + coords[:,2]*v[:,None]
        result = np.ones(len(triangle), bool)
        for slot in np.unique(materials[triangle]):
            indices = np.flatnonzero(materials[triangle] == slot)
            threshold, levels = surfaces[slot]
            for level in levels:
                height, width = level.shape
                xy = np.floor(point[indices] * [width, height] - .5).astype(np.int64)
                for dx, dy in ((0,0), (1,0), (0,1), (1,1)):
                    result[indices] &= level[(xy[:,1]+dy) % height, (xy[:,0]+dx) % width] > threshold
        return result
    return accepts


def physical_alpha_intersections(skin, surfaces, selected):
    """Test the authored cut at the finest converted texture level.

    A distant mip averages opaque cloth with unused UV background. Requiring
    the 1x1 average to pass AlphaTest can reject every surface of a perfectly
    visible dress. Physical fitting/coverage must use the actual cut, just as
    source fitting does; distant texture filtering is not a new body opening.
    Bilinear footprints still have to be opaque, including beside cutouts.
    """
    return alpha_intersections(skin, [(threshold, levels[:1])
                                     for threshold, levels in surfaces], selected)


def unculled_garment_visibility(skin):
    """Visibility evidence for complete imported meshes with cleared hiding bits.

    This does not reconstruct the legacy cache. It describes the converted
    garment used for the coverage rays. Unknown/retained hiding is rejected.
    Texture cutouts still come from the required source opacity mask.
    """
    colors = skin.streams[3]
    if struct.unpack_from('<I', colors.data, 12)[0] != 4:
        raise ValueError('Expected RGBA garment visibility')
    alpha = np.frombuffer(colors.data[24:], dtype=np.uint8).reshape(-1, 4)[:, 3]
    if len(alpha) != skin.n or np.any(alpha != 255):
        raise ValueError('Converted garment retains unknown hiding membership')
    internal_alpha = np.frombuffer(skin.internal.data[95::64], dtype=np.uint8)
    if len(internal_alpha) != skin.ninternal or np.any(internal_alpha[skin.remap] != alpha):
        raise ValueError('Converted garment hiding buffers disagree')
    return np.ones(len(skin.faces), dtype=bool)


def covered_points(points, normals, triangles, inward=.06, outward=.12, accept_hit=None):
    """Test two-sided triangles along short surface-normal segments (metres)."""
    points, normals, triangles = (np.asarray(a, dtype=float) for a in (points, normals, triangles))
    result = np.zeros(len(points), dtype=bool)
    if not len(triangles):
        return result
    a = triangles[:, 0]
    edge1, edge2 = triangles[:, 1] - a, triangles[:, 2] - a
    # Broad phase only: preserve the exact intersection and alpha tests below.
    # Body regions previously tested every vertex against every outfit triangle,
    # even when their short normal segments could not reach that garment.
    # Expand bounds for the existing barycentric tolerance at triangle edges.
    padding = 2e-7 * (np.abs(edge1) + np.abs(edge2)) + 1e-12
    triangle_min = triangles.min(axis=1) - padding
    triangle_max = triangles.max(axis=1) + padding
    for start in range(0, len(points), 64):
        p, direction = points[start:start+64], normals[start:start+64]
        ends = np.concatenate((p - inward*direction, p + outward*direction))
        selected = np.flatnonzero((triangle_min <= ends.max(axis=0)).all(axis=1)
                                 & (triangle_max >= ends.min(axis=0)).all(axis=1))
        if not len(selected):
            continue
        # A block can span both arms or distant garment islands. Retain only
        # individual segment/triangle AABB pairs before allocating cross/dot
        # products; the exact ray and opacity tests below remain unchanged.
        end1, end2 = p - inward*direction, p + outward*direction
        low, high = np.minimum(end1, end2), np.maximum(end1, end2)
        pairs = ((triangle_min[selected][None] <= high[:, None]).all(axis=2)
                 & (triangle_max[selected][None] >= low[:, None]).all(axis=2))
        point_ids, local_triangles = np.nonzero(pairs)
        triangle_ids = selected[local_triangles]
        if not len(point_ids):
            continue
        e1, e2 = edge1[triangle_ids], edge2[triangle_ids]
        rays = direction[point_ids]
        h = np.cross(rays, e2)
        det = np.einsum('ij,ij->i', e1, h)
        valid = np.abs(det) > 1e-10
        inv = np.divide(1., det, out=np.zeros_like(det), where=valid)
        delta = p[point_ids] - a[triangle_ids]
        u = np.einsum('ij,ij->i', delta, h) * inv
        q = np.cross(delta, e1)
        v = np.einsum('ij,ij->i', rays, q) * inv
        distance = np.einsum('ij,ij->i', e2, q) * inv
        hits = valid & (u >= -1e-7) & (v >= -1e-7) & (u+v <= 1+1e-7)
        hits &= (distance >= -inward) & (distance <= outward)
        if accept_hit is not None:
            hits[hits] &= accept_hit(triangle_ids[hits], u[hits], v[hits])
        result[start + point_ids[hits]] = True
    return result


def normals(skin):
    vertices, faces = skin.vertices, skin.faces
    face_normals = np.cross(vertices[faces[:, 1]]-vertices[faces[:, 0]],
                            vertices[faces[:, 2]]-vertices[faces[:, 0]])
    # Weld duplicated render vertices using the engine's own internal remap.
    welded = np.zeros((skin.ninternal, 3))
    for column in range(3):
        np.add.at(welded, skin.remap[faces[:, column]], face_normals)
    result = welded[skin.remap]
    return result / np.maximum(np.linalg.norm(result, axis=1)[:, None], 1e-12)


def exterior_coverage(points, directions, test, candidates=None):
    """Conservative head coverage, including the neck/chest in source heads.

    A collar behind exposed skin is not a cover. A single normal ray can also
    hit the lip of an open neckline. Require opaque, outward hits along the
    normal and four surrounding 45-degree rays before hiding head vertices.
    ``test`` receives only surviving points and unit directions and must use
    outward segments (no inward allowance). It can combine visible garments.
    This is a bounded visibility test, not continuous or animated certification.
    """
    points, directions = (np.asarray(a, dtype=float) for a in (points, directions))
    if points.shape != directions.shape or points.ndim != 2 or points.shape[1] != 3:
        raise ValueError('Invalid head coverage points or directions')
    keep = np.ones(len(points), bool) if candidates is None else np.asarray(candidates, dtype=bool).copy()
    if keep.shape != (len(points),):
        raise ValueError('Head coverage candidates differ from target mesh')
    length = np.linalg.norm(directions, axis=1)
    keep &= np.isfinite(points).all(axis=1) & np.isfinite(length) & (length > 1e-12)
    ids = np.flatnonzero(keep)
    normal = directions[ids] / length[ids, None]
    axis = np.eye(3)[np.argmin(np.abs(normal), axis=1)]
    tangent = np.cross(normal, axis)
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:, None], 1e-12)
    second = np.cross(normal, tangent)
    for frame in (normal, normal+tangent, normal-tangent, normal+second, normal-second):
        active = np.flatnonzero(keep[ids])
        if not len(active):
            break
        direction = frame[active]
        direction /= np.linalg.norm(direction, axis=1)[:, None]
        keep[ids[active]] &= test(points[ids[active]], direction)
    return keep


def head_covered_points(points, directions, triangles, accept_hit=None):
    return exterior_coverage(points, directions, lambda p, n:
        covered_points(p, n, triangles, inward=0, outward=.12, accept_hit=accept_hit))


def apply_membership(blob, memberships):
    skin = CompiledSkin(blob)
    colors = skin.streams[3]
    if struct.unpack_from('<I', colors.data, 12)[0] != 4:
        raise ValueError('Expected RGBA hiding membership')
    alpha = np.full(skin.n, 255, dtype=np.uint8)
    for bit, covered in memberships.items():
        if bit not in range(4, 8) or len(covered) != skin.n:
            raise ValueError('Invalid native coverage allocation')
        # A shared vertex remains exposed if any render copy lacks coverage.
        # OR here used to turn an uncertain UV seam into a hole in the body.
        shared = np.ones(skin.ninternal, dtype=bool)
        np.logical_and.at(shared, skin.remap, covered)
        alpha[shared[skin.remap]] &= np.uint8(255 ^ (1 << bit))
    payload = bytearray(colors.data)
    payload[27::4] = alpha.tobytes()
    internal = bytearray(skin.internal.data)
    for i, remap in enumerate(skin.remap):
        internal[32 + int(remap)*64 + 63] = int(alpha[i])
    return write_chunks(skin.chunks, {colors.id: bytes(payload), skin.internal.id: bytes(internal)})


def triangle_safe_membership(skin, covered):
    """Never cull an adjacent triangle whose vertices lack coverage evidence.

    Native hiding is encoded on vertices, while the preview culls a triangle
    when any of its vertices is hidden. Erode uncertain boundary vertices so
    that this conservative culling cannot erase an uncovered neighbour.
    This is a vertex-boundary guarantee, not continuous surface certification.
    """
    covered = np.asarray(covered, dtype=bool).copy()
    if covered.shape != (skin.n,):
        raise ValueError('Coverage topology differs from target mesh')
    shared = np.ones(skin.ninternal, bool)
    np.logical_and.at(shared, skin.remap, covered)
    covered = shared[skin.remap]
    unsafe = skin.faces[~covered[skin.faces].all(axis=1)]
    shared[skin.remap[unsafe].ravel()] = False
    return shared[skin.remap]


def apply_fitted_membership(blob, memberships):
    """Fitting at a vertex is not permission to erase its exposed neighbours."""
    if not memberships:
        return blob
    skin = CompiledSkin(blob)
    covered = np.logical_or.reduce([np.asarray(value, dtype=bool) for value in memberships.values()])
    return apply_membership(blob, {4: triangle_safe_membership(skin, covered)})


def appearance_elements(derived):
    """Skin, scalp, hair and beard all need conditional garment coverage."""
    return [element for node in derived if node.tag in ('Body', 'Head', 'Hair', 'Beard')
            for element in node.findall('Elements/SkinElement')]


def register_fixed_body_coverage(output, component, namespace):
    """Retain exposed skin under merged outfits instead of replacing regions."""
    from character_lods import source_lods
    from setup_progress import work_progress
    folder = output / 'objects/characters' / component.get('FilePath')
    index = {p.name.lower(): p for p in folder.glob('*.skin')}
    derived = component.find('DerivedComponents')
    outfit = derived.find('Clothing')
    group_path = output / ('Libs/Tables/Character/ClothingHidingGroup__' + namespace + '.xml')
    groups = ET.parse(group_path).getroot()
    definitions = groups.find('ClothingHidingGroups')
    names = set(outfit.get('HidingGroups', '').split())
    def family(element):
        model = element.get('Model')
        return {0: folder / model, **{lod: index[name] for lod, name in source_lods(index, model).items()}}
    covers = []; alpha_cache = {}
    for element in outfit.findall('Elements/SkinElement'):
        surfaces, _ = native_alpha_surfaces(output, folder / element.get('Material'), cache=alpha_cache)
        levels = {}
        for level, path in family(element).items():
            skin = CompiledSkin(path.read_bytes())
            alpha = np.frombuffer(skin.streams[3].data[24:], np.uint8).reshape(-1, 4)[:, 3]
            visible = ~(((255 ^ alpha) & 16) != 0)[skin.faces].any(axis=1)
            levels[level] = (skin.vertices[skin.faces[visible]], physical_alpha_intersections(skin, surfaces, visible))
        covers.append(levels)
    report = []
    targets = [(element, family(element)) for element in appearance_elements(derived)]
    heads = set(derived.findall('Head/Elements/SkinElement'))
    done, total = 0, sum(len(levels)*len(covers) for _, levels in targets)
    work_progress('Clothing coverage checks', done, total)
    for element, target_family in targets:
        region = element.get('EquipmentPart')
        name = namespace + '_outfit_covers_' + region
        for level, path in target_family.items():
            skin = CompiledSkin(path.read_bytes())
            covered = np.zeros(skin.n, bool)
            direction = normals(skin)
            for levels in covers:
                triangles, accept = levels[min(level, max(levels))]
                coverage = head_covered_points if element in heads else covered_points
                covered |= coverage(skin.vertices, direction, triangles, accept_hit=accept)
                done += 1
                work_progress('Clothing coverage checks', done, total, f'{region}, LOD {level}')
            covered = triangle_safe_membership(skin, covered)
            path.write_bytes(apply_membership(path.read_bytes(), {4: covered}))
            report.append(dict(target=path.name, lod=level, covered_vertices=int(covered.sum()), total_vertices=skin.n,
                               coverage_test='outward_head_cone' if element in heads else 'normal_segment'))
        if name not in names:
            ET.SubElement(definitions, 'HidingGroup', Name=name, Index='4', EquipmentPart=region)
            names.add(name)
    outfit.set('HidingGroups', ' '.join(sorted(names)))
    group_path.write_bytes(xml(groups))
    return dict(policy='Visible merged outfit surfaces with opaque alpha; preserve exposed body regions',
                layers=report, runtime_verified=False)


def register_coverage(output, component, garments, tables, namespace):
    from character_lods import source_lods
    from setup_progress import work_progress
    from clothing_fitting import source_layer_contracts, higher_layers
    folder = output / 'objects/characters' / component.get('FilePath')
    derived = component.find('DerivedComponents')
    parts = [g['part'] for g in garments]
    contracts = source_layer_contracts(parts, tables)
    higher = {i: higher_layers(i, parts, contracts) for i in range(len(parts))}
    # Higher garments are processed first, so hidden inner triangles cannot
    # subsequently be used as proof of opaque body coverage.
    order = sorted(range(len(garments)), key=lambda i: (len(higher[i]), garments[i]['stem']))
    if len(order) > 4:
        raise ValueError('Outfit needs more than four independent native hiding groups')
    groups = ET.Element('database', name='barbora')
    definitions = ET.SubElement(groups, 'ClothingHidingGroups', version='2')
    index = {p.name.lower(): p for p in folder.glob('*.skin')}
    def family(element):
        model = element.get('Model')
        return {0: folder / model, **{lod: index[name] for lod, name in source_lods(index, model).items()}}
    targets = [(None, element, family(element)) for element in appearance_elements(derived)]
    heads = set(derived.findall('Head/Elements/SkinElement'))
    nodes = {}
    families = {}
    for i, garment in enumerate(garments):
        node = derived.find("Clothing[@Name='" + namespace + '_' + garment['stem'] + "']")
        nodes[i] = node
        element = node.find('Elements/SkinElement')
        families[i] = family(element)
        targets.append((i, element, families[i]))
    masks = {}; report = []; used_groups = set(); visibility_sources = []; alpha_cache = {}
    done = 0
    total = sum(len(family) for i in order for target_index, _, family in targets
                if target_index is None or i in higher[target_index])
    work_progress('Clothing coverage checks', done, total)
    for i in order:
        garment = garments[i]; node = nodes[i]
        surfaces, evidence = native_alpha_surfaces(output, folder / (garment['stem'] + '.mtl'), cache=alpha_cache)
        covers = {}
        for level, path in families[i].items():
            cover = CompiledSkin(path.read_bytes())
            # Legacy caches describe the original renderer's culling state.
            # Test the complete converted surface at every authored LOD;
            # actual UV alpha and the newly computed upper-layer masks decide
            # which surfaces cover skin, not the old renderer's triangle bits.
            opaque = unculled_garment_visibility(cover)
            if masks.get(path):
                hidden = np.logical_or.reduce(list(masks[path].values()))
                opaque &= ~hidden[cover.faces].any(axis=1)
            covers[level] = (cover.vertices[cover.faces[opaque]], physical_alpha_intersections(cover, surfaces, opaque), int(opaque.sum()))
        visibility_sources.append(dict(garment=garment['stem'], lods=sorted(covers),
            opacity='converted_finest_alpha_intersections', evidence=evidence))
        names = []
        for target_index, element, target_family in targets:
            if target_index is not None and i not in higher[target_index]:
                continue
            for level, path in target_family.items():
                cover_level = min(level, max(covers))
                triangles, accept_hit, count = covers[cover_level]
                skin = CompiledSkin(path.read_bytes())
                coverage = head_covered_points if element in heads else covered_points
                covered = coverage(skin.vertices, normals(skin), triangles, accept_hit=accept_hit)
                done += 1
                work_progress('Clothing coverage checks', done, total, f'{element.get("EquipmentPart")}, LOD {level}')
                if target_index is not None:
                    target = garments[target_index]
                    source_cover = (target.get('source_coverage', {}).get(garment['stem']) if level == 0 else
                                    target.get('lod_coverage', {}).get(level, {}).get(garment['stem']))
                    if source_cover is not None:
                        if np.asarray(source_cover).shape != (skin.n,):
                            raise ValueError('Pre-fit coverage topology differs from converted garment')
                        covered |= np.asarray(source_cover, dtype=bool)
                covered = triangle_safe_membership(skin, covered)
                if not covered.any():
                    continue
                bit = 4 + i
                masks.setdefault(path, {})[bit] = covered
                name = namespace + '_' + garment['stem'] + '_covers_' + element.get('EquipmentPart')
                if name not in used_groups:
                    ET.SubElement(definitions, 'HidingGroup', Name=name, Index=str(bit), EquipmentPart=element.get('EquipmentPart'))
                    used_groups.add(name)
                names.append(name)
                report.append(dict(cover=garment['stem'], target=path.name, lod=level, cover_lod=cover_level,
                    covered_vertices=int(covered.sum()), total_vertices=skin.n, candidate_triangles=count,
                    opacity_test='UV_alpha_at_intersection', hiding_group=name, bit=bit,
                    coverage_test='outward_head_cone' if element in heads else 'normal_segment'))
        node.set('HidingGroups', ' '.join(sorted(set(names))))
        for other in derived.findall('Clothing'):
            if other.get('ArmorType') is None and any(e.get('Model') == garment['stem'] + '.skin' for e in other.findall('Elements/SkinElement')):
                other.set('HidingGroups', node.get('HidingGroups'))
    for path, membership in masks.items():
        path.write_bytes(apply_membership(path.read_bytes(), membership))
    path = output / ('Libs/Tables/Character/ClothingHidingGroup__' + namespace + '.xml')
    path.write_bytes(xml(groups))
    return dict(policy='Source layer contracts; visible converted surfaces; conservative triangle-boundary coverage at every authored LOD',
                inward_metres=.06, outward_metres=.12, layers=report,
                visibility_sources=visibility_sources, runtime_verified=False)
