"""Read-only audit of ladder identities in installed compact-v14 navigation.

Connection presence is not proof of a usable route: this audit does not verify
surface polygons, landing reachability, bed approaches or runtime traversal.
It never generates or changes navigation geometry.
"""
from collections.abc import Mapping
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as E
import zipfile

from campaign_entity_links import guid_value, native_guid
from static_navigation_profiles import BASE, connections, decode
from upgrade_map import read


class ArchiveEntries(Mapping):
    """Read one mesh pair at a time, without loading every variant into RAM."""
    def __init__(self, archive):
        self.archive = archive
        self.names = archive.namelist()

    def __iter__(self):
        return iter(self.names)

    def __len__(self):
        return len(self.names)

    def __getitem__(self, key):
        return read(self.archive, key)


def pairs(entries, settings, version):
    names = {n.lower(): n for n in entries}
    if len(names) != len(entries):
        raise ValueError('Ambiguous navigation archive paths')
    stems = sorted({n[:-4] for n in names if n.endswith(('.hdr', '.nav'))})
    if BASE not in stems:
        raise ValueError('Missing base navigation mesh')
    for stem in stems:
        if any(stem + ext not in names for ext in ('.hdr', '.nav')):
            raise ValueError('Missing navigation pair: ' + stem)
        yield stem, decode(*(entries[names[stem + ext]] for ext in ('.hdr', '.nav')),
                           settings, version)


def entity_index(mission):
    result = {}
    for node in mission.iter('Entity'):
        if not node.get('EntityGuid'):
            continue
        key = guid_value(node.get('EntityGuid'))
        if key in result:
            raise ValueError('Duplicate mission entity GUID: ' + native_guid(key))
        result[key] = node
    return result


def audit(entries, mission, source_ladders, native_entries, native_mission,
          required_names=()):
    """Compare references with actual source/installed/native entity identities.

    Source ladder keys are uint64 KCD1 GUIDs. No coordinate or converted-name
    heuristic is used to associate a source entity with an installed entity.
    Every stored mesh is examined; variants are reported separately from base.
    """
    native_names = {n.lower(): n for n in native_entries}
    try:
        native_mesh = native_entries[native_names[BASE + '.nav']]
    except KeyError as error:
        raise ValueError('Missing native base navigation reference') from error
    if len(native_mesh) < 172:
        raise ValueError('Truncated native compact navigation reference')
    settings, version = native_mesh[:56], struct.unpack_from('<I', native_mesh, 64)[0]
    native_entities = entity_index(native_mission)
    native_ladders = {g for g, e in native_entities.items() if e.get('EntityClass') == 'Ladder'}
    native_categories = set()
    native_references = 0
    # The category contract comes from native connections resolving to Ladder
    # entities, rather than assuming that KCD1's category number still applies.
    native_base = {n: native_entries[n] for n in native_entries if n.lower() in
                   (BASE + '.hdr', BASE + '.nav')}
    for _, cells in pairs(native_base, settings, version):
        for tiles in cells.values():
            for tile in tiles:
                for link in connections(tile):
                    if link['entity'] in native_ladders:
                        native_categories.add(link['category'])
                        native_references += 1
    if not native_categories:
        raise ValueError('Native base has no connections resolving to a Ladder entity')
    installed = entity_index(mission)
    ladders = {g: e for g, e in installed.items() if e.get('EntityClass') == 'Ladder'}
    groups, meshes = {}, []
    for stem, cells in pairs(entries, settings, version):
        references = 0
        for cell, tiles in cells.items():
            for tile in tiles:
                for link in connections(tile):
                    entity, category = link['entity'], link['category']
                    if entity not in source_ladders and entity not in ladders and category not in native_categories:
                        continue
                    references += 1
                    key = (entity, category)
                    if key not in groups:
                        target = installed.get(entity)
                        source = source_ladders.get(entity)
                        groups[key] = dict(entity_guid=native_guid(entity) if entity else None,
                            category=category, category_matches_native=category in native_categories,
                            installed_name=target.get('Name') if target is not None else None,
                            installed_class=target.get('EntityClass') if target is not None else None,
                            resolves_to_installed_ladder=entity in ladders,
                            source_ladder_name=source.get('Name') if source is not None else None,
                            resolves_to_source_ladder=entity in source_ladders,
                            base_references=0, total_references=0, variant_meshes=set(),
                            example=dict(mesh=stem, cell=list(cell),
                                layer=struct.unpack_from('<I', tile, 16)[0],
                                start=link['start'], end=link['end']))
                    row = groups[key]
                    row['total_references'] += 1
                    if stem == BASE:
                        row['base_references'] += 1
                    else:
                        row['variant_meshes'].add(stem)
        meshes.append(dict(mesh=stem, tiles=sum(map(len, cells.values())),
                           ladder_candidate_references=references))
    records = []
    for key in sorted(groups):
        row = groups[key]
        row['variant_meshes'] = sorted(row['variant_meshes'])
        records.append(row)
    coverage = []
    for key, node in sorted(ladders.items(), key=lambda item: (item[1].get('Name', ''), item[0])):
        rows = [r for (g, _), r in groups.items() if g == key and r['category_matches_native']]
        base_count = sum(r['base_references'] for r in rows)
        variants = sorted({m for r in rows for m in r['variant_meshes']})
        coverage.append(dict(name=node.get('Name'), entity_guid=native_guid(key),
            base_references=base_count, variant_meshes=variants,
            reference_status='base_reference_present' if base_count else
                'variant_only' if variants else 'no_native_category_reference'))
    present_names = {row['name'] for row in coverage}
    return dict(native_ladder_categories=sorted(native_categories),
        native_base_ladder_references=native_references, mesh_pairs=len(meshes),
        meshes=meshes, installed_ladders=coverage, references=records,
        unresolved_or_wrong_class_references=[r for r in records if not r['resolves_to_installed_ladder']],
        non_native_category_references=[r for r in records if not r['category_matches_native']],
        requested_ladders=[row for row in coverage if row['name'] in required_names],
        missing_requested_entities=sorted(set(required_names) - present_names),
        polygon_connectivity_verified=False, landing_and_bed_approaches_verified=False,
        runtime_verified=False,
        limitation='Stored variants need their own activation; a matching link does not prove a usable NPC route.')


