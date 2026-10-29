"""Replace an authored stocks placement without deleting neighbouring geometry."""
import copy
import hashlib
import struct
import zlib
import xml.etree.ElementTree as E

from spatial_hlod import records
from upgrade_map import xml


def geometry_signature(blob):
    from clothing_regions import read_chunks
    return hashlib.sha256(b''.join(struct.pack('<HHI', c.kind, c.version, c.id)+c.data
        for c in read_chunks(blob) if c.kind != 0x1014)).digest()


def remove_stocks(data, metadata, plans, meshes, check_model):
    """Keep hierarchy/proxy ownership, updating indices after removal.

    Selection requires the entire source transform AND verified source mesh
    geometry. Coordinate proximity alone is never permission to remove a prop.
    """
    from vegetation import verify_target_hlods
    verify_target_hlods(data)
    root = E.fromstring(metadata)
    spans, payloads, found, removed = [], {}, set(), []
    for node in root.iter('HLod'):
        start, size = (int(node.get(n)) for n in ('DataOffset', 'DataSize'))
        if start < 4 or start+size > len(data) or struct.unpack_from('<I', data, start)[0] != size-4:
            raise ValueError('Stocks HLOD metadata mismatch')
        spans.append((start, start+size))
        items = records(data[start+4:start+size])
        kept, remap = [], {}
        for i, record in enumerate(items):
            candidates = []
            if struct.unpack_from('<I', record)[0] == 1:
                matrix = struct.unpack_from('<12f', record, 44)
                candidates = [j for j, p in enumerate(plans) if
                    all(abs(a-b) < .0003 for a,b in zip(matrix, p['transform']))]
            match = [j for j in candidates if check_model(
                meshes[struct.unpack_from('<H', record, 40)[0]], plans[j]['path'])]
            if len(match) > 1: raise ValueError('Ambiguous source stocks geometry')
            if match:
                j = match[0]
                if j in found: raise ValueError('Duplicate source stocks geometry')
                found.add(j)
                removed.append(dict(source=plans[j], group=node.get('Name'), index=i))
            else:
                remap[i] = len(kept); kept.append(record)
        for child in node.findall('HLod'):
            old = int(child.get('ProxyIndex', '-1'))
            if old >= 0:
                if old not in remap: raise ValueError('Stocks removal would orphan a streaming proxy')
                child.set('ProxyIndex', str(remap[old]))
        payloads[id(node)] = b''.join(kept)
    spans.sort()
    if not spans or spans[0][0] != 4 or spans[-1][1] != len(data) or any(a[1] != b[0] for a,b in zip(spans,spans[1:])):
        raise ValueError('Uncovered or overlapping stocks HLOD payloads')
    if len(found) != len(plans): raise ValueError('Missing verified source stocks geometry')
    output = bytearray(struct.pack('<I', 2))
    for node in root.iter('HLod'):
        payload = payloads[id(node)]
        node.set('DataOffset', str(len(output))); node.set('DataSize', str(len(payload)+4))
        output.extend(struct.pack('<I', len(payload))+payload)
    root.set('Hash', str(zlib.crc32(output)))
    verify_target_hlods(output)
    return bytes(output), xml(root), removed
