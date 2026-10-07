"""Remove legacy far-object proxies incorrectly imported as ordinary brushes.

The original terrain/uberlods.xml supplies near/far switching relationships.
Until those relationships are converted, these far objects must not appear as
unconditional brush instances beside the individual trees.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import struct
import xml.etree.ElementTree as ET
import zipfile

from building_brushes import is_uberlod_proxy
from compiled_terrain import parse
from upgrade_map import read, xml
from vegetation import verify_target_hlods
from water_volumes import read_water


def filter_hlods(data, document, source_keys):
    """Retain blocks, remap all offsets, and preserve every retained record."""
    before = verify_target_hlods(data)
    output = bytearray(data[:4])
    offsets = {}
    removed = Counter()
    vegetation_before, vegetation_after = hashlib.sha256(), hashlib.sha256()
    cursor = 4
    while cursor < len(data):
        old_offset = cursor
        length = struct.unpack_from('<I', data, cursor)[0]
        cursor += 4
        end = cursor + length
        payload = bytearray()
        while cursor < end:
            kind = struct.unpack_from('<I', data, cursor)[0]
            size = {1:104, 2:64}[kind]
            record = data[cursor:cursor+size]
            key = (record[4:28], record[44:92]) if kind == 1 else None
            if kind == 2:
                vegetation_before.update(record)
            if key in source_keys:
                removed[source_keys[key]] += 1
            else:
                payload.extend(record)
                if kind == 2:
                    vegetation_after.update(record)
            cursor += size
        offsets[old_offset] = (len(output), len(payload)+4, length+4)
        output.extend(struct.pack('<I', len(payload)))
        output.extend(payload)
    for node in document.iter('HLod'):
        if 'DataOffset' not in node.attrib:
            continue
        old = int(node.get('DataOffset'))
        if old not in offsets:
            raise ValueError(f'Unknown HLOD block offset {old}')
        new_offset, new_size, old_size = offsets[old]
        if int(node.get('DataSize')) != old_size:
            raise ValueError('HLOD XML/data size mismatch')
        node.set('DataOffset', str(new_offset))
        node.set('DataSize', str(new_size))
    after = verify_target_hlods(output)
    if before.get(1,0)-after.get(1,0) != sum(removed.values()):
        raise ValueError('Unexpected brush count')
    if vegetation_before.digest() != vegetation_after.digest():
        raise ValueError('Vegetation changed')
    return bytes(output), dict(removed), before, after


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library', type=Path, default=Path(r'D:\SteamLibrary\steamapps\common'))
    p.add_argument('--base-level', required=True)
    p.add_argument('--level', required=True)
    args = p.parse_args()
    if not all(n and n.replace('_','').isalnum() for n in (args.base_level,args.level)):
        p.error('Invalid level name')
    root = args.library/'KCD2Mod/Data/Levels'
    base, dest = root/args.base_level, root/args.level
    if dest.exists():
        p.error('Use a new output level')
    keys = {}
    with zipfile.ZipFile(args.library/'KingdomComeDeliverance/Data/Levels/rataje/level.pak') as z:
        terrain = parse(read(z,'terrain/terrain.dat'))
        def collect(data, offset, size, kind):
            if kind != 1:
                return
            mesh = struct.unpack_from('<H',data,offset+36)[0]
            path = terrain.tables['meshes']['paths'][mesh]
            if is_uberlod_proxy(path):
                key = (data[offset+4:offset+28], data[offset+40:offset+88])
                if key in keys and keys[key] != path:
                    raise ValueError('Ambiguous proxy transform/bounds')
                keys[key] = path
        read_water(terrain, collect)
    with zipfile.ZipFile(base/'level.pak') as z:
        document = ET.fromstring(z.read('terrain/hlods.xml'))
        payload, removed, before, after = filter_hlods(z.read('terrain/hlods.dat'),document,keys)
        if not removed:
            raise ValueError('No legacy proxies found in the base level')
        dest.mkdir()
        shutil.copy2(base/'terrain.pak',dest/'terrain.pak')
        with zipfile.ZipFile(dest/'level.pak','x',zipfile.ZIP_STORED) as out:
            for name in z.namelist():
                value = z.read(name)
                if name == 'terrain/hlods.dat':value = payload
                elif name == 'terrain/hlods.xml':value = xml(document)
                elif name == 'levelinfo.xml':
                    doc = ET.fromstring(value);doc.set('Name','data/levels/'+args.level);value=xml(doc)
                    (dest/'levelinfo.xml').write_bytes(value)
                elif name == 'leveldata.xml':
                    doc = ET.fromstring(value);doc.find('LevelInfo').set('Name',args.level);value=xml(doc)
                out.writestr(name,value)
    report = {'level':args.level,'base_level':args.base_level,'removed':removed,
              'removed_placements':sum(removed.values()),'before':before,'after':after,
              'terrain_preserved':True,'vegetation_bytes_preserved':True,
              'status':'Structural checks passed; runtime visual verification pending',
              'limitation':'Legacy distant stand-ins excluded until near/far switching is converted'}
    (dest/'proxy-removal-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='removed'},indent=2))


if __name__ == '__main__':
    main()
