"""Experimental HLOD rebuild from final placed render-node bounds.

Discards legacy switching radii and spatial containers. Retains detail records
and each authored proxy's membership: a proxy must not represent a new arbitrary
set of objects. Does not rebuild mesh LOD geometry or change global distances.
"""
from collections import Counter, defaultdict
import copy
import hashlib
import math
import struct
import xml.etree.ElementTree as ET

from spatial_hlod import Group, bounds, hierarchy, read_groups, records, serialize


def extract(document, data):
    """Validate existing links and recover indivisible proxy/detail groups."""
    ranges = copy.deepcopy(document)
    for node in ranges.iter('HLod'):
        node.set('ProxyIndex', '-1')
    read_groups(ranges, data)  # validates version, lengths and complete coverage
    groups = []
    def visit(node, proxy=None):
        offset, size = int(node.get('DataOffset')), int(node.get('DataSize'))
        own = records(data[offset + 4:offset + size])
        used = set()
        children = []
        for child in node:
            index = int(child.get('ProxyIndex', '-1'))
            child_proxy = None
            if index >= 0:
                if index >= len(own) or len(own[index]) != 104 or index in used:
                    raise ValueError('Invalid or shared parent proxy index')
                used.add(index)
                child_proxy = own[index]
            children.append((child, child_proxy))
        detail = [r for i, r in enumerate(own) if i not in used]
        if proxy is not None and children:
            # Flattening this would change what the coarser proxy represents.
            raise ValueError('Nested authored proxies require a multilevel geometry builder')
        if detail:
            if node.get('Type') not in ('Vegetation', 'Cluster', 'Layer'):
                raise ValueError('Unsupported detailed HLOD type')
            groups.append((node, detail, proxy))
        elif proxy is not None:
            raise ValueError('Proxy has no detailed representation')
        for child, child_proxy in children:
            visit(child, child_proxy)
    root = document if document.tag == 'HLod' else document.find('HLod')
    if root is None or int(root.get('ProxyIndex', '-1')) != -1:
        raise ValueError('Invalid HLOD root')
    visit(root)
    if not groups:
        raise ValueError('No placed geometry to rebuild')
    return groups


def placed_bounds(items):
    """Sphere enclosing the union of final world-space render-node AABBs."""
    low = [math.inf] * 3
    high = [-math.inf] * 3
    for record in items:
        box = struct.unpack_from('<6f', record, 4)
        if not all(map(math.isfinite, box)) or any(box[a] > box[a + 3] for a in range(3)):
            raise ValueError('Invalid placed-object bounds')
        for axis in range(3):
            low[axis] = min(low[axis], box[axis])
            high[axis] = max(high[axis], box[axis + 3])
    if not all(map(math.isfinite, low + high)):
        raise ValueError('Empty placed-object group')
    center = tuple((a + b) / 2 for a, b in zip(low, high))
    return center, math.dist(low, high) / 2


def container(name, kind, children):
    center, radius = bounds(children)
    return Group(name, kind, center, radius, children=children)


def rebuild(document, data):
    source = extract(document, data)
    brushes = []
    vegetation = defaultdict(lambda: defaultdict(list))
    changes = []
    identities = Counter()
    for index, (node, detail, proxy) in enumerate(source):
        # A proxy may extend beyond its detailed surfaces; include both in the
        # spatial envelope without altering the near/proxy switching membership.
        center, radius = placed_bounds(detail + ([proxy] if proxy is not None else []))
        kind = 'Vegetation' if node.get('Type') == 'Vegetation' else 'Cluster'
        identity = hashlib.sha256(kind.encode() + b''.join(detail) + (proxy or b'')).hexdigest()
        occurrence = identities[identity]
        identities[identity] += 1
        group = Group(f'geometry_{identity}_{occurrence}', kind, center, radius, detail, proxy)
        changes.append(dict(source=node.get('Name'), old_radius=float(node.get('Radius')),
                            radius=radius, center=center, records=len(detail), proxy=proxy is not None))
        if kind == 'Vegetation':
            x, y = (math.floor(center[a] / 64) for a in range(2))
            vegetation[(x // 16, y // 16)][(x // 4, y // 4)].append(group)
        else:
            brushes.append(group)
    children = []
    if brushes:
        # Spatial subdivision derives solely from final geometry. No proxy is
        # invented for these containers; leaf proxies retain their memberships.
        clusters = hierarchy(brushes, name='geometry_brushes', fanout=16)
        children.append(container('Main', 'Layer', [clusters]))
    if vegetation:
        regions = []
        for outer, quarters in sorted(vegetation.items()):
            cells = [container(f'geometry_veg_cell_{q[0]}_{q[1]}', 'Vegetation', items)
                     for q, items in sorted(quarters.items())]
            regions.append(container(f'geometry_veg_region_{outer[0]}_{outer[1]}', 'Vegetation', cells))
        # Preserve the four native vegetation tiers. Bounds at every tier are
        # calculated from children, including their actual height and extent.
        children.append(container('vegetation', 'Vegetation', regions))
    root = container('root', 'Cluster', children)
    output, payload, report = serialize(root)
    restored = extract(output, payload)
    # Compare per-leaf membership too: global count preservation alone could
    # overlook moving detail records under the wrong proxy.
    def membership(groups):
        return Counter((proxy, tuple(detail)) for _, detail, proxy in groups)
    if membership(source) != membership(restored):
        raise ValueError('Rebuilt hierarchy changed proxy/detail membership')
    report.update(detail_records=sum(len(detail) for _, detail, _ in source),
                  rebuilt_leaf_groups=len(source), bounds_source='final_placed_render_nodes',
                  inherited_switching_radii=False, membership_preserved=True,
                  mesh_lods_rebuilt=False, runtime_verified=False, bounds=changes)
    return output, payload, report
