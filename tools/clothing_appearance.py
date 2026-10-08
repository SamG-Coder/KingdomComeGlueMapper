"""Carry per-item KCD1 appearance into independent KCD2 clothing assets."""
import copy
import math
import struct
import xml.etree.ElementTree as ET

from clothing_regions import Skin, read_chunks, write_chunks


def apply_armor_colorization(material, armor):
    """Clone material XML and apply the item's four authored HSV zones.

    Armour table hue offsets are turns; Illum's material parameters use degrees.
    Defaults preserve an omitted zone. Separate clones prevent shared materials
    from acquiring the colour of the last item processed.
    """
    result = copy.deepcopy(material)
    mapping = {'color_hue': ('ColorizingHue', 360),
               'color_saturation': ('ColorizingSaturation', 1),
               'brightness': ('ColorizingBrightness', 1)}
    for zone in range(1, 4):
        for source, target, scale in [('hue', 'Hue', 360), ('saturation', 'Saturation', 1),
                                      ('brightness', 'Brightness', 1)]:
            mapping[f'zone{zone}_{source}'] = (f'Mask{target}{zone}', scale)
    values = {}
    for field, (parameter, scale) in mapping.items():
        if armor.get(field) not in (None, ''):
            value = float(armor[field]) * scale
            if not math.isfinite(value):
                raise ValueError('Non-finite armour colour setting')
            values[parameter] = format(value, '.9g')
    for node in result.iter('Material'):
        if node.get('Shader', '').lower() != 'illum':
            continue
        params = node.find('PublicParams')
        if params is None:
            params = ET.SubElement(node, 'PublicParams')
        params.attrib.update(values)
    return result


def rename_clothing_morph(blob, source_name, target_name):
    """Namespace one WH compressed morph without decoding or altering deltas.

    0x802 stores a 48-byte header, zero-terminated name, a table of half-open
    uint16 vertex runs, then three quantized delta bytes per external vertex.
    All records are checked; the delta stream and other chunks remain intact.
    """
    if not source_name.startswith('#') or not target_name.startswith('#') or '\0' in target_name:
        raise ValueError('Morph names must start with # and contain no NUL')
    target = target_name.encode('ascii') + b'\0'
    if len(target) > 128:
        raise ValueError('Morph name is too long')
    chunks = read_chunks(blob)
    candidates = [c for c in chunks if c.kind == 0x2002]
    if len(candidates) != 1 or candidates[0].version != 0x802:
        raise ValueError('Expected one WH 0x802 compiled morph chunk')
    chunk = candidates[0]
    data = chunk.data
    if len(data) < 4:
        raise ValueError('Truncated morph count')
    count = struct.unpack_from('<I', data)[0]
    offset = 4
    output = bytearray(data[:4])
    found = 0
    names = set()
    for _ in range(count):
        if offset + 48 > len(data):
            raise ValueError('Truncated morph header')
        header = bytearray(data[offset:offset+48])
        _, name_length, _, external_count = struct.unpack_from('<4I', header)
        run_count = struct.unpack_from('<I', header, 40)[0]
        offset += 48
        if not 1 <= name_length <= 128 or offset + name_length + run_count*4 > len(data):
            raise ValueError('Invalid morph name or run table')
        name_data = data[offset:offset+name_length]
        if name_data[-1:] != b'\0' or b'\0' in name_data[:-1]:
            raise ValueError('Invalid morph name termination')
        name = name_data[:-1].decode('ascii')
        if name in names or (name == target_name and name != source_name):
            raise ValueError('Duplicate or colliding morph name')
        names.add(name)
        offset += name_length
        payload_start = offset
        previous_end = 0
        covered = 0
        for start, end in struct.iter_unpack('<2H', data[offset:offset+run_count*4]):
            if start < previous_end or end <= start:
                raise ValueError('Invalid compressed morph runs')
            covered += end-start
            previous_end = end
        if covered != external_count:
            raise ValueError('Morph run coverage disagrees with vertex count')
        offset += run_count*4 + external_count*3
        if offset > len(data):
            raise ValueError('Truncated morph deltas')
        if name == source_name:
            found += 1
            name_data = target
            struct.pack_into('<I', header, 4, len(target))
        output.extend(header)
        output.extend(name_data)
        output.extend(data[payload_start:offset])
    if offset != len(data) or found != 1:
        raise ValueError('Trailing morph data or source morph not found exactly once')
    return write_chunks(chunks, {chunk.id: bytes(output)})


