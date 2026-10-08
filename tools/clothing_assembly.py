"""Assemble source garment surfaces into a native region's single draw mesh.

KCD2 selects one clothing geometry per equipment region. A converted outfit
therefore needs a combined region mesh wherever independent legacy surfaces
overlap. Materials remain separate subsets; source skin weights are retained.
This assembles a fixed outfit, not arbitrary runtime inventory combinations.
"""
import math
import struct
import copy
import xml.etree.ElementTree as ET

from clothing_regions import Chunk, Skin, write_chunks, split_skin_regions
from clothing_appearance import bake_clothing_variant


def convert_fixed_outfit(garments, material_prefix):
    """Convert explicit garment/face assignments into native region assets.

    Each garment supplies ``skin`` bytes, a material XML root, a ``name``,
    either ``region`` or one ``face_regions`` label per triangle, and optionally
    ``morph``. The caller owns region classification and native table roles.
    Returns {region: {skin, material, sources, triangles}} without writing files.
    Every source triangle is retained exactly once, including alpha-cut surfaces.
    """
    if not garments:
        raise ValueError('No garments supplied')
    groups = {}
    for garment in garments:
        blob = garment['skin']
        if garment.get('morph'):
            blob = bake_clothing_variant(blob, garment['morph'])
        skin = Skin(blob)
        labels = garment.get('face_regions')
        if labels is None:
            labels = [garment['region']] * len(skin.faces)
        materials = garment['material'].find('SubMaterials')
        slots = list(materials) if materials is not None else [garment['material']]
        required = max(struct.unpack_from('<I', r, 16)[0] for r in skin.subset_records)+1
        if len(slots) < required:
            raise ValueError('Material slots do not cover garment subsets')
        for region, partition in split_skin_regions(blob, labels).items():
            groups.setdefault(region, []).append((garment['name'], partition, slots))
    result = {}
    for region, entries in groups.items():
        material = ET.Element('Material', Name='Imported outfit '+region, MtlFlags='256')
        children = ET.SubElement(material, 'SubMaterials')
        bases = []
        for _, _, slots in entries:
            bases.append(len(children))
            children.extend(copy.deepcopy(slots))
        blob = assemble_region_skins([e[1] for e in entries], bases, material_prefix+region)
        result[region] = dict(skin=blob, material=material,
                              sources=[e[0] for e in entries], triangles=len(Skin(blob).faces))
    return result


