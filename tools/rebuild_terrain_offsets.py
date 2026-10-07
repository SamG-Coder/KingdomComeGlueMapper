"""Correct legacy sector height origins in a complete imported world."""
import argparse
import json
from pathlib import Path
import re
import struct
import zipfile

from build_road_material_probe import write_level_copy
from compiled_terrain import parse, legacy_height_offset
from upgrade_map import read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path('D:/SteamLibrary/steamapps/common'))
    parser.add_argument('--base-level', default='kcd1_world_v42')
    parser.add_argument('--level', default='kcd1_world_v43')
    args = parser.parse_args()
    if not all(re.fullmatch(r'[A-Za-z0-9_]+', n) for n in (args.base_level, args.level)):
        parser.error('Invalid level name')
    root = args.library/'KCD2Mod/Data/Levels'
    base, destination = root/args.base_level, root/args.level
    if destination.exists():
        parser.error('Output exists; choose a new level name')
    with zipfile.ZipFile(args.library/'KingdomComeDeliverance/Data/Levels/rataje/level.pak') as z:
        source = parse(read(z, 'terrain/terrain.dat'))
    with zipfile.ZipFile(base/'terrain.pak') as z:
        terrain = parse(z.read('terrain/terrain.dat'))
    if len(source.nodes) != len(terrain.nodes):
        raise ValueError('Node count mismatch')
    output = bytearray(terrain.data)
    changes = []
    for old, new in zip(source.nodes, terrain.nodes):
        if old['values'][2:8] != new['values'][2:8] or old['size'] != new['size']:
            raise ValueError('Source/target sector mismatch')
        if not old['size']:
            continue
        value = legacy_height_offset(old['values'][8])
        struct.pack_into('<f', output, new['offset']+28, value)
        if value != new['values'][8]:
            changes.append(dict(x=old['values'][2], y=old['values'][3], depth=old['depth'],
                                before=new['values'][8], after=value))
    checked = parse(bytes(output))
    # Resource tables, sample arrays, palettes and all outdoor records survive.
    expected = bytearray(output)
    for node in terrain.nodes:
        expected[node['offset']+28:node['offset']+32] = terrain.data[node['offset']+28:node['offset']+32]
    if expected != terrain.data or checked.tree_offset != terrain.tree_offset:
        raise ValueError('Unexpected non-offset change')
    write_level_copy(base, destination, output)
    result = dict(level=args.level, base_level=args.base_level, changed_nodes=len(changes),
                  max_lowering_metres=max((c['before']-c['after'] for c in changes), default=0),
                  changes=changes, status='Only terrain origin floats changed; runtime validation pending')
    (destination/'terrain-offset-report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='changes'}, indent=2))


if __name__ == '__main__':
    main()
