"""Merge converted residents into a staged travel mod, preserving its services.

The output is a separate mod folder for validation, never an in-place edit of a
running game. Quest-controlled source layers are not promoted to the base world.
"""
import argparse
from collections import Counter
import copy
import errno
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import guid_value, native_guid
from character_person import PersonSource, actor_resources, register_person, native_xml
from character_person_ai import FACTION_PATH
from character_person_world import register_person_world
from character_population import placement_person, input_signature
from character_population_factions import register_catalog
from character_texture_cache import TextureCache
from region_travel_tables import managed_patches
from region_travel_locations import merge_missing_strings
from upgrade_map import read, xml


MOD = 'gluemappertravel'
LEVEL = 'kcd1_travel'


def add_people(files, people):
    files = dict(files)
    mission = ET.fromstring(files['objects_mission0.xml'])
    wh = ET.fromstring(files['whdata_0'])
    names = {e.get('Name') for e in mission}
    guids = {e.get('EntityGuid') for e in mission}
    next_id = max(int(e.get('EntityId', '0')) for e in mission) + 1
    def create(name, cls, pos, guid, rotate=None):
        nonlocal next_id
        if name in names or guid in guids: raise ValueError('Population placement collision: ' + name)
        attributes = dict(Name=name, EntityClass=cls, Pos=pos, EntityGuid=guid, EntityId=str(next_id))
        if rotate: attributes['Rotate'] = rotate
        entity = ET.SubElement(mission, 'Entity', **attributes)
        names.add(name); guids.add(guid); next_id += 1
        return entity
    for person in people:
        npc = register_person(create, wh, person)
        # Exported placements are source world positions. No arrival-offset or
        # player-position spawning is applied to the population.
        for attribute in ('Scale',):
            if person['actor'].get(attribute): npc.set(attribute, person['actor'].get(attribute))
    files.update({'objects_mission0.xml': xml(mission), 'whdata_0': xml(wh)})
    return files


def register_faction_locations(resources, tables, level_id=1001):
    """Create location dependencies that weren't referenced by map icons."""
    path = 'Libs/Tables/rpg/location__' + MOD + '.xml'
    root = ET.fromstring(resources[path])
    rows = root.find('locations')
    known = {r.get('location_id') for r in rows}
    source = {r['location_id']: r for r in tables.get('rpg/location')['rows']}
    factions = ET.fromstring(resources[FACTION_PATH])
    wanted = {r.get('LocationId') for r in factions.iter('Faction')
              if r.get('Name', '').startswith('gluemapper_') and r.get('LocationId')}
    for identity in sorted(wanted - known):
        old = source[identity]
        attributes = dict(location_id=identity, location_name=old['location_name'],
                          level_id=str(level_id), ui_type='Hidden')
        if old.get('location_category_id'): attributes['location_category_id'] = old['location_category_id']
        ET.SubElement(rows, 'location', **attributes)
    resources[path] = xml(root)
    return sorted(wanted - known)


