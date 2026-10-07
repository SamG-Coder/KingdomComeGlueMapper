"""Use installed KCD2 mud-road materials on source road placements, like native water."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import struct
import zipfile

from build_road_material_probe import write_level_copy
from compiled_terrain import parse
from landscape_surfaces import append_surfaces
from water_volumes import read_water


def native_mud_material(source):
    """Explicit mud family only; keep other road types and decals unchanged."""
    prefix = 'materials/terrain/roads/'
    name = source.lower().removeprefix(prefix)
    if source.lower() != prefix+name:
        return None
    if name == 'road_mud_rails_single':
        return prefix+'road_track_4x1'
    if name == 'road_mud_cracks':
        return prefix+'road_soil_b_2m_dry'
    if re.fullmatch(r'road_mud_(02|03|04|06|09|11|12|13)(_tracks)?(_v[23])?(_red)?', name):
        return prefix+('road_soil_b_2m_reddish' if name.endswith('_red') else 'road_country_big')
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path('D:/SteamLibrary/steamapps/common'))
    parser.add_argument('--base-level', default='kcd1_world_v38')
    parser.add_argument('--level', default='kcd1_world_v41')
    parser.add_argument('--surface-level', default='kcd1_world_v33', help='Build containing the source surface manifest')
    args = parser.parse_args()
    if not all(re.fullmatch(r'[A-Za-z0-9_]+', n) for n in (args.base_level, args.level, args.surface_level)):
        parser.error('Invalid level name')
    root = args.library/'KCD2Mod/Data/Levels'
    base, destination = root/args.base_level, root/args.level
    if destination.exists():
        parser.error('Output exists; choose a new level name')
    report = json.loads((root/args.surface_level/'landscape-surfaces-report.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(base/'terrain.pak') as z:
        terrain = parse(z.read('terrain/terrain.dat'))
    surfaces = []
    def visit(data, offset, size, kind):
        if kind in (7, 11):
            surfaces.append((offset, kind, struct.unpack_from('<6f', data, offset+4)))
    read_water(terrain, visit)
    if len(surfaces) != len(report['records']):
        raise ValueError('Surface manifest count does not match target')
    replacements = {}
    road_overrides = []
    counts = Counter()
    for (offset, kind, bbox), source in zip(surfaces, report['records']):
        if kind != source['kind'] or list(bbox) != source['bbox']:
            raise ValueError('Surface manifest ordering/bounds differ from target')
        native = native_mud_material(source['material']) if kind == 11 else None
        if native is None:
            continue
        material_id = struct.unpack_from('<i', terrain.data, offset+108)[0]
        if not 0 <= material_id < len(terrain.tables['materials']['paths']):
            raise ValueError('Invalid target material ID')
        if material_id in replacements and replacements[material_id] != native:
            raise ValueError('Conflicting native material mapping')
        replacements[material_id] = native
        road_overrides.append((offset, native))
        counts[source['material']] += 1
    with zipfile.ZipFile(args.library/'KingdomComeDeliverance2/Data/IPL_GameData.pak') as z:
        installed = {n.replace('\\','/').lower() for n in z.namelist()}
        if any(n+'.mtl' not in installed for n in replacements.values()):
            raise ValueError('Native material is not installed')
    # A road and a projected decal can share an old table entry. Append native
    # entries and patch road pointers only, keeping the decal's original entry.
    native_paths = sorted(set(replacements.values()))
    output = bytearray(append_surfaces(terrain, [], native_paths))
    added_bytes = len(native_paths)*256
    first_material = len(terrain.tables['materials']['paths'])
    for offset, native in road_overrides:
        struct.pack_into('<i', output, offset+added_bytes+108,
                         first_material+native_paths.index(native))
    parse(bytes(output))
    if not counts:
        raise ValueError('No source mud roads found')
    write_level_copy(base, destination, output)
    result = dict(level=args.level, base_level=args.base_level, roads=sum(counts.values()),
                  source_counts=dict(counts), replacements=replacements,
                  status='Native material substitution; runtime/visual verification pending',
                  appearance='Uses KCD2 textures and material tuning; not pixel-identical KCD1 mud')
    (destination/'native-mud-report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
