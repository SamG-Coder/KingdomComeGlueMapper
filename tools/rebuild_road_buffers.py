"""Rebuild imported road buffers from source using the corrected KCD2 alignment."""
import argparse
import json
from pathlib import Path
import re
import struct
import zipfile

from build_road_material_probe import write_level_copy
from compiled_terrain import parse
from landscape_surfaces import convert_surface
from upgrade_map import read
from water_volumes import read_water


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path('D:/SteamLibrary/steamapps/common'))
    parser.add_argument('--base-level', default='kcd1_world_v37')
    parser.add_argument('--level', default='kcd1_world_v42')
    parser.add_argument('--surface-level', default='kcd1_world_v33')
    args = parser.parse_args()
    if not all(re.fullmatch(r'[A-Za-z0-9_]+', n) for n in (args.base_level, args.level, args.surface_level)):
        parser.error('Invalid level name')
    root = args.library/'KCD2Mod/Data/Levels'
    base, destination = root/args.base_level, root/args.level
    if destination.exists():
        parser.error('Output exists; choose a new level name')
    records = json.loads((root/args.surface_level/'landscape-surfaces-report.json').read_text(encoding='utf-8'))['records']
    with zipfile.ZipFile(args.library/'KingdomComeDeliverance/Data/Levels/rataje/level.pak') as z:
        source = read(z, 'terrain/terrain.dat')
    with zipfile.ZipFile(base/'terrain.pak') as z:
        terrain = parse(z.read('terrain/terrain.dat'))
    surfaces = []
    def visit(data, offset, size, kind):
        if kind in (7, 11):
            surfaces.append((offset, size, kind))
    read_water(terrain, visit)
    if len(surfaces) != len(records):
        raise ValueError('Surface manifest count mismatch')
    output = bytearray(terrain.data)
    changed = roads = 0
    for (offset, size, kind), record in zip(surfaces, records):
        if kind != record['kind'] or list(struct.unpack_from('<6f', terrain.data, offset+4)) != record['bbox']:
            raise ValueError('Surface order/bounds do not match manifest')
        if kind != 11:
            continue
        start, source_size = record['offset'], record['size']
        material = struct.unpack_from('<i', terrain.data, offset+108)[0]
        rebuilt = convert_surface(source[start:start+source_size], material)
        if len(rebuilt) != size:
            raise ValueError('Road size changed')
        old = terrain.data[offset:offset+size]
        v, i, t, p, s = struct.unpack_from('<5I', old, 84)
        index_end = 112+v*12+i*2
        tangent_start = (index_end+3)&~3
        if old[:index_end] != rebuilt[:index_end]:
            raise ValueError('Road header, vertices or indices changed')
        for tangent in struct.iter_unpack('<8h', rebuilt[tangent_start:tangent_start+t*16]):
            if tangent[3] not in (-32767, 32767) or tangent[7] != tangent[3]:
                raise ValueError('Invalid aligned tangent handedness')
        output[offset:offset+size] = rebuilt
        changed += rebuilt != old
        roads += 1
    parse(bytes(output))
    write_level_copy(base, destination, output)
    result = dict(level=args.level, base_level=args.base_level, roads=roads, changed_roads=changed,
                  preserved='Terrain, materials, decals, road headers, vertex positions and UVs, indices',
                  corrected='KCD2 tangent array begins at a four-byte boundary after 16-bit indices',
                  status='Binary validation passed; visual verification pending')
    (destination/'road-buffer-report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