def inspect(package, source, target, *, level='kcd1_travel', source_level='rataje',
            required_names=()):
    package, source, target = map(Path, (package, source, target))
    with zipfile.ZipFile(package/'Data/Levels'/level/'level.pak') as converted, \
         zipfile.ZipFile(source/'Data/Levels'/source_level/'level.pak') as original, \
         zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as native_level, \
         zipfile.ZipFile(package/'Data/Levels'/level/'recast.pak') as navigation, \
         zipfile.ZipFile(target/'Data/Levels/trosecko/recast.pak') as native_navigation:
        from chairs import inventory
        entities, _, _ = inventory(original, 'Ladder')
        source_ladders = {int(e.get('EntityGuid'), 16): e for e in entities.values()
                          if e.get('EntityClass') == 'Ladder'}
        report = audit(ArchiveEntries(navigation), E.fromstring(read(converted, 'objects_mission0.xml')),
            source_ladders, ArchiveEntries(native_navigation),
            E.fromstring(read(native_level, 'objects_mission0.xml')), required_names)
    return dict(report, package=str(package), source=str(source), target=str(target),
                level=level, source_level=source_level)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'source', 'target'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--level', default='kcd1_travel')
    parser.add_argument('--source-level', default='rataje')
    parser.add_argument('--require-ladder', action='append', default=[], dest='required_names',
                        help='Report this exact installed Ladder entity name separately; repeatable.')
    parser.add_argument('--output', type=Path, help='Write JSON here instead of stdout.')
    args = vars(parser.parse_args())
    output = args.pop('output')
    report = json.dumps(inspect(**args), indent=2)
    if output:
        output.write_text(report + '\n', encoding='utf-8')
    else:
        print(report)


if __name__ == '__main__':
    main()
