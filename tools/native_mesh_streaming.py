"""Experimental native-shaped CGF/CGFM pairs; requires native runtime validation.

Unlike the rejected mesh_streaming splitter, the companion contains render
nodes and their dependencies only. Resident physics and foliage stay in CGF.
"""
import struct
from clothing_regions import read_chunks, write_chunks
from mesh_streaming import split as resident_split, validate_pair as validate_geometry_pair


def resident_reason(source):
    """Physics without MeshNotNeeded consumes the render vertices at load time.

    CGFContent::ePhysicsalizeFlag_MeshNotNeeded is bit 2 of nFlags2.
    Removing the referenced streams makes LoadPhysGeometry receive null
    vertices/indices. Preserve the complete file until physics is recompiled.
    """
    for chunk in read_chunks(source):
        if chunk.kind != 0x1000:
            continue
        if chunk.version != 0x801 or len(chunk.data) != 264:
            raise ValueError('Unsupported static mesh layout')
        flags, physicalize = struct.unpack_from('<2I', chunk.data)
        if flags & 1:
            continue
        if any(struct.unpack_from('<4I', chunk.data, 92)) and not physicalize & 4:
            return 'physics_requires_render_geometry'
    return None


def validate_pair(header, payload):
    validate_geometry_pair(header, payload)
    for mesh in read_chunks(header):
        if mesh.kind != 0x1000:
            continue
        flags, physicalize = struct.unpack_from('<2I', mesh.data)
        if (flags & 1 and any(struct.unpack_from('<4I', mesh.data, 92))
                and not physicalize & 4):
            raise ValueError('Stripped mesh physics requires resident render geometry')


def split(source):
    if resident_reason(source):
        return source, None
    header, full = resident_split(source)
    if full is None:
        return header, None
    chunks = read_chunks(full)
    by_id = {c.id: c for c in chunks}
    heads = {c.id: c for c in read_chunks(header)}
    meshes = {c.id for c in chunks if c.kind == 0x1000 and
              c.data != heads[c.id].data}
    nodes = [c for c in chunks if c.kind == 0x100b and
             struct.unpack_from('<i', c.data, 64)[0] in meshes]
    node_ids = {c.id for c in nodes}
    keep = set(meshes) | node_ids
    # Structural helper ancestors retain the authored transform hierarchy.
    for node in nodes:
        parent = struct.unpack_from('<i', node.data, 68)[0]
        seen = {node.id}
        while parent >= 0 and parent not in node_ids:
            ancestor = by_id.get(parent)
            if parent in seen or ancestor is None or ancestor.kind != 0x100b:
                raise ValueError('Streamed render node has an invalid resident-only parent')
            seen.add(parent)
            obj = struct.unpack_from('<i', ancestor.data, 64)[0]
            if obj not in by_id or by_id[obj].kind != 0x1001:
                raise ValueError('Streamed render node has an unsupported resident-only parent')
            keep.update((parent, obj))
            parent = struct.unpack_from('<i', ancestor.data, 68)[0]
    nodes = [c for c in chunks if c.kind == 0x100b and c.id in keep]
    node_ids = {c.id for c in nodes}
    # Keep the material graph; material chunks refer to submaterials by ID.
    keep.update(c.id for c in chunks if c.kind == 0x1014)
    replacements = {}
    for node in nodes:
        parent = struct.unpack_from('<i', node.data, 68)[0]
        if parent >= 0 and parent not in node_ids:
            raise ValueError('Streamed render node has a resident-only parent')
        raw = bytearray(node.data)
        children = sum(struct.unpack_from('<i', c.data, 68)[0] == node.id for c in nodes)
        struct.pack_into('<i', raw, 72, children)
        for slot, ident in enumerate(struct.unpack_from('<3i', raw, 188)):
            if ident > 0:
                if ident not in by_id or by_id[ident].kind != 0x100d:
                    # External static LODs can retain IDs of controllers that
                    # were stripped from that file. They cannot animate there.
                    struct.pack_into('<i', raw, 188+slot*4, -1)
                    continue
                keep.add(ident)
        replacements[node.id] = bytes(raw)
    for ident in meshes:
        mesh = by_id[ident]
        keep.add(struct.unpack_from('<I', mesh.data, 20)[0])
        keep.update(i for i in struct.unpack_from('<16I', mesh.data, 28) if i)
        raw = bytearray(mesh.data)
        raw[92:108] = bytes(16)  # physics remains resident in the header
        replacements[ident] = bytes(raw)
    if 0 not in by_id:
        keep.discard(0)
    if any(i not in by_id for i in keep):
        raise ValueError('Missing render dependency')
    payload = write_chunks([c for c in chunks if c.id in keep], replacements)
    if any(c.kind not in (0x1000, 0x1001, 0x100b, 0x100d, 0x1014, 0x1016, 0x1017)
           for c in read_chunks(payload)):
        raise ValueError('Unexpected resident chunk in stream payload')
    validate_pair(header, payload)
    return header, payload
