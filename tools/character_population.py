"""Resumable source population conversion using the shared person upgrader.

Each successful placement has a converted appearance archive and an explicit
native registration. Conditional source actors stay conditional: this does not
activate every quest/battle layer just to increase the spawned NPC count.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

from character_person import PersonSource, actor_resources, native_xml
from character_person_ai import native_brain_and_class, register_factions
from character_person_appearance import AppearanceSession, build_appearance
from character_texture_cache import TextureCache
from character_population_factions import register_catalog
from character_person_brain import register_brain
from upgrade_map import read


VERSION = 1
TEXTURES = 'objects/characters/gluepopulation/textures/'


def placement_person(source_person, duplicate_names=()):
    """Isolate instance overrides without merging shared souls or repeated names."""
    person = copy.deepcopy(source_person)
    identity = person['instance'].findtext('Guid')
    person['source_name'] = person['name']
    person['source_shared_soul'] = person['instance'].findtext('SharedSoulGuid')
    person['soul']['soul_id'] = str(uuid.uuid5(uuid.NAMESPACE_URL,
        'gluemapper/population/' + person['source_level'] + '/' + identity))
    if person['name'] in duplicate_names:
        person['name'] += '_gm_' + person['actor'].get('EntityGuid').lower()
    return person


def source_membership(source, level):
    """Track the actual serialized container, not just the editor's layer label."""
    result = {}
    with zipfile.ZipFile(Path(source) / 'Data/Levels' / level / 'level.pak') as archive:
        for entry in archive.namelist():
            normalized = entry.replace('\\', '/').lower()
            if normalized != 'objects_mission0.xml' and not (normalized.startswith('layers/') and normalized.endswith('.xml')):
                continue
            try:
                root = ET.fromstring(read(archive, entry))
            except ET.ParseError:
                continue
            for entity in root.iter('Entity'):
                if entity.get('EntityClass') not in ('NPC', 'NPC_Female'):
                    continue
                guid = entity.get('EntityGuid').lower()
                if guid in result:
                    raise ValueError('Source human is serialized twice: ' + guid)
                result[guid] = dict(container=entry, resident=normalized == 'objects_mission0.xml',
                                    source_layer=entity.get('Layer'))
    return result


def input_signature(person):
    payload = dict(version=VERSION, soul=person['soul'], name=person['name'],
        actor=ET.tostring(person['actor'], encoding='unicode'),
        instance=ET.tostring(person['instance'], encoding='unicode'))
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def migrate_texture_cache(legacy, cache):
    """Compress this converter's legacy scratch textures before a resumed run."""
    import re
    families = {}
    root = legacy.resolve()
    for path in legacy.rglob('*'):
        if not path.is_file(): continue
        name = path.relative_to(legacy).as_posix()
        match = re.fullmatch(r'(.+\.dds)(\.(?:a|[0-9]+a?))?', name, re.I)
        if not match: raise ValueError('Unexpected legacy texture cache member: ' + name)
        families.setdefault(match[1], []).append((name, path))
    for entries in families.values():
        if not all(cache.contains(name) for name, _ in entries):
            cache.put({name: path.read_bytes() for name, path in entries})
            cache.save()
        verified = cache.family(entries[0][0])
        for name, path in entries:
            # Only remove converter-owned scratch files after their compressed
            # replacements have been written and read back successfully.
            if not path.resolve().is_relative_to(root): raise ValueError('Unsafe cache path')
            if verified[name] != path.read_bytes(): raise ValueError('Texture cache verification failed')
            path.unlink()


def upgrade_cached_archive(path, cache):
    """Deduplicate baked textures in an already converted appearance archive."""
    temporary = path.with_suffix('.tmp')
    with zipfile.ZipFile(path) as z:
        report = json.loads(z.read('population-character.json'))
        if report.get('compressed_textures'): return report
        files = {n: (lambda n=n: z.read(n)) for n in z.namelist() if n != 'population-character.json'}
        materials, shared, excluded = cache.externalize(files)
        shared.update(report['shared_textures'])
        report.update(shared_textures=sorted(shared), compressed_textures=True)
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as dst:
            for name in files:
                if name not in excluded: dst.writestr(name, materials.get(name, files[name]()))
            dst.writestr('population-character.json', json.dumps(report))
    temporary.replace(path)
    return report


