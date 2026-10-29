"""Normal Setup migration for receipt-owned KCD1 character appearances.

Clean builds and Resume share the source converter. Only outdated appearances
are rebuilt; completed world, quest and lighting archives remain unchanged.
The caller must use private staging (setup_resume.update_private_package).
"""
import json
from pathlib import Path
import tempfile
import zipfile

from character_person_appearance import APPEARANCE_POLICY_VERSION, build_appearance
from character_person import resolve_person
from build_quest_people_test import write_actor_paks
from setup_campaign import digest
from upgrade_source_appearances import (TRAVEL, LOCALE, imports, owned_shards,
    retire_shared_assets, retire_shared_definitions, validate_resources, verify_bindings, preserve_carried_assets)
from setup_package_upgrade import rewrite
from clothing_inventory import require_equipment, relevant_resource
from filesystem_paths import filesystem_path


def upgrade(package, receipt, source, target, notify=lambda *a: None, cancelled=lambda: False):
    package = filesystem_path(package)
    changed, removed, resources, labels, rebuilt = {}, [], {}, [], []
    with zipfile.ZipFile(package/TRAVEL) as archive:
        rows = {n: json.loads(archive.read(n)) for n in archive.namelist()
                if n.startswith('Libs/GlueMapper/PersonImport/') and n.endswith('.json')}
        # Native component clones have their own converter; do not claim to
        # rebuild them from source or treat them as missing source receipts.
        rows = {n:r for n,r in rows.items() if r.get('appearance') == 'character_person_appearance'}
        people = imports(archive, {r['person'] for r in rows.values()})
        old_tables = {n:archive.read(n) for n in archive.namelist()
                      if n.lower().startswith('libs/tables/') and n.endswith('.xml')}
        stale = {r['person']:r['namespace'] for r in rows.values()
                 if r.get('appearance_policy') != APPEARANCE_POLICY_VERSION}
    if stale and (source is None or target is None):
        raise ValueError('KCD1 and KCD2 folders are required to upgrade saved source clothing')
    if cancelled():
        raise InterruptedError('Cancelled before source clothing upgrade')
    shards = {person:owned_shards(package, receipt['files'], ns, allow_shared=True, allow_equipment=True)
              for person,ns in stale.items()}
    retained_equipment = {}
    # Stage one complete person at a time. PAK replacements break checkpoint
    # hardlinks; never overwrite asset bytes through a shared file handle.
    from bake_pool import release
    try:
        for index,(name,namespace) in enumerate(stale.items()):
            if cancelled():
                raise InterruptedError('Cancelled during source clothing upgrade')
            notify('Upgrade saved character clothing', index, len(stale), name)
            person = resolve_person(Path(source), name, include_ai=False)
            if namespace.startswith('gm_person_') and 'gm_person_'+person['soul']['soul_id'].replace('-','') != namespace:
                raise ValueError('Source soul no longer matches import receipt: '+name)
            with tempfile.TemporaryDirectory(prefix='setup-appearance-', dir=package.parent) as temp:
                work = Path(temp); appearance = work/'appearance'
                character = build_appearance(Path(source), Path(target), person, namespace, appearance,
                    notify=lambda message: notify('Upgrade saved character clothing', index, len(stale), name + ': ' + message))
                with zipfile.ZipFile(package/TRAVEL) as archive:
                    verify_bindings(archive, character, namespace)
                packed = work/'packed'; packed.mkdir()
                notify('Upgrade saved character clothing', index, len(stale), name + ': Packing and validating character assets')
                paks, libs = write_actor_paks(appearance, packed, namespace)
                validate_resources(libs, namespace); resources.update(libs)
                strings = appearance/'Localization/English/text_ui_items.xml'
                if strings.exists(): labels.append(strings.read_bytes())
                incoming = {'Data/'+p.name for p in paks}
                notify('Upgrade saved character clothing', index, len(stale), name + ': Preserving carried equipment')
                kept = preserve_carried_assets(package, namespace, shards[name])
                changed.update(kept); retained_equipment.update(kept)
                for pak in paks:
                    relative = 'Data/'+pak.name
                    changed[relative] = digest(pak)
                    pak.replace(package/relative)
                for old in shards[name]:
                    if old not in incoming:
                        (package/old).unlink(); removed.append(old)
                rebuilt.append(dict(person=name,namespace=namespace,gender=character['gender']))
            path = 'Libs/GlueMapper/PersonImport/'+namespace+'.json'
            rows[path]['appearance_policy'] = APPEARANCE_POLICY_VERSION
            resources[path] = json.dumps(rows[path],indent=2).encode('utf-8')
        if stale:
            changed.update(retire_shared_assets(package, receipt['files'], stale.values(),
                [p for paths in shards.values() for p in paths]))
    finally:
        if stale: release()
    shared, retired = retire_shared_definitions(old_tables, stale.values(), resources)
    if resources:
        rewrite(package, TRAVEL, {**shared, **resources})
        changed[TRAVEL] = digest(package/TRAVEL)
    if labels:
        from build_english_audio_test import merge_audio_strings
        with zipfile.ZipFile(package/LOCALE) as archive:
            before = blob = archive.read('text_ui_items.xml')
        for label in labels: blob = merge_audio_strings(blob, label)
        if blob != before:
            rewrite(package, LOCALE, {'text_ui_items.xml':blob})
            changed[LOCALE] = digest(package/LOCALE)
    # Always check actual Storm -> equipment wrapper -> outfit -> item chains.
    # A current marker cannot disguise a naked NPC caused by a broken wrapper.
    with zipfile.ZipFile(package/TRAVEL) as archive:
        checks = require_equipment({n:archive.read(n) for n in archive.namelist() if relevant_resource(n)},
                                   {ns:name for name,ns in people.items()})
    if cancelled(): raise InterruptedError('Cancelled before accepting source clothing upgrade')
    notify('Upgrade saved character clothing', len(stale), len(stale), 'Equipment references validated')
    return changed, removed, dict(version=APPEARANCE_POLICY_VERSION, mode='source_converter',
        actors=len(people), rebuilt=rebuilt, equipment_validation=True, equipment_checks=checks,
        retired_shared_definitions=retired, retained_equipment=sorted(retained_equipment), runtime_verified=False)


def apply(package, receipt, source, target, notify, cancelled):
    changed, removed, report = upgrade(package, receipt, source, target, notify, cancelled)
    receipt['files'].update(changed)
    for name in removed: receipt['files'].pop(name, None)
    receipt['source_appearance_upgrade'] = report
