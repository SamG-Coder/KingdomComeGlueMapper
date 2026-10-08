"""Experimental named-bone bind-pose replacement for compiled character skins.

Preserves bone order, hierarchy, weights, geometry and morphs. This tests native
rest-pose compatibility; it does not claim to perform animation retargeting.
"""
import argparse
import json
from pathlib import Path
import struct
from clothing_regions import read_chunks, write_chunks


def bones(blob):
    chunks = read_chunks(blob)
    matches = [c for c in chunks if c.kind == 0x2000]
    if len(matches) != 1: raise ValueError('Expected one compiled bone table')
    chunk = matches[0]
    if chunk.version != 0x800 or (len(chunk.data)-32) % 584:
        raise ValueError('Unsupported bone layout')
    records = {}
    for offset in range(32, len(chunk.data), 584):
        name = chunk.data[offset+312:offset+568].split(b'\0')[0].decode('ascii')
        if name in records: raise ValueError('Duplicate bone name: ' + name)
        records[name] = offset
    return chunks, chunk, records


def rebind(blob, target):
    chunks, source, names = bones(blob)
    _, native, target_names = bones(target)
    payload = bytearray(source.data)
    changed = []; unmatched = []
    for name, offset in names.items():
        if name not in target_names:
            unmatched.append(name); continue
        other = target_names[name]
        # CompiledBone v0x800: inverse Matrix34 then absolute Matrix34.
        pose = native.data[other+216:other+312]
        if source.data[offset+216:offset+312] != pose:
            before = struct.unpack_from('<12f', source.data, offset+264)
            after = struct.unpack_from('<12f', native.data, other+264)
            changed.append(dict(bone=name, translation_delta=[after[i]-before[i] for i in (3,7,11)]))
        payload[offset+216:offset+312] = pose
    return write_chunks(chunks, {source.id:bytes(payload)}), dict(changed=changed, unmatched=unmatched)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--skeleton', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists(): raise FileExistsError('Choose a new staging directory')
    prepared = {}; report = {}
    for source in a.source.glob('*.skin'):
        prepared[source.name], report[source.name] = rebind(source.read_bytes(), a.skeleton.read_bytes())
    if not prepared: raise ValueError('No skins found')
    a.output.mkdir(parents=True)
    for name, data in prepared.items(): (a.output/name).write_bytes(data)
    (a.output/'bind-pose-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({name:dict(changed=len(r['changed']), unmatched=r['unmatched']) for name,r in report.items()}))