def stage(source, target, base_package, output, *, level='rataje', limit=None):
    """Convert resident placements; retain every conditional placement in coverage.

    A failed NPC never gets a dummy body or a substitute outfit. Its exact error
    remains retryable in population.json. Completed archives are reusable when
    their source signature agrees, so correcting one adapter needn't restart all.
    """
    source, target, base_package, output = map(Path, (source, target, base_package, output))
    output.mkdir(parents=True, exist_ok=True)
    actors_dir = output / 'actors'; actors_dir.mkdir(exist_ok=True)
    shared = TextureCache(output / 'textures')
    legacy = output / 'shared'
    if legacy.exists(): migrate_texture_cache(legacy, shared)
    base_level = base_package / 'Data/Levels/kcd1_travel/level.pak'
    with zipfile.ZipFile(base_level) as z:
        original = ET.fromstring(read(z, 'whdata_0'))
        existing = {n.findtext('Guid') for n in original.findall('SoulList/Souls/Soul')}
    membership = source_membership(source, level)
    with zipfile.ZipFile(target / 'Data/Tables.pak') as z:
        tables = {p: native_xml(read(z, p)) for p in (
            'Libs/Tables/ai/brain.xml', 'Libs/Tables/rpg/social_class.xml', 'Libs/Tables/rpg/FactionTree.xml')}
    report = dict(version=VERSION, source=str(source), target=str(target), source_level=level,
        base_package=str(base_package.resolve()), actors=[], runtime_verified=False,
        all_people_loaded=False, systems_verified=False)
    def checkpoint():
        report['counts'] = dict(Counter(a['status'] for a in report['actors']))
        temp = output / 'population.json.tmp'
        temp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temp.replace(output / 'population.json')
    attempted = 0
    with PersonSource(source, level) as catalog, AppearanceSession(source, target, shared) as session:
        faction_resources = register_catalog(catalog.tables, tables['Libs/Tables/rpg/FactionTree.xml'])
        names = Counter(a.get('Name') for a in catalog.actors)
        duplicates = {name for name, count in names.items() if count > 1}
        for actor in catalog.actors:
            guid = actor.get('EntityGuid').lower()
            member = membership[guid]
            row = dict(source_name=actor.get('Name'), source_guid=guid, **member)
            report['actors'].append(row)
            person = placement_person(catalog.resolve(entity_guid=guid), duplicates)
            row['instance_guid'] = person['instance'].findtext('Guid')
            if row['instance_guid'] in existing:
                row['status'] = 'existing_preserved'; continue
            if not member['resident']:
                row['status'] = 'conditional_layer_pending'; continue
            if limit is not None and attempted >= limit:
                row['status'] = 'not_attempted'; continue
            attempted += 1
            namespace = 'gmp_' + guid
            archive_path = actors_dir / (guid + '.zip')
            signature = input_signature(person)
            started = time.monotonic()
            try:
                # Catch unsupported semantics before spending time on geometry.
                brains = register_brain(person['ai'], target, faction_resources, tables['Libs/Tables/ai/brain.xml'], tables)
                native_brain_and_class(person['ai'], brains, tables['Libs/Tables/rpg/social_class.xml'])
                register_factions(dict(faction_resources), person['ai'], tables['Libs/Tables/rpg/FactionTree.xml'])
                if archive_path.exists():
                    cached = upgrade_cached_archive(archive_path, shared)
                    if cached['signature'] != signature:
                        raise ValueError('Cached source changed; select a new output directory')
                    character = cached['character']
                    if any(not shared.contains(path) for path in cached['shared_textures']):
                        raise ValueError('Cached appearance has missing shared textures')
                else:
                    with tempfile.TemporaryDirectory(prefix='person-', dir=output) as tmp:
                        character_root = Path(tmp) / 'character'
                        character = build_appearance(source, target, person, namespace, character_root, session=session)
                        # Validate registration before publishing a successful archive.
                        actor_resources(target, person, character, namespace, native_cache=tables, resources=faction_resources)
                        files = {p.relative_to(character_root).as_posix(): p.read_bytes
                                 for p in character_root.rglob('*') if p.is_file()}
                        materials, textures, excluded = shared.externalize(files)
                        temporary_archive = Path(tmp) / 'converted.zip'
                        with zipfile.ZipFile(temporary_archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
                            for name, load in files.items():
                                if name not in excluded: z.writestr(name, materials.get(name, load()))
                            z.writestr('population-character.json', json.dumps(dict(signature=signature,
                                character=character, shared_textures=sorted(textures), compressed_textures=True)))
                        temporary_archive.replace(archive_path)
                row.update(status='converted', namespace=namespace, soul_id=person['soul']['soul_id'],
                           target_name=person['name'], archive=str(archive_path.relative_to(output)),
                           gender=character['gender'], seconds=round(time.monotonic()-started, 2))
            except (ValueError, KeyError, StopIteration, FileNotFoundError, NotImplementedError) as error:
                row.update(status='blocked', error=type(error).__name__ + ': ' + str(error))
            print(json.dumps(dict(index=attempted, person=row['source_name'], status=row['status'],
                                  error=row.get('error'), seconds=round(time.monotonic()-started, 2))), flush=True)
            checkpoint()
    checkpoint()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--base-package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--level', default='rataje')
    parser.add_argument('--limit', type=int, help='Bound a development conversion; unattempted actors remain explicit')
    args = parser.parse_args()
    result = stage(args.source, args.target, args.base_package, args.output, level=args.level, limit=args.limit)
    print(json.dumps(result['counts']))