def assemble_region_skins(blobs, material_bases, material_name):
    """Join compatible compiled skins and offset their material/vertex indices.

    Variants must be baked first. Skeleton order and all used joint bind poses
    must agree. Physics metadata comes from the first input and is not certified
    for gameplay collision; this function targets skinned rendering.
    """
    if not blobs or len(blobs) != len(material_bases):
        raise ValueError('One material base is required per input skin')
    if any(not isinstance(v, int) or v < 0 for v in material_bases):
        raise ValueError('Invalid material base')
    if len(material_name.encode('ascii')) >= 128:
        raise ValueError('Material name exceeds CGF field')
    skins = [Skin(b) for b in blobs]
    template = skins[0]
    bones = template.one(0x2000)
    if bones.version != 0x800 or (len(bones.data)-32) % 584:
        raise ValueError('Unsupported bones')
    bone_count = (len(bones.data)-32)//584
    stream_kinds = set().union(*(s.streams for s in skins)) - {5}
    if not stream_kinds <= {0, 1, 2, 3, 6, 9} or not {0, 1, 2, 6, 9} <= stream_kinds:
        raise ValueError('Unsupported garment streams')
    widths = {0: 12, 1: 12, 2: 8, 3: 4, 6: 16, 9: 12}
    streams = {k: bytearray() for k in stream_kinds}
    indices = []
    internal_faces = []
    internal_vertices = bytearray(template.one(0x2005).data[:32])
    remap = []
    subsets = []
    boxes = {}
    external_offset = internal_offset = 0
    positions = []
    for skin, material_base in zip(skins, material_bases):
        candidate_bones = skin.one(0x2000)
        if candidate_bones.version != 0x800 or len(candidate_bones.data) != len(bones.data):
            raise ValueError('Skeleton sizes differ')
        for i in range(bone_count):
            offset = 32+i*584
            if (bones.data[offset:offset+4] != candidate_bones.data[offset:offset+4]
                    or bones.data[offset+312:offset+584] != candidate_bones.data[offset+312:offset+584]):
                raise ValueError('Skeleton names, hierarchy or controller IDs differ')
        weights = skin.streams[9]
        for offset in range(24, len(weights.data), 12):
            joints = struct.unpack_from('<4H', weights.data, offset)
            for joint, weight in zip(joints, weights.data[offset+8:offset+12]):
                if not weight:
                    continue
                if joint >= bone_count:
                    raise ValueError('Invalid weighted joint')
                address = 32+joint*584+216
                a = struct.unpack_from('<24f', bones.data, address)
                b = struct.unpack_from('<24f', candidate_bones.data, address)
                if any(not math.isfinite(x) or not math.isfinite(y) or abs(x-y) > 1e-5 for x, y in zip(a, b)):
                    raise ValueError('A weighted joint has a different bind pose')
        morphs = [c for c in skin.chunks if c.kind == 0x2002]
        if any(c.data != struct.pack('<I', 0) for c in morphs):
            raise ValueError('Bake garment variants before assembly')
        internal = skin.one(0x2005)
        if internal.version != 0x800 or (len(internal.data)-32) % 64:
            raise ValueError('Unsupported internal vertices')
        internal_count = (len(internal.data)-32)//64
        if external_offset+skin.vertex_count > 65535 or internal_offset+internal_count > 65535:
            raise ValueError('Assembled skin exceeds 16-bit vertex capacity')
        if any(i >= internal_count for i in skin.remap):
            raise ValueError('Invalid internal vertex mapping')
        for kind in stream_kinds:
            source = skin.streams.get(kind)
            if source is None:
                if kind != 3:
                    raise ValueError('A required stream is absent')
                streams[kind].extend(b'\xff\xff\xff\xff' * skin.vertex_count)
            else:
                if struct.unpack_from('<I', source.data, 12)[0] != widths[kind]:
                    raise ValueError('Incompatible stream width')
                streams[kind].extend(source.data[24:])
        for record in skin.subset_records:
            first, count, first_vertex, vertices, material = struct.unpack_from('<5I', record)
            record = bytearray(record)
            struct.pack_into('<5I', record, 0, len(indices)+first, count,
                             external_offset+first_vertex, vertices, material_base+material)
            subsets.append(bytes(record))
        indices.extend(v+external_offset for face in skin.faces for v in face)
        internal_faces.extend(v+internal_offset for face in struct.iter_unpack('<3H', skin.internal_faces.data) for v in face)
        internal_vertices.extend(internal.data[32:])
        remap.extend(v+internal_offset for v in skin.remap)
        positions.extend(skin.positions())
        for box in skin.chunks:
            if box.kind != 0x3004:
                continue
            if box.version != 0x801 or len(box.data) < 32:
                raise ValueError('Unsupported bone bounds')
            joint = struct.unpack_from('<I', box.data)[0]
            count = struct.unpack_from('<I', box.data, 28)[0]
            if len(box.data) != 32+2*count:
                raise ValueError('Invalid bone bounds count')
            members = [v[0] for v in struct.iter_unpack('<H', box.data[32:])]
            if joint >= bone_count or any(v >= skin.vertex_count for v in members):
                raise ValueError('Invalid bone bounds references')
            boxes.setdefault(joint, []).extend(v+external_offset for v in members)
        external_offset += skin.vertex_count
        internal_offset += internal_count
    mesh = bytearray(template.mesh.data)
    struct.pack_into('<3I', mesh, 8, external_offset, len(indices), len(subsets))
    bounds = [min(p[k] for p in positions) for k in range(3)] + [max(p[k] for p in positions) for k in range(3)]
    struct.pack_into('<6f', mesh, 108, *bounds)
    chunks = [c for c in template.chunks if c.kind != 0x3004]
    next_id = max(c.id for c in template.chunks)+1
    replacements = {}
    for kind, payload in streams.items():
        source = template.streams.get(kind)
        if source is None:
            ident = next_id
            next_id += 1
            chunks.append(Chunk(0x1016, 0x800, ident, b''))
            struct.pack_into('<I', mesh, 28+kind*4, ident)
        else:
            ident = source.id
        replacements[ident] = struct.pack('<6I', 0, kind, external_offset, widths[kind], 0, 0)+payload
    replacements[template.mesh.id] = bytes(mesh)
    replacements[template.indices.id] = struct.pack('<6I', 0, 5, len(indices), 2, 0, 0)+struct.pack('<'+'H'*len(indices), *indices)
    replacements[template.subsets.id] = struct.pack('<4I', 0, len(subsets), 0, 0)+b''.join(subsets)
    replacements[template.one(0x2005).id] = bytes(internal_vertices)
    replacements[template.remap_chunk.id] = struct.pack('<'+'H'*len(remap), *remap)
    replacements[template.internal_faces.id] = struct.pack('<'+'H'*len(internal_faces), *internal_faces)
    # Older female garments can retain an unused second material chunk. Resolve
    # the mesh node's explicit material instead of assuming there is only one.
    material_chunks = [c for c in template.chunks if c.kind == 0x1014]
    if len(material_chunks) == 1:
        material_chunk = material_chunks[0]
    else:
        nodes = [c for c in template.chunks if c.kind == 0x100b and c.version == 0x824
                 and len(c.data) >= 80 and struct.unpack_from('<I', c.data, 64)[0] == template.mesh.id]
        ids = {struct.unpack_from('<I', c.data, 76)[0] for c in nodes}
        selected = [c for c in material_chunks if c.id in ids]
        if len(selected) != 1:
            raise ValueError('Ambiguous mesh-node material binding')
        material_chunk = selected[0]
    if material_chunk.version != 0x802:
        raise ValueError('Unsupported material chunk')
    material_count = max(struct.unpack_from('<I', r, 16)[0] for r in subsets)+1
    replacements[material_chunk.id] = (material_name.encode('ascii').ljust(128, b'\0')
        + struct.pack('<I', material_count) + struct.pack('<i', -1)*material_count
        + b''.join(f'layer_{i}\0'.encode('ascii') for i in range(material_count)))
    for joint, members in sorted(boxes.items()):
        members = sorted(set(members))
        if not members:
            continue
        bounds = [min(positions[i][k] for i in members) for k in range(3)] + [max(positions[i][k] for i in members) for k in range(3)]
        payload = struct.pack('<I6fI', joint, *bounds, len(members)) + struct.pack('<'+'H'*len(members), *members)
        chunks.append(Chunk(0x3004, 0x801, next_id, payload))
        next_id += 1
    output = write_chunks(chunks, replacements)
    Skin(output)
    return output
