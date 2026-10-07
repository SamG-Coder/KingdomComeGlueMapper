"""Build a reversible test that removes surface materials from named water volumes.

This is a targeted rendering workaround, not a geometry or physics conversion.
All other records and inherited asset references remain unchanged.
"""
import argparse
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile

from compiled_terrain import parse
from upgrade_map import xml
from water_volumes import read_water


def suppress_surfaces(terrain, identifiers):
    if terrain.version != 29 or not identifiers:
        raise ValueError("Expected target terrain and explicit volume IDs")
    records, _ = read_water(terrain)
    found = {struct.unpack_from('<Q', terrain.data, r['offset'] + 48)[0] for r in records}
    if not identifiers <= found:
        raise ValueError("Requested volume ID is not present")
    output = bytearray(terrain.data)
    changes = []
    for r in records:
        identifier = struct.unpack_from('<Q', terrain.data, r['offset'] + 48)[0]
        if identifier in identifiers:
            struct.pack_into('<i', output, r['offset'] + 56, -1)
            changes.append({'id':hex(identifier), 'bbox':r['bbox'], 'previous_material':r['material']})
    expected = {r['offset'] + 56 + i for r in records
                if struct.unpack_from('<Q', terrain.data, r['offset'] + 48)[0] in identifiers for i in range(4)}
    if any(a != b and i not in expected for i, (a,b) in enumerate(zip(output, terrain.data))):
        raise ValueError("Non-material data changed")
    return bytes(output), changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, default=Path(r'D:\SteamLibrary\steamapps\common\KCD2Mod'))
    parser.add_argument('--base-level', default='kcd1_streams_v21')
    parser.add_argument('--level', default='kcd1_water_v22')
    parser.add_argument('--suppress-surface', type=lambda s:int(s,0), action='append', required=True)
    args = parser.parse_args()
    if not all(n and n.replace('_','').isalnum() for n in (args.base_level,args.level)):
        parser.error('Invalid level name')
    base=args.tools/'Data/Levels'/args.base_level
    dest=args.tools/'Data/Levels'/args.level
    if dest.exists():parser.error('Output exists; choose a new level name')
    with zipfile.ZipFile(base/'terrain.pak') as archive:
        original=parse(archive.read('terrain/terrain.dat'))
        terrain,changes=suppress_surfaces(original,set(args.suppress_surface))
        if read_water(parse(terrain))[1]!=read_water(original)[1]:raise ValueError('Object counts changed')
        dest.mkdir(parents=True)
        with zipfile.ZipFile(dest/'terrain.pak','x',zipfile.ZIP_STORED) as out:
            for n in archive.namelist():out.writestr(n,terrain if n=='terrain/terrain.dat' else archive.read(n))
    with zipfile.ZipFile(base/'level.pak') as archive, zipfile.ZipFile(dest/'level.pak','x',zipfile.ZIP_STORED) as out:
        for n in archive.namelist():
            b=archive.read(n)
            if n=='levelinfo.xml':
                d=ET.fromstring(b);d.set('Name','data/levels/'+args.level);b=xml(d);(dest/n).write_bytes(b)
            elif n=='leveldata.xml':
                d=ET.fromstring(b);d.find('LevelInfo').set('Name',args.level);b=xml(d)
            out.writestr(n,b)
    report={'level':args.level,'base_level':args.base_level,'surface_workarounds':changes,
            'status':'Only material indices changed; visual and physics behavior require runtime validation',
            'limitation':'Targeted workaround for a visible legacy plane; original rendering behavior not reconstructed'}
    (dest/'water-surface-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
