"""Bounded migration of completed setup packages; never re-run world conversion."""
import copy
import json
import shutil
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
from upgrade_map import read, xml
from setup_campaign import RECEIPT, MOD_ID, digest, verify_package


def migrate_step_ids(report):
    from setup_gameplay import STAGE_IDS
    old = [s for s in STAGE_IDS if s != 'gm_guard_interaction']
    steps = report.get('steps', [])
    if [s.get('id') for s in steps if s.get('id')] == old:
        index = next(i for i,s in enumerate(steps) if s.get('id') == 'combat')
        steps.insert(index, dict(id='gm_guard_interaction', name='Source guard conversations (migration)'))


def eligible(receipt):
    from build_recipe import VERSION, require_current
    from campaign_start_policy import POLICY
    candidate = copy.deepcopy(receipt)
    report = candidate.get('build_recipe', {})
    if report.get('version') not in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, VERSION):
        raise ValueError('Saved gameplay recipe is not supported for migration')
    report['version'] = VERSION
    migrate_step_ids(report)
    report.setdefault('content_validation', {})['start_policy'] = POLICY
    candidate['single_playable_world'] = True
    require_current(candidate)


def rewrite(package, relative, replacements, remove=lambda name: False):
    path = package/relative
    pending = path.with_suffix('.upgrade-pending')
    # Baked terrain archives already exceed ZIP's 65,535-entry directory limit.
    # ZIP64 is required for their directory, even when every member is small.
    # Python only emits the extension when needed; ordinary PAKs stay unchanged.
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(pending, 'x', zipfile.ZIP_STORED, allowZip64=True) as out:
        for entry in source.infolist():
            if not remove(entry.filename):
                out.writestr(entry, replacements.get(entry.filename, read(source, entry.filename)))
        for name in sorted(set(replacements) - set(source.namelist())):
            out.writestr(name, replacements[name])
    with zipfile.ZipFile(pending) as check:
        if check.testzip() is not None:
            raise ValueError('Package migration archive verification failed')
    pending.replace(path)