def bake_clothing_variant(blob, name):
    """Bake one source garment variant's compressed position deltas at weight 1.

    The selected KCD1 variant is an asset choice, not an animation. Render and
    internal skin positions are both updated, and morphs are removed afterwards
    so the selected variant cannot accidentally be applied for a second time.
    Authored normals/tangents are retained, as the source target stores positions.
    """
    # First validate every compressed record, including the requested name.
    rename_clothing_morph(blob, name, name)
    skin = Skin(blob)
    chunk = skin.one(0x2002)
    data = chunk.data
    offset = 4
    delta = [(0.0, 0.0, 0.0)] * skin.vertex_count
    for _ in range(struct.unpack_from('<I', data)[0]):
        _, length, _, count = struct.unpack_from('<4I', data, offset)
        minimum = struct.unpack_from('<3f', data, offset+16)
        extent = struct.unpack_from('<3f', data, offset+28)
        runs = struct.unpack_from('<I', data, offset+40)[0]
        offset += 48
        target = data[offset:offset+length-1].decode('ascii')
        offset += length
        ranges = list(struct.iter_unpack('<2H', data[offset:offset+runs*4]))
        offset += runs*4
        if target == name:
            cursor = offset
            if not all(math.isfinite(v) for v in (*minimum, *extent)):
                raise ValueError('Non-finite morph quantization')
            for start, end in ranges:
                if end > skin.vertex_count:
                    raise ValueError('Morph vertex outside render stream')
                for index in range(start, end):
                    q = data[cursor:cursor+3]
                    delta[index] = tuple(minimum[k]+extent[k]*q[k]/255 for k in range(3))
                    cursor += 3
        offset += count*3
    positions = skin.positions()
    updated = [tuple(p[k]+d[k] for k in range(3)) for p, d in zip(positions, delta)]
    render = skin.streams[0]
    internal = skin.one(0x2005)
    if internal.version != 0x800 or (len(internal.data)-32) % 64:
        raise ValueError('Unsupported internal skin vertices')
    internal_data = bytearray(internal.data)
    written = {}
    for i, mapped in enumerate(skin.remap):
        address = 32+mapped*64+12
        if address+12 > len(internal_data):
            raise ValueError('Internal vertex outside skin stream')
        old = struct.unpack_from('<3f', internal.data, address)
        if math.dist(old, positions[i]) > 1e-5:
            raise ValueError('Render and internal bind positions disagree')
        if mapped in written and math.dist(written[mapped], updated[i]) > 1e-5:
            raise ValueError('Morph tears a shared internal vertex')
        written[mapped] = updated[i]
        struct.pack_into('<3f', internal_data, address, *updated[i])
    mesh = bytearray(skin.mesh.data)
    bounds = [min(p[k] for p in updated) for k in range(3)] + [max(p[k] for p in updated) for k in range(3)]
    struct.pack_into('<6f', mesh, 108, *bounds)
    replacements = {render.id: render.data[:24]+b''.join(struct.pack('<3f', *p) for p in updated),
                    internal.id: bytes(internal_data), skin.mesh.id: bytes(mesh),
                    chunk.id: struct.pack('<I', 0)}
    # Bone boxes are in model coordinates; retain their vertex membership and
    # recompute bounds after changing the selected garment shape.
    for box in skin.chunks:
        if box.kind != 0x3004:
            continue
        if box.version != 0x801 or len(box.data) < 32:
            raise ValueError('Unsupported bone box')
        count = struct.unpack_from('<I', box.data, 28)[0]
        if len(box.data) != 32+count*2:
            raise ValueError('Invalid bone box indices')
        indices = [v[0] for v in struct.iter_unpack('<H', box.data[32:])]
        if any(v >= len(updated) for v in indices):
            raise ValueError('Bone box vertex outside stream')
        if indices:
            payload = bytearray(box.data)
            bounds = [min(updated[i][k] for i in indices) for k in range(3)] + [max(updated[i][k] for i in indices) for k in range(3)]
            struct.pack_into('<6f', payload, 4, *bounds)
            replacements[box.id] = bytes(payload)
    return write_chunks(skin.chunks, replacements)