def assemble(stage, output, *, allow_partial=False):
    stage, output = Path(stage), Path(output)
    if output.exists(): raise FileExistsError('Use a fresh population package directory')
    report = json.loads((stage / 'population.json').read_text())
    incomplete = [a for a in report['actors'] if a['resident'] and a['status'] not in ('converted', 'existing_preserved')]
    if incomplete and not allow_partial:
        raise ValueError('Resident conversion is incomplete; retry the stage or explicitly select --allow-partial for diagnostics')
    source, target, base = map(Path, (report['source'], report['target'], report['base_package']))
    selected = [a for a in report['actors'] if a['status'] == 'converted']
    if not selected: raise ValueError('No converted population to package')
    textures = TextureCache(stage / 'textures')
    base_main = base / 'Data' / (MOD + '.pak')
    resources, tables = {}, {}
    with zipfile.ZipFile(base_main) as z:
        for name in z.namelist():
            if name.startswith('Libs/Tables/') or name in ('Libs/Storm/storm.xml', 'Libs/Storm/common/base.xml'):
                resources[name] = read(z, name)
    with zipfile.ZipFile(target / 'Data/Tables.pak') as z:
        faction_tree = native_xml(read(z, 'Libs/Tables/rpg/FactionTree.xml'))
    level_path = 'Data/Levels/' + LEVEL + '/level.pak'
    with zipfile.ZipFile(base / level_path) as z:
        world = {n: read(z, n) for n in ('objects_mission0.xml', 'whdata_0', 'tables/ai/scheduler.xml',
                                        'triggerareas.fubar', 'waitinglinks.xml')}
    people, character_archives, shared_members, localizations = [], [], set(), []
    with PersonSource(source, report['source_level']) as catalog:
        names = Counter(a.get('Name') for a in catalog.actors)
        duplicates = {name for name, count in names.items() if count > 1}
        resources.pop('Libs/Tables/rpg/FactionTree__' + MOD + '.xml', None)
        resources.update(register_catalog(catalog.tables, faction_tree))
        added_locations = register_faction_locations(resources, catalog.tables)
        for ordinal, record in enumerate(selected, 1):
            person = placement_person(catalog.resolve(entity_guid=record['source_guid']), duplicates)
            path = stage / record['archive']
            if not path.resolve().is_relative_to(stage.resolve()): raise ValueError('Invalid population archive path')
            with zipfile.ZipFile(path) as z:
                character = json.loads(z.read('population-character.json'))
                if character['signature'] != input_signature(person): raise ValueError('Stale population appearance')
                for name in character['shared_textures']:
                    if not textures.contains(name): raise ValueError('Missing shared texture: ' + name)
                    shared_members.add(name)
                for name in z.namelist():
                    if name.startswith('Libs/Tables/'):
                        if name in resources: raise ValueError('Population table namespace collision')
                        resources[name] = z.read(name)
                    elif name.startswith('Localization/'):
                        localizations.append(z.read(name))
                resources = actor_resources(target, person, character['character'], record['namespace'],
                    resources=resources, native_cache=tables)
            people.append(person); character_archives.append(path)
            if ordinal % 100 == 0:
                print(json.dumps(dict(stage='registrations', completed=ordinal, total=len(selected))), flush=True)
        world = add_people(world, people)
        world, registrations = register_person_world(world, people, source, target)
    resources = managed_patches(resources, MOD)
    resources['Mods/' + MOD + '/Data/Levels/' + LEVEL + '/tables/ai/scheduler.xml'] = world['tables/ai/scheduler.xml']
    # All runtime files and registrations have now passed preparation. Only now
    # publish a separate package; all immutable base assets are shared locally.
    output.mkdir(parents=True)
    replaced = {level_path.lower(), ('Data/' + MOD + '.pak').lower(), 'localization/english_xml.pak'}
    for path in base.rglob('*'):
        if not path.is_file(): continue
        relative = path.relative_to(base)
        if relative.as_posix().lower() in replaced: continue
        destination = output / relative; destination.parent.mkdir(parents=True, exist_ok=True)
        try: os.link(path, destination)
        except OSError as error:
            if error.errno != errno.EXDEV and getattr(error, 'winerror', None) != 17: raise
            shutil.copy2(path, destination)
    destination = output / level_path; destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(base / level_path) as src, zipfile.ZipFile(destination, 'x', zipfile.ZIP_STORED) as dst:
        seen = set()
        for info in src.infolist():
            key = info.filename.lower(); seen.add(key)
            dst.writestr(info, world.get(key, read(src, info.filename)))
        for name, data in world.items():
            if name.lower() not in seen: dst.writestr(name, data)
    destination = output / 'Data' / (MOD + '.pak')
    with zipfile.ZipFile(base_main) as src, zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=1) as dst:
        seen = set()
        for info in src.infolist():
            if info.filename in resources: continue
            with src.open(info) as inp, dst.open(info.filename, 'w', force_zip64=True) as out:
                shutil.copyfileobj(inp, out)
            seen.add(info.filename.lower())
        def emit(name, data):
            if name.lower() in seen: raise ValueError('Duplicate population package resource: ' + name)
            dst.writestr(name, data); seen.add(name.lower())
        for name, data in resources.items(): emit(name, data)
        for path in character_archives:
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    if name.startswith(('Libs/Tables/', 'Localization/')) or name == 'population-character.json': continue
                    emit(name, z.read(name))
        for path in sorted({textures.index[name] for name in shared_members}):
            with zipfile.ZipFile(textures.root / path) as z:
                for name in z.namelist():
                    if name in shared_members and name.lower() not in seen: emit(name, z.read(name))
    with zipfile.ZipFile(source / 'Localization/English_xml.pak') as z:
        source_names = read(z, 'text_ui_soul.xml')
    destination = output / 'Localization/English_xml.pak'; destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(base / 'Localization/English_xml.pak') as src, zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = read(src, info.filename)
            if info.filename.lower() == 'text_ui_soul.xml': data = merge_missing_strings(data, source_names)
            if info.filename.lower() == 'text_ui_items.xml':
                for additions in localizations: data = merge_missing_strings(data, additions)
            dst.writestr(info, data)
    result = dict(stage=str(stage), converted_residents=len(people), source_counts=report['counts'],
                  world_registrations=registrations, added_faction_locations=added_locations,
                  installed=False, runtime_verified=False,
                  all_people_loaded=False, systems_verified=False)
    (output / 'population-package.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-partial', action='store_true', help='Diagnostic package of completed residents only')
    args = parser.parse_args()
    result = assemble(args.stage, args.output, allow_partial=args.allow_partial)
    print(json.dumps(dict(converted_residents=result['converted_residents'], runtime_verified=False)))
