"""Compile source-authored near/far scenery into KCD2 HLOD parent references.

ProxyIndex is an index in the parent's record payload, not a mesh-table index.
No scenery placement is discarded and no world proxy is an ordinary near brush.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
import struct
import xml.etree.ElementTree as ET

from building_brushes import is_uberlod_proxy
from water_volumes import read_water
from vegetation import verify_target_hlods


def records(data, offset, size):
    end = offset + size
    if offset < 4 or end > len(data) or size < 4:
        raise ValueError('HLOD block outside payload')
    if struct.unpack_from('<I', data, offset)[0] != size - 4:
        raise ValueError('HLOD XML/block length mismatch')
    cursor = offset + 4
    while cursor < end:
        kind = struct.unpack_from('<I', data, cursor)[0]
        length = {1: 104, 2: 64}.get(kind)
        if length is None or cursor + length > end:
            raise ValueError('Invalid HLOD record')
        yield cursor, data[cursor:cursor + length]
        cursor += length


def read_near(data, document):
    """Every block must have exactly one owner before restructuring it."""
    expected = verify_target_hlods(data)
    seen = set()
    result = []
    for node in document.iter('HLod'):
        if int(node.get('ProxyIndex', '-1')) != -1:
            raise ValueError('Expected an unconverted near-only HLOD tree')
        offset, size = int(node.get('DataOffset')), int(node.get('DataSize'))
        if offset in seen:
            raise ValueError('HLOD block has multiple owners')
        seen.add(offset)
        result.extend(records(data, offset, size))
    cursor = 4
    while cursor < len(data):
        if cursor not in seen:
            raise ValueError('Unowned HLOD block')
        cursor += 4 + struct.unpack_from('<I', data, cursor)[0]
    if Counter(struct.unpack_from('<I', r)[0] for _, r in result) != expected:
        raise ValueError('HLOD record count mismatch')
    return result


def _vector(text):
    value = tuple(map(float, text.split(',')))
    if len(value) != 3 or not all(map(math.isfinite, value)):
        raise ValueError('Invalid source HLOD vector')
    return value


def source_groups(terrain, document, layers):
    """Resolve exact far-object geometry and retain source state ownership."""
    by_position = defaultdict(list)
    def collect(data, offset, size, kind):
        if kind != 1: return
        mesh = struct.unpack_from('<H', data, offset + 36)[0]
        name = terrain.tables['meshes']['paths'][mesh].replace('\\', '/').lower()
        if not is_uberlod_proxy(name): return
        matrix = struct.unpack_from('<12f', data, offset + 40)
        position = (matrix[3], matrix[7], matrix[11])
        by_position[tuple(round(x) for x in position)].append((offset, name, position))
    read_water(terrain, collect)
    result = []
    for definition in document:
        far, near, switch = (definition.find(k) for k in ('FarObject', 'NearObject', 'SwitchShape'))
        if far is None or near is None or switch is None:
            raise ValueError('Incomplete source HLOD definition')
        position = _vector(far.get('Position'))
        bounds = _vector(far.get('BoundsMin')) + _vector(far.get('BoundsMax'))
        layer = int(far.get('LayerId', '0'))
        matches = []
        for offset, name, original_pos in by_position[tuple(round(x) for x in position)]:
            original_layer = struct.unpack_from('<H', terrain.data, offset + 28)[0]
            original_bounds = struct.unpack_from('<6f', terrain.data, offset + 4)
            if original_layer == layer and max(abs(a-b) for a,b in zip(position+bounds, original_pos+original_bounds)) < .002:
                matches.append((offset, name))
        if len(matches) != 1:
            raise ValueError(f'Non-unique far geometry for source HLOD {definition.get("Id")}: {matches}')
        offset, name = matches[0]
        layer_name = layers.get(layer, '').split('{')[0].lower()
        # The current world exports intact state0 scenery. Other source states
        # remain separate definitions, never overlapping unconditional proxies.
        import re
        state = re.search(r'(?:^|_)state(\d+)(?:_|$)', layer_name)
        active = state is None or state.group(1) == '0'
        if switch.get('ShapeType') != 'Sphere':
            raise ValueError('Unsupported source switching shape')
        points = [tuple(map(float, x.get('Coord').split(','))) for x in definition.findall('Shape/Point')]
        result.append(dict(id=int(definition.get('Id')), path=name, offset=offset, layer=layer,
            layer_name=layer_name, active=active, kind=near.get('MemberType'),
            cell=int(near.get('VegetationCellIndex', '-1')), points=points,
            center=_vector(switch.get('Center')), radius=float(switch.get('Radius'))))
    return result


def contains(points, x, y):
    """Source polygon membership, including its boundary."""
    inside = False
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        cross = (x-ax)*(by-ay) - (y-ay)*(bx-ax)
        if abs(cross) < .00001 and min(ax,bx)-.00001 <= x <= max(ax,bx)+.00001 and min(ay,by)-.00001 <= y <= max(ay,by)+.00001:
            return True
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside


@dataclass
class Node:
    name: str
    kind: str = 'Cluster'
    payload: list = field(default_factory=list)
    children: list = field(default_factory=list)
    proxy: int = -1
    center: tuple = None
    radius: float = None


def _bounds(payload):
    boxes = [struct.unpack_from('<6f', b, 4) for _, b in payload]
    if not boxes: raise ValueError('Cannot bound an empty scenery node')
    if any(not all(map(math.isfinite, b)) or any(b[i] > b[i+3] for i in range(3)) for b in boxes):
        raise ValueError('Invalid scenery bounds')
    low = [min(b[i] for b in boxes) for i in range(3)]
    high = [max(b[i+3] for b in boxes) for i in range(3)]
    center = tuple((a+b)/2 for a,b in zip(low,high))
    return center, max(1.0, math.dist(low, high)/2)


def _spatial_parent(name, leaves, kind='Cluster'):
    """A bounded 4x4 grouping avoids thousands of siblings at the world root."""
    buckets = defaultdict(list)
    for leaf in leaves:
        buckets[(math.floor(leaf.center[0]/512), math.floor(leaf.center[1]/512))].append(leaf)
    top = Node(name, kind, center=(2048,2048,128), radius=8000)
    for (x,y), children in sorted(buckets.items()):
        branch = Node(f'{name}_{x}_{y}', kind, center=(x*512+256,y*512+256,128), radius=8000)
        for leaf in children:
            # A far record belongs to this parent; only its own near child
            # points at it. Other detailed records are never used as proxies.
            if hasattr(leaf, 'far_record'):
                leaf.proxy = len(branch.payload)
                branch.payload.append((None, leaf.far_record))
            branch.children.append(leaf)
        # Parent sphere encloses child switching spheres, including tall terrain.
        branch.radius = max(math.dist(branch.center, c.center)+c.radius for c in children)
        top.children.append(branch)
    return top


def compile_hierarchy(near_records, groups, far_records):
    vegetation = defaultdict(list)
    brushes = []
    for identity, record in near_records:
        if struct.unpack_from('<I', record)[0] == 2:
            x,y = struct.unpack_from('<2f', record,44)
            vegetation[(math.floor(x/64),math.floor(y/64))].append((identity,record))
        else: brushes.append((identity,record))
    active = [g for g in groups if g['active']]
    cells = {}
    for group in active:
        if group['kind'] == 'UberlodVegetation':
            key = (group['cell'] % 64, group['cell'] // 64)
            if key in cells: raise ValueError('Duplicate source vegetation cell')
            cells[key] = group
    leaves = []; used = set()
    for key, payload in sorted(vegetation.items()):
        center,radius = _bounds(payload)
        leaf = Node('vegetation_%d_%d' % key, 'Vegetation', payload, center=center,radius=radius)
        group = cells.get(key)
        if group:
            leaf.center = group['center']
            # Radius is a switching sphere in KCD1. Expand it if an imported
            # near object extends past it, rather than clipping the placement.
            leaf.radius = max(group['radius'], math.dist(leaf.center,center)+radius)
            leaf.far_record = far_records[group['id']]
            used.add(group['id'])
        leaves.append(leaf)
    building_groups = [g for g in active if g['kind'] == 'UberlodBrushes']
    spatial = defaultdict(list)
    for group in building_groups:
        points = group['points']
        if len(points) < 3: raise ValueError('Building proxy has no ownership polygon')
        group['area'] = abs(sum(a*d-c*b for (a,b),(c,d) in zip(points,points[1:]+points[:1])))/2
        for x in range(math.floor(min(p[0] for p in points)/64),math.floor(max(p[0] for p in points)/64)+1):
            for y in range(math.floor(min(p[1] for p in points)/64),math.floor(max(p[1] for p in points)/64)+1):
                spatial[(x,y)].append(group)
    assigned = defaultdict(list); remainder = defaultdict(list)
    for identity,record in brushes:
        matrix = struct.unpack_from('<12f',record,44);x,y=matrix[3],matrix[7]
        key = (math.floor(x/64),math.floor(y/64))
        candidates = [g for g in spatial[key] if contains(g['points'],x,y)]
        if candidates:
            # The source's smallest enclosing polygon owns nested town pieces.
            selected = min(candidates, key=lambda g:(g['area'],g['id']))
            assigned[selected['id']].append((identity,record))
        else: remainder[key].append((identity,record))
    buildings = []
    for group in building_groups:
        payload = assigned[group['id']]
        if not payload: continue
        center,radius = _bounds(payload)
        leaf = Node('source_buildings_'+str(group['id']), payload=payload,center=group['center'],
                    radius=max(group['radius'],math.dist(group['center'],center)+radius))
        leaf.far_record=far_records[group['id']];used.add(group['id']);buildings.append(leaf)
    for key,payload in sorted(remainder.items()):
        center,radius=_bounds(payload)
        buildings.append(Node('scenery_%d_%d' % key,payload=payload,center=center,radius=radius))
    root = Node('root',center=(2048,2048,128),radius=8000)
    root.children = [_spatial_parent('vegetation',leaves,'Vegetation'),_spatial_parent('scenery',buildings)]
    output=bytearray(struct.pack('<I',2)); emitted=[]
    def emit(node):
        payload=b''.join(r for _,r in node.payload);off=len(output)
        output.extend(struct.pack('<I',len(payload))+payload)
        emitted.extend(i for i,_ in node.payload if i is not None)
        e=ET.Element('HLod',DataOffset=str(off),DataSize=str(len(payload)+4),ProxyIndex=str(node.proxy),
            Type=node.kind,Pos=','.join(format(v,'.9g') for v in node.center),Radius=format(node.radius,'.9g'),
            NearestObserverDistance='0',Name=node.name)
        for child in node.children:e.append(emit(child))
        return e
    document=emit(root)
    if sorted(emitted)!=sorted(i for i,_ in near_records):raise ValueError('Lost or duplicated near placements')
    check_hierarchy(output,document)
    return bytes(output),document,dict(near_records=len(near_records),proxy_count=len(used),
        vegetation_cells=len(leaves),building_proxy_groups=len(assigned)-sum(not v for v in assigned.values()),
        unused_source_groups=[g['id'] for g in groups if g['id'] not in used],
        inactive_state_groups=[g['id'] for g in groups if not g['active']])


def check_hierarchy(data,document):
    """Validate parent-local proxy indices, bounds, sizes and exact ownership."""
    verify_target_hlods(data);seen=set();proxies=0
    def visit(node,parent):
        nonlocal proxies
        off,size=int(node.get('DataOffset')),int(node.get('DataSize'))
        if off in seen:raise ValueError('Duplicate HLOD block ownership')
        seen.add(off);payload=list(records(data,off,size))
        index=int(node.get('ProxyIndex'))
        if index>=0:
            if index>=len(parent) or struct.unpack_from('<I',parent[index][1])[0]!=1:
                raise ValueError('Invalid parent-local scenery proxy index')
            proxies+=1
        _vector(node.get('Pos'))
        radius=float(node.get('Radius'))
        if not math.isfinite(radius) or radius<=0:raise ValueError('Invalid HLOD radius')
        child_indices=[int(c.get('ProxyIndex')) for c in node if int(c.get('ProxyIndex'))>=0]
        if len(set(child_indices))!=len(child_indices):raise ValueError('Proxy belongs to multiple children')
        for child in node:visit(child,payload)
    visit(document,[])
    cursor=4
    while cursor<len(data):
        if cursor not in seen:raise ValueError('Unowned output HLOD block')
        cursor+=4+struct.unpack_from('<I',data,cursor)[0]
    return dict(nodes=len(seen),proxies=proxies)
