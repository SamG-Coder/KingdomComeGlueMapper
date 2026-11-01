"""Stage quest/profile NPCs, their source appearance, and journal markers.

Keeps existing actors and packages. Does not install or claim dialogue conversion.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import copy
import hashlib
import json
import os
import pickle
import time
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
from campaign_sources import RetailSourceReader
from campaign_dependency_adapters import SourceTables
from campaign_quest_people import dependencies, bind_markers
from campaign_profile_actors import collect as collect_profile_actors
from campaign_actor_dialogues import required_actor_souls
from character_person import resolve_person, register_person, actor_resources
from character_person_appearance import build_appearance
from character_person_world import register_person_world
from build_english_audio_test import merge_audio_strings
from build_horse_trader_test import rewrite
from upgrade_map import read, xml


ACTOR_PAK_LIMIT = 900 * 1024**2


def conversion_souls(requested, registered):
    """Count actual conversions, excluding duplicates, existing and filtered souls."""
    return {r['soul_id'] for r in requested.values() if r.get('soul_id')
            and not r.get('soul_class_id') and not r.get('soul_faction_id')} - set(registered)


def conversion_person(a, name, sid):
    person = getattr(a, 'resolved_people', {}).get(name)
    if person is None:
        return resolve_person(a.source, name, a.source_level)
    if (person['name'] != name or person['soul']['soul_id'] != sid
            or person['source_level'] != a.source_level
            or person['instance'].findtext('SharedSoulGuid') != sid or person.get('ai') is None):
        raise ValueError('Discovered source person does not match conversion: ' + name)
    return copy.deepcopy(person)


def write_actor_paks(appearance, data_dir, namespace, limit=ACTOR_PAK_LIMIT):
    """Pack and validate streamed assets, including families crossing ZIP32 shards."""
    from texture_streaming import checked_entries, generated_entries
    from setup_progress import work_progress
    libs, paks, pak = {}, [], None
    used, count = 22, 0
    paths = sorted(p for p in appearance.rglob('*') if p.is_file())
    from character_asset_selection import runtime_paths
    paths = runtime_paths(appearance, paths, namespace)
    asset_count = sum(p.relative_to(appearance).as_posix().startswith('objects/') for p in paths)
    work_progress('Pack character assets', 0, asset_count)
    # Characters pack in parallel into the shared Data folder, where other
    # workers list and open *.pak. Write each archive under a partial name
    # and give it its final name only once every shard is complete.
    def partial(path):
        if path.exists(): raise FileExistsError(path)
        return path.with_name(path.name + '.partial')
    def entries():
        completed, written = 0, 0
        for p in paths:
            name = p.relative_to(appearance).as_posix()
            if name.startswith('Libs/'): libs[name] = p.read_bytes()
            if not name.startswith('objects/'): continue
            yield name, p.read_bytes()
            completed += 1; written += p.stat().st_size
            work_progress('Pack character assets', completed, asset_count, f'{written / 1024**2:.1f} MiB processed')
        work_progress('Validate texture families', 0, 0, 'Checking packed mip chains')
    try:
        # Appearance conversion emits complete RGBA16F macro textures and
        # byte-channel masks. They need the same native streaming layout as
        # world assets, even when an actor is added after the main world build.
        for name, blob in checked_entries(generated_entries(entries())):
            size = len(blob) + 2 * len(name.encode('utf-8')) + 76
            if size + 22 > limit: raise ValueError('Actor asset exceeds ZIP32 shard limit: ' + name)
            if pak is not None and (used + size > limit or count >= 50000):
                pak.close(); pak = None
            if pak is None:
                suffix = '' if not paks else '_%02d' % len(paks)
                paks.append(data_dir / (namespace + suffix + '.pak'))
                pak = zipfile.ZipFile(partial(paks[-1]), 'x', zipfile.ZIP_STORED, allowZip64=False)
                used, count = 22, 0
            item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            item.create_system = 0
            pak.writestr(item, blob)
            used += size; count += 1
        if not paks:
            paks.append(data_dir / (namespace + '.pak'))
            pak = zipfile.ZipFile(partial(paks[0]), 'x', zipfile.ZIP_STORED, allowZip64=False)
    finally:
        if pak is not None: pak.close()
    for path in paks:
        partial(path).rename(path)
    work_progress('Pack character assets', asset_count, asset_count, 'Assets packed and validated')
    return paks, libs


def convert_character(source, target, mod, person, name, sid, source_level, namespace, output, data_dir,
                      notify=lambda message: None):
    """Appearance, carried equipment and actor archives of one character.

    Reads only the game installs and the input mod, and writes only this
    character's folders and archives, so characters can convert in parallel.
    person is None when it still has to be resolved from the source level.
    """
    started = time.perf_counter()
    cache, key, hit = character_cache(source, target, mod, person, name, sid, source_level, namespace)
    if hit is not None:
        from setup_build_cache import copy_payload
        notify('Reusing converted character: ' + name)
        payload, _ = hit
        copy_payload(payload/'Data', data_dir)
        result = pickle.loads((payload/'result.pickle').read_bytes())
        return dict(result, elapsed=time.perf_counter() - started)
    result = _convert_character_files(source, target, mod, person, name, sid, source_level, namespace, output, data_dir, notify)
    paks = result.pop('paks', [])
    if cache is not None and 'error' not in result:
        record = output/'people'/namespace/'cached-result.pickle'
        record.write_bytes(pickle.dumps({k: v for k, v in result.items() if k != 'elapsed'}))
        cache.publish(key, {'result.pickle': record, **{'Data/' + p.name: p for p in paks}}, {})
    return result


def character_cache(source, target, mod, person, name, sid, source_level, namespace):
    """(cache, key, hit) for one converted character; all None unless GLUEMAPPER_STEP_CACHE=1."""
    from container_items import item_patches
    from setup_build_cache import Cache, root, forced, fingerprint, converter_identity, install_fingerprint
    if root() is None or os.environ.get('GLUEMAPPER_STEP_CACHE') != '1':
        return None, None, None
    patches = hashlib.sha256()
    for blob in item_patches(mod):
        patches.update(hashlib.sha256(blob).digest())
    key = fingerprint(dict(version=1, step='character', converter=converter_identity(),
        kcd1=install_fingerprint(source), kcd2=install_fingerprint(target), item_patches=patches.hexdigest(),
        person=hashlib.sha256(pickle.dumps(person)).hexdigest() if person is not None else None,
        name=name, sid=sid, source_level=source_level, namespace=namespace))
    cache = Cache(root()/'characters')
    return cache, key, None if forced() else cache.lookup(key)


def _convert_character_files(source, target, mod, person, name, sid, source_level, namespace, output, data_dir, notify):
    from character_weapon_inventory import stage as stage_weapons
    started = time.perf_counter()
    notify('Resolving source character: ' + name)
    if person is None:
        person = resolve_person(source, name, source_level)
    notify('Converting source appearance: ' + person['name'])
    appearance = output / 'people' / namespace
    try:
        character = build_appearance(source, target, person, namespace, appearance,
            notify=lambda message: notify(person['name'] + ': ' + message))
    except ValueError as error:
        return dict(person=person, error=str(error))
    notify(person['name'] + ': Converting carried equipment')
    weapon_result = stage_weapons(source, target, mod, person, character, namespace, output / 'equipment' / namespace)
    character['source_weapons'] = weapon_result['items']
    item_tables = []
    for path, payload in weapon_result['files'].items():
        if path.startswith('Localization/'):
            item_tables.append(payload)
        else:
            dest = appearance / path; dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
    (appearance / 'appearance-result.json').write_text(json.dumps(character, indent=2))
    # Keep each actor's geometry in its own ZIP32 archive. The shared world
    # archive is already large and must not grow past the engine's limit.
    notify(person['name'] + ': Packing and validating character assets')
    paks, libs = write_actor_paks(appearance, data_dir, namespace)
    strings = appearance / 'Localization/English/text_ui_items.xml'
    if strings.exists(): item_tables.append(strings.read_bytes())
    return dict(person=person, character=character, libs=libs, item_tables=item_tables, paks=paks,
                elapsed=time.perf_counter() - started)


# Measured: a 14-part guard peaks below 1 GB with one texture bake worker.
CHARACTER_WORKER_BYTES = 1536 * 1024**2
_WORKER_BATCH = None


def _start_character_worker(bakes):
    """Share the texture bake budget between workers; keep one source batch open per worker."""
    global _WORKER_BATCH
    os.environ['GLUEMAPPER_BAKE_WORKERS'] = str(bakes)
    from character_build_cache import appearance_batch
    _WORKER_BATCH = appearance_batch()
    _WORKER_BATCH.__enter__()


def _convert_character(job, convert=None):
    return (convert or convert_character)(*job)


def convert_characters(jobs, progress, cancelled, convert=None):
    """Yield convert_character results in job order, in worker processes when memory allows.

    Workers are sized from free memory and a quarter of the cores, so a 16 GB
    machine runs one or two; GLUEMAPPER_CHARACTER_WORKERS overrides the count.
    convert replaces convert_character (module-level, for tests).
    """
    from bounded_pool import worker_count
    workers = worker_count(len(jobs), CHARACTER_WORKER_BYTES, 'GLUEMAPPER_CHARACTER_WORKERS',
                           limit=max(1, (os.cpu_count() or 1) // 4)) if len(jobs) > 1 else 1
    from setup_build_cache import root
    if jobs and root() is not None and os.environ.get('GLUEMAPPER_STEP_CACHE') == '1':
        # Bound the opt-in cache before this batch adds to it (one entry per character).
        from setup_build_cache import Cache
        Cache(root()/'characters').prune(keep=max(200, 3 * len(jobs)))
    yield from _convert_in_order(jobs, workers, progress, cancelled, convert)


def _convert_in_order(jobs, workers, progress, cancelled, convert):
    from bounded_pool import worker_count
    if workers <= 1:
        for job in jobs:
            yield (convert or convert_character)(*job, notify=progress)
        return
    bakes = max(1, worker_count(16, 768 * 1024**2, 'GLUEMAPPER_BAKE_WORKERS') // workers)
    progress(f'Converting {len(jobs)} characters in {workers} worker processes')
    with ProcessPoolExecutor(max_workers=workers, initializer=_start_character_worker, initargs=(bakes,)) as pool:
        futures = [pool.submit(_convert_character, job, convert) for job in jobs]
        try:
            for future in futures:
                if cancelled(): raise InterruptedError('Cancelled during character conversion')
                yield future.result()
        finally:
            for future in futures: future.cancel()


def build(a):
    from character_build_cache import appearance_batch
    with appearance_batch():
        return _build(a)


def _build(a):
    if a.output.exists(): raise FileExistsError('Choose a fresh stage')
    a.output.mkdir(parents=True)
    package = a.output / 'package'
    (package / 'Data').mkdir(parents=True)
    selected = getattr(a, 'person', [])
    if selected and (a.model or getattr(a, 'source_profile', []) or getattr(a, 'dialogue_model', [])):
        raise ValueError('Explicit actor staging must not implicitly expand quest/profile dependencies')
    if not selected and not a.model and not getattr(a, 'source_profile', []):
        raise ValueError('Provide a quest model, source profile or explicit source actors')
    model = json.loads(a.model.read_text(encoding='utf-8')) if a.model else None
    with RetailSourceReader(a.source) as reader:
        tables = SourceTables(a.source, reader)
        source_souls = {r['soul_id']: r['soul_name'] for r in tables.get('rpg/soul')['rows']}
        dialogue_models = [json.loads(p.read_text(encoding='utf-8')) for p in getattr(a,'dialogue_model',[])]
        eligible_souls = None
        if getattr(a, 'dialogue_profile', None):
            with zipfile.ZipFile(a.source / 'Data/Levels' / a.source_level / 'level.pak') as z:
                documents = {n:read(z,n) for n in z.namelist()
                    if n.replace('\\','/').lower().startswith('layers/') and n.lower().endswith('.xml')}
                eligible_souls = {r['soul_id'] for r in collect_profile_actors(
                    ET.fromstring(read(z,'whdata_1')), ET.fromstring(read(z,'whdata_0')),
                    documents, a.dialogue_profile)}
        dialogue_souls = required_actor_souls(dialogue_models,
            tables.get('rpg/soul2role')['rows'], tables.get('rpg/role')['rows'],
            tables.get('rpg/metarole')['rows'], eligible_souls) if dialogue_models else {}
    travel = a.mod / 'Data/gluemappertravel.pak'
    level = a.mod / 'Data/Levels' / a.level / 'level.pak'
    locale = a.mod / 'Localization/English_xml.pak'
    with zipfile.ZipFile(travel) as z:
        resources = {n: read(z, n) for n in z.namelist() if n.startswith('Libs/')}
        if model is not None:
            project_blob, quest_blob = read(z, a.project_path), read(z, a.quest_path)
    with zipfile.ZipFile(level) as z:
        files = {n: read(z, n) for n in ('objects_mission0.xml', 'whdata_0',
                 'tables/ai/scheduler.xml', 'triggerareas.fubar', 'waitinglinks.xml')}
    mission, wh = ET.fromstring(files['objects_mission0.xml']), ET.fromstring(files['whdata_0'])
    registered = {s.findtext('SharedSoulGuid') for s in wh.findall('./SoulList/Souls/Soul')
                  if any(e.get('EntityGuid') == s.findtext('EntityGuid') for e in mission)}
    next_id = max(int(e.get('EntityId', '0')) for e in mission) + 1
    def add(name, cls, pos, guid, rotation):
        nonlocal next_id
        if any(e.get('Name') == name or e.get('EntityGuid') == guid for e in mission):
            raise ValueError('Conflicting actor: ' + name)
        e = ET.SubElement(mission, 'Entity', Name=name, EntityClass=cls, Pos=pos,
                          EntityGuid=guid, EntityId=str(next_id), CastShadowMinSpec='1')
        if rotation: e.set('Rotate', rotation)
        next_id += 1
        return e
    people, converted, existing, pending, names, item_tables = [], [], [], [], ET.Element('Table'), []
    seen = set()
    requested = dict(dependencies(model)) if model is not None else {}
    for name in selected:
        matches = [sid for sid, source_name in source_souls.items() if source_name == name]
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous source actor: ' + name)
        requested['selected_actor_' + name] = dict(soul_id=matches[0])
    requested.update({"dialogue_role_" + role: dict(soul_id=sid) for role,sid in dialogue_souls.items()})
    profile_actors = []
    if getattr(a, 'source_profile', None):
        with zipfile.ZipFile(a.source / 'Data/Levels' / a.source_level / 'level.pak') as z:
            documents = {n:read(z,n) for n in z.namelist()
                         if n.replace('\\','/').lower().startswith('layers/') and n.lower().endswith('.xml')}
            profile_actors = collect_profile_actors(ET.fromstring(read(z,'whdata_1')),
                ET.fromstring(read(z,'whdata_0')), documents, a.source_profile)
        for actor in profile_actors:
            requested['profile_actor_' + actor['entity_guid']] = dict(soul_id=actor['soul_id'])
    total, completed = len(conversion_souls(requested, registered)), 0
    def progress(message):
        callback = getattr(a, 'progress', None)
        if callback is not None:
            callback('Convert source character appearances', completed, total, message)
        else:
            getattr(a, 'notify', print)(message)
    progress(f'{total} new characters to convert')
    # Decide every conversion first, in request order, then convert the
    # characters (in worker processes when memory allows) and register them
    # in that same order, so reports and archives match a serial run.
    plan = []
    for aid, row in requested.items():
        if getattr(a, 'cancelled', lambda: False)():
            raise InterruptedError('Cancelled before actor conversion')
        sid = row.get('soul_id')
        if not sid or row.get('soul_class_id') or row.get('soul_faction_id'):
            plan.append(('pending', dict(asset=aid, reason='Filtered or non-explicit soul'))); continue
        if sid in seen: continue
        seen.add(sid)
        if sid in registered:
            plan.append(('existing', dict(soul_id=sid, person=source_souls.get(sid)))); continue
        if sid not in source_souls: raise ValueError('Missing source soul: ' + sid)
        resolved = getattr(a, 'resolved_people', {}).get(source_souls[sid])
        plan.append(('convert', aid, sid, conversion_person(a, source_souls[sid], sid) if resolved is not None else None))
    jobs = [(a.source, a.target, a.mod, step[3], source_souls[step[2]], step[2], a.source_level,
             'gm_person_' + step[2].replace('-', ''), a.output, package / 'Data') for step in plan if step[0] == 'convert']
    results = convert_characters(jobs, progress, getattr(a, 'cancelled', lambda: False))
    for step in plan:
        if step[0] == 'pending':
            pending.append(step[1]); continue
        if step[0] == 'existing':
            existing.append(step[1]); continue
        if getattr(a, 'cancelled', lambda: False)():
            raise InterruptedError('Cancelled before actor conversion')
        _, aid, sid, _ = step
        result = next(results)
        person = result['person']
        if aid.startswith('profile_actor_') and person['actor'].get('EntityGuid', '').lower() != aid[14:].lower():
            raise ValueError('Profile actor resolved to a different source placement: ' + person['name'])
        namespace = 'gm_person_' + sid.replace('-', '')
        if 'error' in result:
            pending.append(dict(asset=aid, person=person['name'], reason=result['error']))
            print(json.dumps(pending[-1]), flush=True)
            completed += 1
            progress('Appearance conversion failed: ' + person['name'] + ': ' + result['error'])
            continue
        character = result['character']
        resources = actor_resources(a.target, person, character, namespace,
            role_name=namespace + '_role', native_soul_table='Libs/Tables/rpg/soul__ttac.xml',
            native_soul_name='ttac_procek', native_role_name='PREVOZNIK_TROSECKO', resources=resources)
        resources.update(result['libs'])
        item_tables.extend(result['item_tables'])
        with zipfile.ZipFile(a.source / 'Localization/English_xml.pak') as z:
            key = person['instance'].findtext('StaticData/NameStringId')
            names.append(next(r for r in ET.fromstring(read(z, 'text_ui_soul.xml')) if r.findtext('Cell') == key))
        placement = getattr(a, "placements", {}).get(person["name"])
        register_person(add, wh, person, placement)
        people.append(person); registered.add(sid)
        converted.append(dict(person=person['name'], soul_id=sid, position=(placement or person['actor']).get('Pos'),
                              source_level=person['source_level'], namespace=namespace,
                              elapsed_seconds=result['elapsed'],
                              appearance_timings_seconds=character.get('timings_seconds', {})))
        completed += 1
        progress(f'{person["name"]}: completed ({completed}/{total}) in {result["elapsed"]:.1f} s')
    from bake_pool import release
    from static_assets import release_library_index
    release(); release_library_index()  # the people are converted; free the bake workers and archives
    if getattr(a, 'progress', None) is not None:
        a.progress('Bind source characters to the world', 0, 0, 'Writing schedules, world placements and shared archives')
    files['objects_mission0.xml'], files['whdata_0'] = xml(mission), xml(wh)
    files, world = register_person_world(files, people, a.source, a.target)
    markers = dict(converted=[])
    if model is not None:
        resources[a.project_path], resources[a.quest_path], markers = bind_markers(project_blob, quest_blob, model, registered)
    modid = ET.parse(a.mod / 'mod.manifest').findtext('info/modid')
    for path, blob in files.items():
        if path.startswith('tables/'): resources[f'Mods/{modid}/Data/Levels/{a.level}/{path}'] = blob
    with zipfile.ZipFile(locale) as z:
        strings = {'text_ui_soul.xml': merge_audio_strings(read(z, 'text_ui_soul.xml'), xml(names))}
        if item_tables:
            blob = read(z, 'text_ui_items.xml')
            for table in item_tables: blob = merge_audio_strings(blob, table)
            strings['text_ui_items.xml'] = blob
    rewrite(travel, package / 'Data/gluemappertravel.pak', resources)
    rewrite(level, package / 'Data/Levels' / a.level / 'level.pak', files)
    rewrite(locale, package / 'Localization/English_xml.pak', strings)
    report = dict(converted=converted, existing=existing, pending=pending, markers=markers, world=world,
        source_profile_actors=profile_actors, dialogue_actor_dependencies=dialogue_souls,
        profile_activation_converted=False,
        profile_activation_note='Staged actors are mission placements; conditional profile lifecycle still requires conversion',
        runtime_verified=False, dialogue_converted=False,
        baseline={str(p.relative_to(a.mod)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (travel, level, locale)})
    (a.output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(dict(converted=len(converted), existing=len(existing), markers=len(markers['converted']))))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'target', 'mod', 'output'): p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--model', type=Path)
    p.add_argument('--person', action='append', default=[], help='Stage only these source actors, preserving every existing quest')
    p.add_argument('--source-level', default='rataje'); p.add_argument('--level', default='kcd1_travel')
    p.add_argument('--project-path', default='Quests/GlueTravel/GlueTravel_kcd1.xml')
    p.add_argument('--quest-path')
    p.add_argument('--source-profile', action='append', default=[],
        help='Also stage actors placed in this authored source profile; does not activate a profile')
    p.add_argument('--dialogue-profile', action='append', default=[],
        help='Resolve shared dialogue roles within these authored source profiles')
    p.add_argument('--dialogue-model', action='append', type=Path, default=[],
        help='Include every source NPC role dependency from a resolved dialogue model')
    build(p.parse_args())
