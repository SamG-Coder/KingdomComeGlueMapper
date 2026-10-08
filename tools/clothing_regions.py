"""Lossless triangle partitioning for KCD1 compiled garment skins.

Region assignment is a separate policy. Splitting retains vertex indices, skin
weights, bind poses, morph targets and vertex masks byte-for-byte. It changes
draw indices, subset ranges, and compiled internal faces only. Keeping unused
vertices costs memory, but avoids corrupting the compressed morph vertex order.
"""
from collections import Counter
import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import tempfile


@dataclass(frozen=True)
class Chunk:
    kind: int
    version: int
    id: int
    data: bytes


def read_chunks(blob):
    if len(blob) < 16 or blob[:8] != b'CrCh' + struct.pack('<I', 0x746):
        raise ValueError('Expected a little-endian 0x746 compiled skin')
    count, table = struct.unpack_from('<II', blob, 8)
    if table < 16 or table + count * 16 > len(blob):
        raise ValueError('Invalid chunk table')
    result = []
    occupied = [(0, 16), (table, table + count * 16)]
    for i in range(count):
        kind, version, ident, size, offset = struct.unpack_from('<HHIII', blob, table + i * 16)
        if offset + size > len(blob) or any(offset < end and offset + size > start for start, end in occupied):
            raise ValueError('Overlapping or truncated chunk')
        occupied.append((offset, offset + size))
        result.append(Chunk(kind, version, ident, bytes(blob[offset:offset + size])))
    if len({c.id for c in result}) != count:
        raise ValueError('Duplicate chunk IDs')
    return result


def write_chunks(chunks, replacements):
    output = bytearray(b'CrCh' + struct.pack('<III', 0x746, len(chunks), 16))
    output.extend(bytes(16 * len(chunks)))
    for i, chunk in enumerate(chunks):
        output.extend(bytes((-len(output)) % 4))
        payload = replacements.get(chunk.id, chunk.data)
        struct.pack_into('<HHIII', output, 16 + i * 16,
                         chunk.kind, chunk.version, chunk.id, len(payload), len(output))
        output.extend(payload)
    return bytes(output)