def finalize(package, *, source=None, target=None, notify=lambda *a: None, cancelled=lambda: False):
    """Called only on a private build or the transactional resume staging copy."""
    from campaign_start_policy import gate_start
    from region_travel_companions import remove_horse_recovery
    from smart_area_registration import reconcile
    from setup_gameplay import PROJECT, OPENING, validate
    from build_recipe import VERSION
    package = Path(package).resolve()
    receipt = verify_package(package)
    eligible(receipt)
    if receipt.get('travel_bridge', {}).get('destination') != 'kcd1_travel':
        raise ValueError('Expected the established playable travel world')
    from setup_guard_conversations import upgrade as upgrade_guards
    guard_files, guard_report = upgrade_guards(package, source, target)
    receipt['files'].update(guard_files)
    if guard_report: receipt['guard_conversations'] = guard_report
    from setup_tavern_upgrade import upgrade as upgrade_tavern
    tavern_files, tavern_report = upgrade_tavern(package, source, target)
    receipt['files'].update(tavern_files)
    if tavern_report: receipt['source_tavern'] = tavern_report
    from setup_merchant_upgrade import upgrade as upgrade_merchants
    merchant_files, merchant_reports = upgrade_merchants(package, source, target)
    receipt['files'].update(merchant_files)
    if merchant_reports: receipt['source_merchants'] = merchant_reports
    changes = {}
    format_level_changes = {}
    travel = 'Data/gluemappertravel.pak'
    with zipfile.ZipFile(package/travel) as z:
        player = 'Scripts/Entities/actor/player.lua'
        changes = {PROJECT: gate_start(read(z, PROJECT), OPENING), player: remove_horse_recovery(read(z, player))}
        if b'SetStatLevelDebug' in changes[PROJECT] or b'SetSkillLevelDebug' in changes[PROJECT]:
            if target is None:
                raise ValueError('Game folder required to migrate retail campaign initialization')
            from campaign_initial_state import upgrade_debug_initializers
            with zipfile.ZipFile(Path(target)/'Data/IPL_GameData.pak') as native:
                changes[PROJECT], initialization_report = upgrade_debug_initializers(
                    changes[PROJECT], 'gm_source_baseline', read(native, 'Libs/concept/definitions.xml'))
            receipt['retail_initialization_upgrade'] = initialization_report
        from region_travel_merchant import repair_trade_prompts
        for name in z.namelist():
            if name.lower().startswith('quests/') and name.lower().endswith('.xml'):
                original = changes.get(name, read(z, name))
                repaired = repair_trade_prompts(original)
                if repaired != original:
                    changes[name] = repaired
        legacy_world = next((n for n in z.namelist() if n.lower() == 'quests/gluetravel/world/open_world.xml'), None)
        world = legacy_world or next((n for n in z.namelist() if n.lower() == 'quests/gluetravel/entry/world/open_world.xml'), None)
        if world:
            if target is None:
                raise ValueError('Game folder required to repair packaged world gameplay dependencies')
            from world_gameplay_basis import package_dependencies, validate_dependency_scopes
            entry = next(n for n in z.namelist() if n.lower() == 'quests/gluetravel/entry/kcd1_travel.xml')
            with zipfile.ZipFile(Path(target)/'Data/Scripts.pak') as scripts:
                dependencies = package_dependencies({entry: read(z, entry), world: read(z, world)},
                                                    entry, world, scripts)
            with zipfile.ZipFile(Path(target)/'Data/IPL_GameData.pak') as native:
                validate_dependency_scopes(dependencies, entry.rsplit('/', 1)[0]+'/world/open_world.xml',
                                           read(native, 'Libs/concept/definitions.xml'))
            changes.update(dependencies)
        from setup_gameplay import QUEST_PATH, QUEST
        marker_report = None
        if QUEST_PATH in z.namelist():
            if source is None:
                raise ValueError('KCD1 source folder required to restore quest objective markers')
            from quest_import import import_quest
            from campaign_quest_people import bind_markers
            model, _ = import_quest(Path(source), QUEST)
            with zipfile.ZipFile(package/'Data/Levels/kcd1_travel/level.pak') as level_archive:
                wh = ET.fromstring(read(level_archive, 'whdata_0'))
            registered = {s.findtext('SharedSoulGuid') for s in wh.findall('SoulList/Souls/Soul')}
            changes[PROJECT], changes[QUEST_PATH], marker_report = bind_markers(
                changes[PROJECT], read(z, QUEST_PATH), model, registered)
            from campaign_place_markers import bind as bind_places
            if model['tables'].get('quest_place', {}).get('rows'):
                with zipfile.ZipFile(Path(source)/'Data/Levels/rataje/level.pak') as original:
                    source_entities = [e for n in original.namelist() if n.lower() == 'objects_mission0.xml'
                        or n.lower().startswith('layers/') and n.lower().endswith('.xml')
                        for e in ET.fromstring(read(original,n)).iter('Entity')]
                with zipfile.ZipFile(package/'Data/Levels/kcd1_travel/level.pak') as level_archive:
                    (changes[PROJECT], changes[QUEST_PATH], format_level_changes['objects_mission0.xml'],
                     format_level_changes['waitinglinks.xml'], marker_report['places']) = bind_places(
                        changes[PROJECT], changes[QUEST_PATH], read(level_archive,'objects_mission0.xml'),
                        read(level_archive,'waitinglinks.xml'), model, source_entities)
            from campaign_objective_conditions import bind as bind_conditions
            quest_root = ET.fromstring(changes[QUEST_PATH])
            conditions = bind_conditions(quest_root.find('./Skald/Quest/Nodes'), model)
            from campaign_objective_events import bind as bind_events
            events = bind_events(quest_root.find('./Skald/Quest/Nodes'), model)
            marker_report['objective_events'] = events
            if conditions or events:
                changes[QUEST_PATH] = ET.tostring(quest_root, encoding='utf-8', xml_declaration=True)
            script_repairs = {}
            candidates = {n: changes.get(n, read(z, n)) for n in z.namelist()
                          if n.startswith('Quests/') and n.endswith('.xml') and b'ExitScript=' in read(z, n)}
            if any(b'Utils.' in b for b in candidates.values()):
                from campaign_sources import RetailSourceReader
                from campaign_dependency_adapters import SourceTables
                from campaign_script_import import upgrade_dialogue
                with RetailSourceReader(Path(source)) as reader:
                    roles = SourceTables(Path(source), reader).get('rpg/role')['rows']
                for name, blob in candidates.items():
                    updated, count = upgrade_dialogue(blob, roles)
                    if count:
                        changes[name] = updated
                        script_repairs[name] = count
            marker_report['objective_conditions'] = conditions
            marker_report['dialogue_script_repairs'] = script_repairs
            from campaign_actor_inventory_upgrade import plan as inventory_upgrade
            equipment = {n:read(z,n) for n in z.namelist() if n.startswith('Libs/Storm/equipment/')}
            inventory_changes, inventories = inventory_upgrade(package, source, target, equipment)
            changes.update(inventory_changes)
            marker_report['actor_inventories'] = inventories
        from campaign_format_upgrade import polylogues
        resources = {n:read(z,n) for n in z.namelist() if n.startswith(('Libs/','Quests/'))}
        resources.update(changes)
        dialogue_changes, dialogue_report = polylogues(resources, source, target)
        changes.update(dialogue_changes)
        receipt['dialogue_format_upgrade'] = dict(conversations=dialogue_report, runtime_verified=False)
        resources.update(changes)
        from campaign_dialogue_requests import plans as dialogue_requests
        if QUEST_PATH in resources and dialogue_requests(model):
            from campaign_dialogue_requests import lower as lower_requests
            from build_campaign_combat import source_binding
            bindings = source_binding(model, Path(source)/'Data/Levels/rataje/level.pak')
            request_changes, request_report = lower_requests(resources, model, bindings)
            changes.update(request_changes)
            resources.update(request_changes)
            receipt['source_dialogue_requests'] = request_report
        from campaign_branch_resume import upgrade as upgrade_branch_resume
        branch_changes, branch_report = upgrade_branch_resume(resources, source)
        changes.update(branch_changes)
        receipt['dialogue_root_resume'] = dict(branches=branch_report, runtime_verified=False)
        resources.update(changes)
        from campaign_proximity_upgrade import upgrade as upgrade_proximity
        proximity_changes, proximity_report = upgrade_proximity(resources, source)
        changes.update(proximity_changes)
        receipt['proximity_observers'] = dict(observers=proximity_report, runtime_verified=False)
        resources.update(changes)
        from campaign_quest_inventory import upgrade as upgrade_quest_inventory
        quest_changes, quest_level, quest_report = upgrade_quest_inventory(
            package, source, target, resources, PROJECT, OPENING, format_level_changes)
        changes.update(quest_changes)
        format_level_changes.update(quest_level)
        receipt['source_quest_inventories'] = quest_report
    rewrite(package, travel, changes, lambda n: n == legacy_world)
    from world_speech_audio import upgrade as upgrade_world_speech
    speech_files, speech_report = upgrade_world_speech(package, target, notify)
    receipt['files'].update(speech_files)
    receipt['world_speech_audio'] = speech_report
    level = 'Data/Levels/kcd1_travel/level.pak'
    if format_level_changes:
        rewrite(package, level, format_level_changes)
    with zipfile.ZipFile(package/level) as z:
        metadata, added = reconcile(read(z, 'whdata_0'), read(z, 'objects_mission0.xml'))
        from area_geometry_contract import normalize
        missions = [read(z, n) for n in z.namelist() if n.lower() == 'objects_mission0.xml'
                    or n.lower().startswith('layers/') and n.lower().endswith('.xml')]
        geometry, removed = normalize(read(z, 'triggerareas.fubar'), missions)
        normalize(geometry, missions, validate_only=True)
        from skalitz_outer_gates import normalize as correct_gates
        gates, gate_report = correct_gates({n:read(z,n) for n in ('terrain/hlods.dat','terrain/hlods.xml')})
    rewrite(package, level, {'whdata_0': metadata, 'triggerareas.fubar': geometry, **gates})
    runtime = f'Data/{MOD_ID}.pak'
    table = f'Libs/Tables/level__{MOD_ID}.xml'
    with zipfile.ZipFile(package/runtime) as z:
        root = ET.fromstring(read(z, table))
    playable = [row for row in root.iter() if row.get('LevelName') == 'kcd1_travel']
    if len(playable) != 1 or playable[0].get('LevelId') != '1001':
        raise ValueError('Playable travel level identity is missing or changed')
    for parent in root.iter():
        for row in list(parent):
            if row.get('LevelName') == 'kcd1_rataje':
                if row.get('LevelId') != '1000':
                    raise ValueError('Unexpected intermediate level identity')
                parent.remove(row)
    rewrite(package, runtime, {table: xml(root)}, lambda n: '/data/levels/kcd1_rataje/' in n.lower())
    intermediate = (package/'Data/Levels/kcd1_rataje').resolve()
    if not intermediate.is_relative_to(package) or intermediate == package:
        raise ValueError('Intermediate world is outside the private package')
    if intermediate.exists():
        shutil.rmtree(intermediate)
    receipt['single_playable_world'] = True
    from mesh_streaming import restore_package
    restored, removed_streams = restore_package(package)
    receipt['files'].update(restored)
    for name in removed_streams:
        receipt['files'].pop(name, None)
    receipt.pop('mesh_streaming', None)
    from mesh_lod_metadata import upgrade_package
    mesh_files, mesh_report = upgrade_package(package)
    receipt['files'].update(mesh_files)
    receipt['mesh_lod_metadata'] = mesh_report
    from setup_streaming import validate_mesh_package
    receipt['build_recipe']['streaming_validation'].update(validate_mesh_package(package))
    from hlod_spatial_metadata import upgrade_package as upgrade_spatial_metadata
    spatial_files, spatial_report = upgrade_spatial_metadata(package)
    receipt['files'].update(spatial_files)
    receipt['vegetation_spatial_metadata'] = spatial_report
    receipt['build_recipe']['streaming_validation']['vegetation_grid_version'] = spatial_report['version']
    from lod_ratio_metadata import upgrade_package as upgrade_lod_ratios
    ratio_files, ratio_report = upgrade_lod_ratios(package)
    receipt['files'].update(ratio_files)
    receipt['lod_ratio_metadata'] = ratio_report
    receipt['build_recipe']['streaming_validation']['lod_ratio_version'] = ratio_report['version']
    from setup_actor_materials import upgrade as upgrade_materials
    actor_files, actor_removed, actor_report = upgrade_materials(package, target, notify, cancelled)
    receipt['files'].update(actor_files)
    for name in actor_removed: receipt['files'].pop(name, None)
    if actor_report['actors'] or 'actor_material_upgrade' not in receipt:
        receipt['actor_material_upgrade'] = actor_report
    from item_icons import upgrade as upgrade_item_icons
    icon_files, icon_report = upgrade_item_icons(package, source)
    receipt['files'].update(icon_files)
    if icon_report: receipt['source_item_icons'] = icon_report
    receipt['build_recipe']['content_validation'] = validate(package)
    receipt['build_recipe']['version'] = VERSION
    migrate_step_ids(receipt['build_recipe'])
    receipt['package_upgrade'] = dict(version=2, registered_smart_areas=added,
        removed_duplicate_smart_geometry=removed, skalitz_outer_gates=gate_report, runtime_verified=False)
    if marker_report is not None:
        receipt['quest_markers'] = marker_report
    if 'baking' in receipt:
        receipt['baking']['levels'] = [r for r in receipt['baking'].get('levels', []) if r.get('level') == 'kcd1_travel']
    receipt['files'] = {n:h for n,h in receipt['files'].items() if not n.startswith('Data/Levels/kcd1_rataje/')}
    for name in (travel, level, runtime):
        receipt['files'][name] = digest(package/name)
    pending = package/(RECEIPT+'.pending')
    pending.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    pending.replace(package/RECEIPT)
    if target is not None:
        from campaign_menu_stage import add_to_package
        add_to_package(target, package)
    receipt = verify_package(package)
    return receipt
