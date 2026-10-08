"""Add a bounded road/decal diagnostic to an existing imported world."""
import argparse
from game_paths import GameLibrary
from collections import Counter
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from compiled_terrain import parse
from entity_visuals import initial_layer
from landscape_surfaces import append_surfaces, convert_surface
from static_assets import asset_pack
from upgrade_map import read, xml
from water_volumes import read_water


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=GameLibrary, default=Path('D:/SteamLibrary/steamapps/common'))
    parser.add_argument('--base-level', default='kcd1_world_v30')
    parser.add_argument('--level', default='kcd1_world_v32')
    parser.add_argument('--position', type=float, nargs=3, default=(725.63867, 3441.4238, 64.993584))
    parser.add_argument('--radius', type=float, default=20)
    parser.add_argument('--all', action='store_true', help='Import shared and explicit initial-state roads/decals across the map')
    args = parser.parse_args()
    if not all(n and n.replace('_', '').isalnum() for n in (args.level, args.base_level)):
        parser.error('Invalid level name')
    if args.radius <= 0:
        parser.error('Radius must be positive')
    root = args.library/'KCD2Mod/Data'
    base, destination = (root/'Levels'/n for n in (args.base_level, args.level))
    prefix = 'gluelandscape/' + args.level + '/'
    assets = root/prefix
    if destination.exists() or assets.exists():
        parser.error('Output exists; choose a new level name')
    with ExitStack() as stack:
        archive = stack.enter_context(zipfile.ZipFile(args.library/'KingdomComeDeliverance/Data/Levels/rataje/level.pak'))
        source = parse(read(archive, 'terrain/terrain.dat'))
        layer_names = {int(e.get('Id')): e.get('Name', '') for e in
                       ET.fromstring(read(archive, 'leveldata.xml')).find('Layers')}
        allowed = {0} | {i for i,n in layer_names.items() if initial_layer(n)}
        excluded_layers = Counter()
        selected = []
        def visit(data, offset, size, kind):
            if kind not in (7, 11):
                return
            bbox = struct.unpack_from('<6f', data, offset+4)
            layer = struct.unpack_from('<H', data, offset+28)[0]
            if layer not in allowed:
                excluded_layers[layer_names.get(layer, str(layer))] += 1
                return
            distance = sum(max(bbox[a]-args.position[a], 0, args.position[a]-bbox[a+3])**2 for a in range(3))**0.5
            if args.all or distance <= args.radius:
                material_id = struct.unpack_from('<i', data, offset+(108 if kind == 7 else 104))[0]
                if not 0 <= material_id < len(source.tables['materials']['paths']):
                    raise ValueError('Invalid source surface material')
                selected.append(dict(offset=offset, size=size, kind=kind, bbox=bbox,
                                     material_id=material_id, distance=distance, layer=layer))
        _, source_counts = read_water(source, visit)
        with zipfile.ZipFile(base/'terrain.pak') as z:
            baseline = parse(z.read('terrain/terrain.dat'))
        _, base_counts = read_water(baseline)
        if base_counts.get(7) or base_counts.get(11):
            raise ValueError('Base already contains surfaces; avoid duplicate imports')
        Path('outputs').mkdir(exist_ok=True)
        cache = stack.enter_context(tempfile.TemporaryDirectory(prefix='surfaces-', dir='outputs'))
        _, emitted, material, _ = asset_pack(stack, args.library, prefix, cache)
        materials, mapping = [], {}
        material_failures = {}
        for item in selected:
            identifier = item['material_id']
            if identifier in material_failures:
                continue
            if identifier not in mapping:
                name = source.tables['materials']['paths'][identifier]
                if name:
                    try:
                        target = material(name)
                    except (FileNotFoundError, KeyError, ValueError) as error:
                        material_failures[identifier] = dict(path=name, error=str(error))
                        continue
                    mapping[identifier] = len(baseline.tables['materials']['paths'])+len(materials)
                    materials.append(target)
                else:
                    mapping[identifier] = -1  # retain source material-less roads
            item['material'] = source.tables['materials']['paths'][identifier]
        unavailable = [r for r in selected if r['material_id'] in material_failures]
        selected = [r for r in selected if r['material_id'] not in material_failures]
        records = [convert_surface(source.data[r['offset']:r['offset']+r['size']], mapping[r['material_id']]) for r in selected]
        converted = append_surfaces(baseline, records, materials)
        checked = parse(converted)
        _, checked_counts = read_water(checked)
        expected = Counter(base_counts)
        expected.update(r['kind'] for r in selected)
        if checked_counts != dict(expected):
            raise ValueError('Surface count verification failed')
        # Verify the existing terrain heightfields and surface palettes verbatim.
        old_start = baseline.tables['materials']['offset'] + len(baseline.tables['materials']['paths'])*256
        new_start = checked.tables['materials']['offset'] + len(checked.tables['materials']['paths'])*256
        if baseline.data[old_start:baseline.tree_offset] != checked.data[new_start:checked.tree_offset]:
            raise ValueError('Terrain nodes changed')
        destination.mkdir(parents=True)
        for file in base.iterdir():
            if file.is_file() and file.name not in ('terrain.pak', 'level.pak', 'levelinfo.xml'):
                shutil.copy2(file, destination/file.name)
        with zipfile.ZipFile(base/'terrain.pak') as z, zipfile.ZipFile(destination/'terrain.pak', 'x', zipfile.ZIP_STORED) as out:
            for name in z.namelist():
                out.writestr(name, converted if name == 'terrain/terrain.dat' else z.read(name))
        with zipfile.ZipFile(base/'level.pak') as z, zipfile.ZipFile(destination/'level.pak', 'x', zipfile.ZIP_STORED) as out:
            for name in z.namelist():
                payload = z.read(name)
                if name in ('levelinfo.xml', 'leveldata.xml'):
                    doc = ET.fromstring(payload)
                    if name == 'levelinfo.xml':
                        doc.set('Name', 'data/levels/'+args.level)
                    else:
                        doc.find('LevelInfo').set('Name', args.level)
                    payload = xml(doc)
                    if name == 'levelinfo.xml':
                        (destination/name).write_bytes(payload)
                out.writestr(name, payload)
        for name, payload_path in emitted.items():
            path = (root/name).resolve()
            if not path.is_relative_to(assets.resolve()):
                raise ValueError('Asset escaped namespace')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as out, payload_path.open('rb') as inp:
                shutil.copyfileobj(inp, out)
        report = dict(level=args.level, base_level=args.base_level, position=args.position,
                      radius=args.radius, source_counts=source_counts, target_counts=checked_counts,
                      records=selected, assets=len(emitted),
                      all_initial_surfaces=args.all, excluded_layers=dict(excluded_layers),
                      material_failures=material_failures, unavailable_records=unavailable,
                      status='Binary checks passed; runtime and visual acceptance pending')
        (destination/'landscape-surfaces-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k != 'records'}, indent=2))


if __name__ == '__main__':
    main()
