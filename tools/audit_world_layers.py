"""Read-only inventory of editor layers and compiled import coverage.

Editor object counts are not directly comparable to compiled records: prefabs,
state variants and merged batches change cardinality. Coverage is established
separately against the installed compiled map, not inferred from layer names.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile

from building_brushes import is_uberlod_proxy
from compiled_terrain import parse
from entity_visuals import collect_visuals, initial_layer, VISUAL_CLASSES
from upgrade_map import read
from vegetation import verify_target_hlods
from water_volumes import read_water


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library', type=Path, default=Path('D:/SteamLibrary/steamapps/common'))
    p.add_argument('--editor-archive', type=Path, required=True)
    p.add_argument('--level', default='kcd1_world_v32')
    p.add_argument('--output', type=Path, default=Path('reports/world-layer-audit.json'))
    args = p.parse_args()
    report = {'target_level': args.level, 'editor_archive': str(args.editor_archive),
              'limitations': ['Editor archive and installed retail map may differ in revision.',
                              'Record presence does not prove shader, collision or gameplay fidelity.',
                              'Brush correspondence uses exact bounds/transform multisets; mesh identity is not compared.',
                              'Editor objects, compiled records and merged instances have different cardinalities.']}
    editor_layers = []
    types, classes = Counter(), Counter()
    errors = []
    with zipfile.ZipFile(args.editor_archive) as z:
        for name in z.namelist():
            if not name.lower().startswith('data/levels/rataje/layers/') or not name.lower().endswith('.lyr'):
                continue
            try:
                doc = ET.fromstring(read(z, name))
            except (ET.ParseError, UnicodeError) as error:
                errors.append({'path': name, 'error': str(error)})
                continue
            layer = doc.find('Layer')
            counts, entity_classes = Counter(), Counter()
            for o in doc.iter('Object'):
                counts[o.get('Type', '(unspecified)')] += 1
                if o.get('EntityClass'):
                    entity_classes[o.get('EntityClass')] += 1
            types.update(counts)
            classes.update(entity_classes)
            editor_layers.append({'path': name.removeprefix('Data/Levels/rataje/Layers/'),
                                  'attributes': layer.attrib if layer is not None else {},
                                  'object_types': dict(counts), 'entity_classes': dict(entity_classes),
                                  'object_count': sum(counts.values())})
    report['editor'] = {'layer_files_parsed': len(editor_layers), 'parse_errors': errors,
                        'object_types': dict(types.most_common()), 'entity_classes': dict(classes.most_common()),
                        'layers': editor_layers}
    print(f'Parsed {len(editor_layers)} editor layers; errors={len(errors)}', flush=True)

    target_root = args.library/'KCD2Mod/Data/Levels'/args.level
    with zipfile.ZipFile(target_root/'terrain.pak') as z:
        target = parse(z.read('terrain/terrain.dat'))
        _, target_counts = read_water(target)
        target_terrain_members = set(z.namelist())
    brush_keys = Counter()
    with zipfile.ZipFile(target_root/'level.pak') as z:
        hlods = z.read('terrain/hlods.dat')
        hlod_counts = verify_target_hlods(hlods)
        cursor = 4
        while cursor < len(hlods):
            end = cursor + 4 + struct.unpack_from('<I', hlods, cursor)[0]
            cursor += 4
            while cursor < end:
                kind = struct.unpack_from('<I', hlods, cursor)[0]
                if kind == 1:
                    brush_keys[(hlods[cursor+4:cursor+28], hlods[cursor+44:cursor+92])] += 1
                cursor += 104 if kind == 1 else 64
        target_objects = ET.fromstring(z.read('objects_mission0.xml'))
        target_entities = list(target_objects.iter('Entity'))
        target_ids = {e.get('Name', '').removeprefix('glue_item_') for e in target_entities if e.get('Name', '').startswith('glue_item_')}
        target_members = set(z.namelist()) | target_terrain_members
        report['target_entities'] = dict(Counter(e.get('EntityClass', '') for e in target_entities))
        report['target_mission_object_types'] = dict(Counter(o.get('Type', '') for o in target_objects.iter('Object')))
    with zipfile.ZipFile(args.library/'KingdomComeDeliverance/Data/Levels/rataje/level.pak') as z:
        source = parse(read(z, 'terrain/terrain.dat'))
        leveldata = ET.fromstring(read(z, 'leveldata.xml'))
        layer_names = {int(e.get('Id')): e.get('Name', '') for e in leveldata.find('Layers')}
        layer_counts = defaultdict(Counter)
        brush_status = Counter()
        brush_missing_paths = Counter()
        def visit(d, o, n, kind):
            layer = struct.unpack_from('<H', d, o+28)[0]
            layer_counts[layer][str(kind)] += 1
            if kind != 1:
                return
            key = (d[o+4:o+28], d[o+40:o+88])
            mesh = struct.unpack_from('<H', d, o+36)[0]
            path = source.tables['meshes']['paths'][mesh].replace('\\', '/').lower()
            if brush_keys[key]:
                status = 'matching_target_placement'
                brush_keys[key] -= 1
            elif is_uberlod_proxy(path):
                status = 'excluded_world_proxy'
            elif layer and not initial_layer(layer_names.get(layer, '')):
                status = 'excluded_non_initial_layer'
            elif path.startswith('%level%/'):
                status = 'missing_level_local_geometry'
            else:
                status = 'unmatched_initial_or_shared_brush'
            brush_status[status] += 1
            layer_counts[layer][status] += 1
            if status in ('missing_level_local_geometry', 'unmatched_initial_or_shared_brush'):
                brush_missing_paths[path] += 1
        _, source_counts = read_water(source, visit)
        report['compiled_objects'] = {'source': source_counts, 'target_terrain': target_counts, 'target_hlod': hlod_counts,
                                      'brush_placement_comparison': dict(brush_status),
                                      'unmatched_target_brushes': sum(brush_keys.values()),
                                      'missing_brush_paths': dict(brush_missing_paths.most_common()),
                                      'layers': [{'id': i, 'name': layer_names.get(i, '(shared)' if i == 0 else '(unknown)'),
                                                  'counts': dict(c)} for i,c in sorted(layer_counts.items())]}
        report['compiled_layer_registry'] = [{'id': i, 'name': n} for i,n in layer_names.items()]
        unique_entities, duplicates = {}, 0
        entity_layers = []
        mission_types = Counter()
        for name in z.namelist():
            main = name.lower() == 'objects_mission0.xml'
            if not main and not (name.lower().startswith('layers/') and name.lower().endswith('.xml')):
                continue
            doc = ET.fromstring(read(z, name))
            counts = Counter()
            for entity in doc.iter('Entity'):
                key = entity.get('EntityId') or entity.get('EntityGuid') or (name, len(unique_entities))
                counts[entity.get('EntityClass', '')] += 1
                if key in unique_entities:
                    duplicates += 1
                    continue
                unique_entities[key] = {'class': entity.get('EntityClass', ''), 'layer': entity.get('Layer', ''),
                                        'name': entity.get('Name', ''), 'main': main}
            if main:
                mission_types.update(o.get('Type', '(unspecified)') for o in doc.iter('Object'))
            entity_layers.append({'file': name, 'classes': dict(counts), 'entities': sum(counts.values())})
        entity_classes = defaultdict(Counter)
        missing_visuals = []
        for key, entity in unique_entities.items():
            cls = entity['class']
            entity_classes[cls]['source_unique'] += 1
            if key in target_ids:
                entity_classes[cls]['imported_visual'] += 1
            elif cls not in VISUAL_CLASSES:
                entity_classes[cls]['outside_visual_importer_classes'] += 1
            elif not (initial_layer(entity['layer']) or (entity['main'] and not entity['layer'])):
                entity_classes[cls]['non_initial_state'] += 1
            else:
                entity_classes[cls]['not_imported_initial_visual_candidate'] += 1
                missing_visuals.append(dict(id=key, **entity))
        report['entities'] = {'unique_source': len(unique_entities), 'duplicate_occurrences': duplicates,
                              'imported_visual_ids': len(target_ids), 'classes': {k:dict(v) for k,v in sorted(entity_classes.items())},
                              'layers': entity_layers, 'missing_initial_visual_candidates': missing_visuals,
                              'mission_non_entity_types': dict(mission_types)}
        visuals, excluded = collect_visuals(z, args.library)
        report['entities']['visual_resolver'] = {
            'resolved': len(visuals), 'excluded_occurrences': excluded,
            'resolved_not_packaged': [{k:v for k,v in record.items() if k != 'entity'}
                                      for record in visuals if record['source_id'] not in target_ids]}
        def category(name):
            parts = name.replace('\\', '/').split('/')
            if len(parts) == 1:
                return '(root)/' + parts[0]
            if parts[0] == 'terrain' and len(parts) == 2:
                return 'terrain/' + parts[1]
            return '/'.join(parts[:2]) if len(parts) > 2 else parts[0]+'/*'
        source_members = Counter(category(n) for n in z.namelist())
        target_categories = Counter(category(n) for n in target_members)
        report['package_categories'] = [{'category': name, 'source_members': count,
                                         'target_members': target_categories.get(name, 0)}
                                        for name,count in sorted(source_members.items())]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k in ('target_entities', 'target_mission_object_types')}, indent=2))
    print('Object types:', dict(types.most_common()))
    print('Brush status:', dict(brush_status))
    print(f'Wrote {args.output}', flush=True)


if __name__ == '__main__':
    main()
