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
    steps = report.get('steps', [])
    ids = [s.get('id') for s in steps if s.get('id')]
    missing = {'gm_guard_interaction', 'quest-introductions'} - set(ids)
    if ids == [s for s in STAGE_IDS if s not in missing]:
        for identity in STAGE_IDS:
            if identity not in missing: continue
            following = set(STAGE_IDS[STAGE_IDS.index(identity)+1:])
            index = next(i for i,s in enumerate(steps) if s.get('id') in following)
            steps.insert(index,dict(id=identity,name=identity+' (migration)'))


def eligible(receipt):
    from build_recipe import VERSION, require_current
    from campaign_start_policy import POLICY
    candidate = copy.deepcopy(receipt)
    report = candidate.get('build_recipe', {})
    if report.get('version') not in range(3, VERSION + 1):
        raise ValueError('Saved gameplay recipe is not supported for migration')
    report['version'] = VERSION
    migrate_step_ids(report)
    report.setdefault('content_validation', {})['start_policy'] = POLICY
    candidate['single_playable_world'] = True
    from character_person_appearance import APPEARANCE_POLICY_VERSION
    candidate['source_appearance_upgrade'] = dict(version=APPEARANCE_POLICY_VERSION,
        mode='source_converter', equipment_validation=True)
    # Eligibility describes work this migration can perform, not proof it has
    # already run. The real receipt is written only after conversion succeeds.
    from setup_gameplay import STATIC_WORLD_PROFILES
    from static_navigation_profiles import VERSION as NAVIGATION_VERSION
    candidate['static_navigation_profiles'] = dict(version=NAVIGATION_VERSION,
        mode='materialized_static_profiles', profiles=[dict(profile=p) for p in STATIC_WORLD_PROFILES])
    from animal_population import VERSION as ANIMAL_VERSION
    from setup_animal_population import MODE as ANIMAL_MODE
    candidate['animal_population'] = dict(version=ANIMAL_VERSION, mode=ANIMAL_MODE,
        profiles=list(STATIC_WORLD_PROFILES), scheduler_validation=True)
    from setup_profile_population import VERSION as POPULATION_VERSION, MODE as POPULATION_MODE
    candidate['profile_population'] = dict(version=POPULATION_VERSION, mode=POPULATION_MODE,
        profiles=list(STATIC_WORLD_PROFILES), scheduler_validation=True)
    from terrain_object_tree import VERSION as PLACEMENT_VERSION
    candidate['surface_placement'] = dict(version=PLACEMENT_VERSION)
    from setup_crime_services import VERSION as CRIME_VERSION
    candidate['crime_services'] = dict(version=CRIME_VERSION,status='bound_native_services')
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