class Skin:
    def __init__(self, blob):
        self.chunks = read_chunks(blob)
        by_id = {c.id: c for c in self.chunks}
        meshes = [c for c in self.chunks if c.kind == 0x1000]
        if len(meshes) != 1 or meshes[0].version not in (0x800, 0x801) or len(meshes[0].data) != 264:
            raise ValueError('Only one standard 0x800/0x801 mesh is supported')
        self.mesh = meshes[0]
        flags, _, self.vertex_count, index_count, subset_count, subset_id = struct.unpack_from('<6I', self.mesh.data)
        if flags & 5:
            raise ValueError('Empty meshes and extra bone weights are not supported')
        self.streams = {}
        for kind, ident in enumerate(struct.unpack_from('<16I', self.mesh.data, 28)):
            if not ident:
                continue
            chunk = by_id.get(ident)
            if chunk is None or chunk.kind != 0x1016 or chunk.version != 0x800 or len(chunk.data) < 24:
                raise ValueError('Invalid mesh stream reference')
            _, stream_type, count, width = struct.unpack_from('<4I', chunk.data)
            if stream_type != kind or len(chunk.data) != 24 + count * width:
                raise ValueError('Invalid stream layout')
            if count != (index_count if kind == 5 else self.vertex_count):
                raise ValueError('Stream count does not match mesh')
            self.streams[kind] = chunk
        if 5 not in self.streams:
            raise ValueError('Missing indices')
        self.indices = self.streams[5]
        width = struct.unpack_from('<I', self.indices.data, 12)[0]
        if width not in (2, 4) or index_count % 3:
            raise ValueError('Invalid triangle index stream')
        self.index_format = 'H' if width == 2 else 'I'
        self.faces = list(struct.iter_unpack('<3' + self.index_format, self.indices.data[24:]))
        if any(i >= self.vertex_count for face in self.faces for i in face):
            raise ValueError('Vertex index out of range')
        self.subsets = by_id.get(subset_id)
        if self.subsets is None or self.subsets.kind != 0x1017 or self.subsets.version != 0x800:
            raise ValueError('Invalid subset reference')
        if len(self.subsets.data) < 16:
            raise ValueError('Truncated subsets')
        subset_flags, count = struct.unpack_from('<2I', self.subsets.data)
        if subset_flags != 0 or count != subset_count or len(self.subsets.data) != 16 + count * 36:
            raise ValueError('Unsupported subset extensions or malformed subsets')
        self.subset_records = [self.subsets.data[16+i*36:52+i*36] for i in range(count)]
        self.face_subsets = [-1] * len(self.faces)
        for number, record in enumerate(self.subset_records):
            first, size, first_vertex, vertices = struct.unpack_from('<4I', record)
            if first % 3 or size % 3 or first + size > index_count or first_vertex + vertices > self.vertex_count:
                raise ValueError('Invalid subset range')
            for face in range(first // 3, (first + size) // 3):
                if self.face_subsets[face] != -1:
                    raise ValueError('Overlapping subset ranges')
                self.face_subsets[face] = number
        if -1 in self.face_subsets:
            raise ValueError('Indices outside subset ranges')
        self.internal_faces = self.one(0x2004)
        self.remap_chunk = self.one(0x2006)
        if self.internal_faces.version != 0x800 or self.remap_chunk.version != 0x800:
            raise ValueError('Unsupported compiled skin mapping')
        if len(self.remap_chunk.data) != 2 * self.vertex_count or len(self.internal_faces.data) % 6:
            raise ValueError('Invalid compiled skin mapping size')
        self.remap = [v[0] for v in struct.iter_unpack('<H', self.remap_chunk.data)]
        mapped = [tuple(self.remap[i] for i in face) for face in self.faces]
        if Counter(mapped) != Counter(struct.iter_unpack('<3H', self.internal_faces.data)):
            raise ValueError('Internal and render triangles disagree')

    def one(self, kind):
        chunks = [c for c in self.chunks if c.kind == kind]
        if len(chunks) != 1:
            raise ValueError(f'Expected one chunk of type {kind:#x}')
        return chunks[0]

    def positions(self):
        chunk = self.streams.get(0)
        if chunk is None or struct.unpack_from('<I', chunk.data, 12)[0] != 12:
            raise ValueError('Expected float3 positions')
        positions = list(struct.iter_unpack('<3f', chunk.data[24:]))
        if any(not math.isfinite(x) for p in positions for x in p):
            raise ValueError('Non-finite vertex position')
        return positions


def split_skin_regions(blob, face_regions):
    """Return {region: skin_bytes}, assigning every source triangle exactly once.

    face_regions follows the render-index triangle order. The caller supplies
    semantic regions; this function never guesses a garment's cut or coverage.
    Unknown chunk payloads are preserved. Unsupported geometry fails explicitly.
    """
    skin = Skin(blob)
    regions = list(face_regions)
    if len(regions) != len(skin.faces) or any(not isinstance(r, str) or not r for r in regions):
        raise ValueError('Exactly one nonempty region name is required per triangle')
    outputs = {}
    for region in sorted(set(regions)):
        selected = [i for i, value in enumerate(regions) if value == region]
        faces = []
        records = []
        for subset, original in enumerate(skin.subset_records):
            subset_faces = [skin.faces[i] for i in selected if skin.face_subsets[i] == subset]
            if not subset_faces:
                continue
            vertices = [i for face in subset_faces for i in face]
            record = bytearray(original)
            struct.pack_into('<4I', record, 0, len(faces) * 3, len(subset_faces) * 3,
                             min(vertices), max(vertices) - min(vertices) + 1)
            records.append(bytes(record))
            faces.extend(subset_faces)
        mesh = bytearray(skin.mesh.data)
        struct.pack_into('<2I', mesh, 12, len(faces) * 3, len(records))
        subset_header = bytearray(skin.subsets.data[:16])
        struct.pack_into('<I', subset_header, 4, len(records))
        index_header = bytearray(skin.indices.data[:24])
        struct.pack_into('<I', index_header, 8, len(faces) * 3)
        replacements = {
            skin.mesh.id: bytes(mesh),
            skin.subsets.id: bytes(subset_header) + b''.join(records),
            skin.indices.id: bytes(index_header) + b''.join(struct.pack('<3' + skin.index_format, *f) for f in faces),
            skin.internal_faces.id: b''.join(struct.pack('<3H', *(skin.remap[i] for i in f)) for f in faces),
        }
        result = write_chunks(skin.chunks, replacements)
        Skin(result)  # Verify each output's references, ranges and internal faces.
        outputs[region] = result
    return outputs


def assign_upper_garment_regions(blob, *, waist_z, arm_weight_threshold=0.5):
    """Suggest torso/arms/waist cuts using source bind-pose weights and a plane.

    waist_z is explicitly in this skin's local coordinates. This heuristic is
    suitable for a preview; authored seam labels can instead be supplied directly
    to split_skin_regions. It does not claim to convert native hiding masks.
    """
    if not math.isfinite(waist_z) or not 0 < arm_weight_threshold <= 1:
        raise ValueError('Invalid region thresholds')
    skin = Skin(blob)
    bones = skin.one(0x2000)
    if bones.version != 0x800 or (len(bones.data) - 32) % 584:
        raise ValueError('Unsupported compiled bones')
    names = [bones.data[o+312:o+568].split(b'\0')[0].decode('ascii')
             for o in range(32, len(bones.data), 584)]
    arm_bones = {i for i, name in enumerate(names)
                 if name.startswith(('LeftArm', 'RightArm', 'LeftForeArm', 'RightForeArm',
                                     'LeftHand', 'RightHand', 'LeftInHand', 'RightInHand',
                                     'LeftElbow', 'RightElbow'))}
    weights = skin.streams.get(9)
    if not arm_bones or weights is None or struct.unpack_from('<I', weights.data, 12)[0] != 12:
        raise ValueError('Expected named arm bones and four-influence bone mappings')
    arm_weights = []
    for offset in range(24, len(weights.data), 12):
        ids = struct.unpack_from('<4H', weights.data, offset)
        values = weights.data[offset+8:offset+12]
        if any(i >= len(names) and w for i, w in zip(ids, values)) or not sum(values):
            raise ValueError('Invalid bone influences')
        arm_weights.append(sum(w for i, w in zip(ids, values) if i in arm_bones) / sum(values))
    positions = skin.positions()
    return ['arms' if sum(arm_weights[i] for i in face) / 3 >= arm_weight_threshold
            else 'waist' if sum(positions[i][2] for i in face) / 3 < waist_z
            else 'torso' for face in skin.faces]


def assign_body_regions(blob):
    """Assign each body triangle by its summed named bone influences.

    This preserves a closed body's complete surface across five native regions.
    It is a preview partition, not an authored clothing-hiding mask.
    """
    skin = Skin(blob)
    bones = skin.one(0x2000)
    if bones.version != 0x800 or (len(bones.data)-32) % 584:
        raise ValueError('Unsupported compiled bones')
    names = [bones.data[o+312:o+568].split(b'\0')[0].decode('ascii')
             for o in range(32, len(bones.data), 584)]
    def region(name):
        name = name.removeprefix('Left').removeprefix('Right')
        if name.startswith(('Hand', 'InHand')): return 'hands'
        if name.startswith(('Foot', 'Toe', 'Heel')): return 'feet'
        if name.startswith(('UpLeg', 'Leg', 'Knee')): return 'legs'
        if name.startswith(('Arm', 'ForeArm', 'Elbow')): return 'arms'
        return 'torso'
    weights = skin.streams.get(9)
    if weights is None or struct.unpack_from('<I', weights.data, 12)[0] != 12:
        raise ValueError('Expected four-influence bone mappings')
    values = []
    for offset in range(24, len(weights.data), 12):
        scores = Counter()
        for joint, weight in zip(struct.unpack_from('<4H', weights.data, offset), weights.data[offset+8:offset+12]):
            if weight:
                if joint >= len(names): raise ValueError('Invalid weighted joint')
                scores[region(names[joint])] += weight
        if not scores: raise ValueError('Unweighted body vertex')
        values.append(scores)
    labels = []
    for face in skin.faces:
        scores = sum((values[v] for v in face), Counter())
        labels.append(max(sorted(scores), key=scores.get))
    return labels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('skin', type=Path)
    parser.add_argument('output', type=Path, help='New output directory; existing directories are refused')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--face-regions', type=Path, help='JSON list with one label per render triangle')
    mode.add_argument('--waist-z', type=float, help='Use the upper-garment preview classifier')
    parser.add_argument('--arm-weight-threshold', type=float, default=0.5)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output directory already exists')
    blob = args.skin.read_bytes()
    labels = (json.loads(args.face_regions.read_text(encoding='utf-8')) if args.face_regions
              else assign_upper_garment_regions(blob, waist_z=args.waist_z,
                                               arm_weight_threshold=args.arm_weight_threshold))
    if not isinstance(labels, list) or any(not isinstance(r, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', r) for r in labels):
        parser.error('Region labels must be safe lowercase names')
    partitions = split_skin_regions(blob, labels)
    manifest = dict(source=str(args.skin.resolve()), source_sha256=hashlib.sha256(blob).hexdigest(),
                    triangle_count=len(labels), region_triangles=dict(Counter(labels)),
                    assignment='explicit' if args.face_regions else 'preview_heuristic',
                    morphs='Preserved source payloads; native morph semantics not converted',
                    vertex_masks='Preserved source payloads; native hiding semantics not converted',
                    unused_vertices_preserved=True, visual_verified=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='clothing-', dir=args.output.parent))
    try:
        for region, payload in partitions.items():
            (temporary/(region+'.skin')).write_bytes(payload)
        (temporary/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        temporary.rename(args.output)
    finally:
        if temporary.exists():
            if temporary.resolve().parent != args.output.parent.resolve() or not temporary.name.startswith('clothing-'):
                raise RuntimeError('Refusing cleanup outside the temporary output directory')
            shutil.rmtree(temporary)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
