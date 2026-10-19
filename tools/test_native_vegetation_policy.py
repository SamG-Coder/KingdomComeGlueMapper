"""Bounded native-vegetation flag/radius experiment; not a setup default."""
import copy
import math
import struct
import xml.etree.ElementTree as ET
from spatial_hlod import records


def native_policy(tree, data):
    common = (1 << 64) - 1
    count = 0
    radii = set()
    for node in tree.iter('HLod'):
        offset, size = int(node.get('DataOffset')), int(node.get('DataSize'))
        vegetation = [r for r in records(data[offset+4:offset+size]) if len(r) == 64]
        for record in vegetation:
            common &= struct.unpack_from('<Q', record, 32)[0]
            count += 1
        if vegetation and node.get('Type') == 'Vegetation' and not len(node):
            radii.add(node.get('Radius'))
    upper = common & 0xffffffff00000000
    if not count or not upper or len(radii) != 1:
        raise ValueError('Native reference lacks a consistent vegetation policy')
    radius = radii.pop()
    if abs(float(radius) - math.sqrt(2)*32) > .001:
        raise ValueError('Native reference is not a 64m leaf grid')
    return upper, radius


def patch(tree, data, upper, radius):
    tree = copy.deepcopy(tree)
    output = bytearray(data)
    changed_flags = changed_cells = 0
    for node in tree.iter('HLod'):
        offset, size = int(node.get('DataOffset')), int(node.get('DataSize'))
        cursor = offset + 4
        cells = set()
        for record in records(data[cursor:offset+size]):
            if len(record) == 64:
                flags = struct.unpack_from('<Q', record, 32)[0]
                changed_flags += flags | upper != flags
                struct.pack_into('<Q', output, cursor+32, flags | upper)
                x, y = struct.unpack_from('<2f', record, 44)
                cells.add((math.floor(x/64), math.floor(y/64)))
            cursor += len(record)
        if cells and node.get('Type') == 'Vegetation' and not len(node):
            if len(cells) != 1:
                raise ValueError('Cannot assign 64m radius to a multi-cell leaf')
            changed_cells += node.get('Radius') != radius
            node.set('Radius', radius)
    # Preserve the existing hierarchy identity and all membership/proxy links.
    return ET.tostring(tree), bytes(output), {'flags_changed': changed_flags,
        'leaf_radii_changed': changed_cells, 'upper_flags_added': hex(upper),
        'leaf_radius': radius}