def finalize(package, *, source=None, target=None, repair_roots=(), notify=lambda *a: None, cancelled=lambda: False):
    """Called only on a private build or the transactional resume staging copy."""
    from campaign_start_policy import gate_start
    from region_travel_companions import remove_horse_recovery
    from smart_area_registration import reconcile
    from setup_gameplay import PROJECT, OPENING, validate
    from build_recipe import VERSION
    package = Path(package).resolve()
    # Saved jobs pass the game folders from job.json as strings.
    source, target = (None if p is None else Path(p) for p in (source, target))
    receipt = verify_package(package)
    eligible(receipt)
    if receipt.get('travel_bridge', {}).get('destination') != 'kcd1_travel':
        raise ValueError('Expected the established playable travel world')
    # Resolve stale derived metadata before spending time on unrelated builders.
    # The caller owns the private staging transaction and supplies its cache roots.
    from setup_section_repair import repair, stale_sections
    if stale_sections(package):
        receipt = repair(package, repair_roots=repair_roots, notify=notify, cancelled=cancelled)
    # Shared by clean finalization and both resume paths. Repair verified pickup
    # registrations before another stage can replace archives or their hashes.
    from setup_female_clothing import upgrade as repair_female_clothing
    files, report = repair_female_clothing(package, receipt)
    receipt['files'].update(files); receipt['female_clothing'] = report
    # Recipe 52 already contains the complete world, schedules and training.
    # Reuse lighting and terrain; refresh outdated appearances separately.
    if (receipt.get('build_recipe',{}).get('version') in range(52, VERSION + 1)
            and receipt['build_recipe'].get('status') == 'complete'
            and receipt.get('quest_training') and (package/'quest-training.json').exists()):
        from designer_collision_repair import upgrade as repair_collision
        from training_presentation_repair import upgrade as repair_presentation
        from setup_training_upgrade import validate as validate_training
        from setup_quest_invitation import validate as validate_invitation
        notify('Upgrade saved gameplay and schedules', 0, 1, 'Reuse the verified world and repair its gameplay dependencies')
        for key, operation, game in (('designer_collision_repair', repair_collision, source),
                                     ('training_presentation_repair', repair_presentation, target)):
            files, report = operation(package, game)
            receipt['files'].update(files); receipt[key] = report
        from setup_opening_clock import upgrade as repair_clock
        files, report = repair_clock(package, source)
        receipt['files'].update(files); receipt['opening_clock'] = report
        from well_collision_repair import upgrade as repair_wells
        files, report = repair_wells(package, source, target)
        receipt['files'].update(files); receipt['well_collision_repair'] = report
        from market_visual_repair import upgrade as repair_market
        files, report = repair_market(package, source, target)
        receipt['files'].update(files); receipt['market_visual_repair'] = report
        from setup_static_gate_repair import upgrade as repair_static_gates
        files, report = repair_static_gates(package, source, target)
        receipt['files'].update(files); receipt['static_gate_repair'] = report
        from training_handoff_repair import upgrade as repair_training_handoff
        files, report = repair_training_handoff(package, source, target)
        receipt['files'].update(files); receipt['training_handoff_repair'] = report
        from training_entry_repair import upgrade as repair_training_entry
        files, report = repair_training_entry(package, source, target)
        receipt['files'].update(files); receipt['training_entry_repair'] = report
        # Completed worlds still need current source-introduction semantics;
        # the fast path must not leave an old worker behind after an upgrade.
        from setup_quest_introductions import upgrade as repair_introductions
        files, report = repair_introductions(package, source, target)
        receipt['files'].update(files); receipt['quest_introductions'] = report
        from setup_pub_cleaning import upgrade as upgrade_pub_work
        files, report = upgrade_pub_work(package, source, target)
        receipt['files'].update(files); receipt['source_pub_work'] = report
        from setup_gambler_work import upgrade as upgrade_gambler_work
        files, report = upgrade_gambler_work(package, source, target)
        receipt['files'].update(files); receipt['source_gambler_work'] = report
        from setup_work_coverage import upgrade as audit_work_ownership
        files, report = audit_work_ownership(package, source)
        receipt['files'].update(files); receipt['source_work_coverage'] = report
        from setup_work_speech import upgrade as upgrade_work_speech
        files, report = upgrade_work_speech(package, source, target)
        receipt['files'].update(files); receipt['source_work_speech'] = report
        from setup_world_encounters import upgrade as upgrade_encounters
        files, report = upgrade_encounters(package, source, target)
        receipt['files'].update(files); receipt['world_encounters'] = report
        from setup_crime_services import upgrade as upgrade_crime
        files, report = upgrade_crime(package, source, target, notify=notify)
        receipt['files'].update(files); receipt['crime_services'] = report
        from world_speech_audio import upgrade as upgrade_world_speech
        files, report = upgrade_world_speech(package, target, notify)
        receipt['files'].update(files); receipt['world_speech_audio'] = report
        from setup_source_appearances import apply as upgrade_appearances
        upgrade_appearances(package, receipt, source, target, notify, cancelled)
        from world_actor_contracts import upgrade as upgrade_actor_contracts
        files, report = upgrade_actor_contracts(package, target)
        receipt['files'].update(files); receipt['world_actor_contracts'] = report
        receipt['quest_training'] = validate_training(package)
        receipt['quest_invitation'] = validate_invitation(package)
        receipt['build_recipe']['content_validation'] = validate(package)
        receipt['build_recipe']['version'] = VERSION
        from lod_ratio_metadata import upgrade_package as validate_lod
        validate_lod(package, validate_only=True)
        pending=package/(RECEIPT+'.pending')
        pending.write_text(json.dumps(receipt,indent=2),encoding='utf-8');pending.replace(package/RECEIPT)
        notify('Upgrade saved gameplay and schedules', 1, 1, 'Saved-build upgrade validated')
        return verify_package(package)
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
            from campaign_sources import RetailSourceReader
            with RetailSourceReader(source) as reader:
                model, _ = import_quest(Path(source), QUEST, source_reader=reader)
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
    level = 'Data/Levels/kcd1_travel/level.pak'
    if format_level_changes:
        rewrite(package, level, format_level_changes)
    with zipfile.ZipFile(package/level) as z:
        from world_environment import retire_converted_audio
        mission, audio_report = retire_converted_audio(read(z, 'objects_mission0.xml'), source)
        receipt['ambient_audio_upgrade'] = audio_report
        from world_lighting import disable_global_probe_fog
        mission, receipt['global_probe_fog'] = disable_global_probe_fog(mission)
        metadata, added = reconcile(read(z, 'whdata_0'), mission)
        from area_geometry_contract import normalize
        missions = [mission if n.lower() == 'objects_mission0.xml' else read(z, n)
                    for n in z.namelist() if n.lower() == 'objects_mission0.xml'
                    or n.lower().startswith('layers/') and n.lower().endswith('.xml')]
        geometry, removed = normalize(read(z, 'triggerareas.fubar'), missions)
        normalize(geometry, missions, validate_only=True)
        from skalitz_outer_gates import normalize as correct_gates
        gates, gate_report = correct_gates({n:read(z,n) for n in ('terrain/hlods.dat','terrain/hlods.xml')})
    rewrite(package, level, {'objects_mission0.xml': mission, 'whdata_0': metadata, 'triggerareas.fubar': geometry, **gates})
    from door_animation_upgrade import upgrade as upgrade_door_animations
    door_files, door_report = upgrade_door_animations(package)
    receipt['files'].update(door_files)
    receipt['door_animation_registration'] = door_report
    from designer_collision_repair import upgrade as repair_designer_collision
    collision_files, collision_report = repair_designer_collision(package, source)
    receipt['files'].update(collision_files)
    receipt['designer_collision_repair'] = collision_report
    from static_navigation_profiles import upgrade as upgrade_static_navigation
    from setup_gameplay import STATIC_WORLD_PROFILES
    navigation_files, navigation_report = upgrade_static_navigation(
        package, source, target, STATIC_WORLD_PROFILES)
    receipt['files'].update(navigation_files)
    receipt['static_navigation_profiles'] = navigation_report
    from setup_profile_population import upgrade as upgrade_population
    population_files, population_report = upgrade_population(
        package, source, target, STATIC_WORLD_PROFILES, notify, cancelled)
    receipt['files'].update(population_files)
    receipt['profile_population'] = population_report
    from setup_pub_cleaning import upgrade as upgrade_pub_work
    pub_files, pub_report = upgrade_pub_work(package, source, target)
    receipt['files'].update(pub_files); receipt['source_pub_work'] = pub_report
    from setup_gambler_work import upgrade as upgrade_gambler_work
    files, report = upgrade_gambler_work(package, source, target)
    receipt['files'].update(files); receipt['source_gambler_work'] = report
    from setup_work_coverage import upgrade as audit_work_ownership
    files, report = audit_work_ownership(package, source)
    receipt['files'].update(files); receipt['source_work_coverage'] = report
    from character_water_work import upgrade_well_bodies
    well_files, well_report = upgrade_well_bodies(package, source, target)
    receipt['files'].update(well_files)
    receipt['well_bodies'] = well_report
    from well_collision_repair import upgrade as repair_wells
    well_files, well_report = repair_wells(package, source, target)
    receipt['files'].update(well_files); receipt['well_collision_repair'] = well_report
    from market_visual_repair import upgrade as repair_market
    market_files, market_report = repair_market(package, source, target)
    receipt['files'].update(market_files); receipt['market_visual_repair'] = market_report
    from forge_loft import upgrade as upgrade_forge_loft
    notify('Furnish the forge loft', 0, 1, 'Ladder, assigned beds and personal storage')
    loft_files, loft_report = upgrade_forge_loft(package, target)
    receipt['files'].update(loft_files)
    receipt['forge_loft'] = loft_report
    from procedural_lights import upgrade as upgrade_procedural_lights
    notify('Register candles and fire lights', 0, 1, 'Native flames and lighting at source placements')
    light_files, light_report = upgrade_procedural_lights(package, source, target)
    receipt['files'].update(light_files)
    receipt['procedural_lights'] = light_report
    from setup_animal_population import upgrade as upgrade_animals
    animal_files, animal_report = upgrade_animals(
        package, source, target, STATIC_WORLD_PROFILES, notify, cancelled)
    receipt['files'].update(animal_files)
    receipt['animal_population'] = animal_report
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
    from hlod_spatial_metadata import upgrade_package as upgrade_spatial_metadata
    spatial_files, spatial_report = upgrade_spatial_metadata(package)
    receipt['files'].update(spatial_files)
    receipt['vegetation_spatial_metadata'] = spatial_report
    receipt['build_recipe']['streaming_validation']['vegetation_grid_version'] = spatial_report['version']
    from lod_ratio_metadata import upgrade_package as upgrade_lod_ratios
    ratio_files, ratio_report = upgrade_lod_ratios(package, repair_roots=repair_roots)
    receipt['files'].update(ratio_files)
    receipt['lod_ratio_metadata'] = ratio_report
    receipt['build_recipe']['streaming_validation']['lod_ratio_version'] = ratio_report['version']
    from terrain_surface_placement import upgrade_package as upgrade_surface_placement
    notify('Applying KCD2 surface-tree placement', 0, 0, '')  # setup progress: label, current, total, detail
    placement_files, placement_report = upgrade_surface_placement(package)
    receipt['files'].update(placement_files)
    receipt['surface_placement'] = placement_report
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
    from localization_registry import repair_package as repair_localization
    localization_files, localization_report = repair_localization(package)
    receipt['files'].update(localization_files)
    if localization_report: receipt['localization_registry'] = localization_report
    from setup_quest_starts import upgrade as upgrade_quest_starts
    start_files, start_report = upgrade_quest_starts(package, source)
    receipt['files'].update(start_files)
    receipt['quest_start_placements'] = start_report
    from setup_quest_introductions import upgrade as upgrade_introductions
    intro_files, intro_report = upgrade_introductions(package, source, target)
    receipt['files'].update(intro_files)
    receipt['quest_introductions'] = intro_report
    from setup_quest_invitation import upgrade as upgrade_invitation
    invitation_files, invitation_report = upgrade_invitation(package, source, target)
    receipt['files'].update(invitation_files)
    receipt['quest_invitation'] = invitation_report
    from setup_training_upgrade import upgrade as upgrade_training
    training_files, training_report = upgrade_training(package, source, target)
    receipt['files'].update(training_files)
    receipt['quest_training'] = training_report
    from training_presentation_repair import upgrade as repair_training_presentation
    presentation_files, presentation_report = repair_training_presentation(package, target)
    receipt['files'].update(presentation_files)
    receipt['training_presentation_repair'] = presentation_report
    from training_handoff_repair import upgrade as repair_training_handoff
    files, report = repair_training_handoff(package, source, target)
    receipt['files'].update(files); receipt['training_handoff_repair'] = report
    from training_entry_repair import upgrade as repair_training_entry
    files, report = repair_training_entry(package, source, target)
    receipt['files'].update(files); receipt['training_entry_repair'] = report
    from setup_training_upgrade import validate as validate_training
    receipt['quest_training'] = validate_training(package)
    from setup_static_gate_repair import upgrade as repair_static_gates
    files, report = repair_static_gates(package, source, target)
    receipt['files'].update(files); receipt['static_gate_repair'] = report
    # The training stage takes ownership of departure availability and adds a
    # behavior to its shared host; retain the updated invitation provenance.
    from setup_quest_invitation import validate as validate_invitation
    receipt['quest_invitation'] = validate_invitation(package)
    from setup_starting_inventory import upgrade as upgrade_starting_inventory
    inventory_files, inventory_report = upgrade_starting_inventory(package, target)
    receipt['files'].update(inventory_files)
    receipt['starting_inventory'] = inventory_report
    from setup_opening_clock import upgrade as upgrade_opening_clock
    clock_files, clock_report = upgrade_opening_clock(package, source)
    receipt['files'].update(clock_files)
    receipt['opening_clock'] = clock_report
    # After every step that registers people and shops: give each shopkeeper the
    # native haggle and shop-ending roles, without which a confirmed trade does nothing.
    from shop_roles import keepers as shop_keepers, add_haggle_roles
    with zipfile.ZipFile(package/'Data/Levels/kcd1_travel/level.pak') as level_archive:
        people = shop_keepers(read(level_archive, 'objects_mission0.xml'))
    with zipfile.ZipFile(package/travel) as z:
        role_files = {n: read(z, n) for n in z.namelist() if n.lower().startswith('libs/storm/roles/')}
    role_changes, receipt['shop_roles'] = add_haggle_roles(role_files, people)
    if role_changes:
        rewrite(package, travel, role_changes)
    # Speech binds the final actor, staffed-shop and dialogue registrations.
    # Apply it after all person/service upgrades in both build paths.
    from setup_work_speech import upgrade as upgrade_work_speech
    speech_files, speech_report = upgrade_work_speech(package, source, target)
    receipt['files'].update(speech_files); receipt['source_work_speech'] = speech_report
    from setup_world_encounters import upgrade as upgrade_encounters
    encounter_files, encounter_report = upgrade_encounters(package, source, target)
    receipt['files'].update(encounter_files); receipt['world_encounters'] = encounter_report
    from setup_crime_services import upgrade as upgrade_crime
    crime_files, crime_report = upgrade_crime(package, source, target, notify=notify)
    receipt['files'].update(crime_files); receipt['crime_services'] = crime_report
    # Resolve recordings from the final graph closure, including controllers
    # added by encounters/crime. The completed-build path must do this too.
    from world_speech_audio import upgrade as upgrade_world_speech
    speech_files, speech_report = upgrade_world_speech(package, target, notify)
    receipt['files'].update(speech_files); receipt['world_speech_audio'] = speech_report
    from setup_source_appearances import apply as upgrade_appearances
    upgrade_appearances(package, receipt, source, target, notify, cancelled)
    from world_actor_contracts import upgrade as upgrade_actor_contracts
    actor_files, actor_report = upgrade_actor_contracts(package, target)
    receipt['files'].update(actor_files); receipt['world_actor_contracts'] = actor_report
    receipt['build_recipe']['content_validation'] = validate(package)
    from setup_pak_limits import split_assets, validate as validate_pak_limits
    receipt['files'].update(split_assets(package))
    validate_pak_limits(package)
    receipt['build_recipe']['version'] = VERSION
    migrate_step_ids(receipt['build_recipe'])
    receipt['package_upgrade'] = dict(version=2, registered_smart_areas=added,
        removed_duplicate_smart_geometry=removed, skalitz_outer_gates=gate_report, runtime_verified=False)
    if marker_report is not None:
        receipt['quest_markers'] = marker_report
    if 'baking' in receipt:
        receipt['baking']['levels'] = [r for r in receipt['baking'].get('levels', []) if r.get('level') == 'kcd1_travel']
    from setup_baking import upgrade_baked_exposure
    for name in upgrade_baked_exposure(package, receipt, target):
        receipt['files'][name] = digest(package/name)
    receipt['files'] ={n:h for n,h in receipt['files'].items() if not n.startswith('Data/Levels/kcd1_rataje/')}
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
