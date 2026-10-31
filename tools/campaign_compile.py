"""Build the campaign conversion closure from a retail New Game entry point.

There is no named opening quest or scripted spawn in this pipeline. Discovery
starts at the source UI dispatch and follows world links and script references.
Partial conversions are written outside Data and cannot pass the runtime gate.
"""
import argparse
from collections import Counter, deque
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from campaign_bindings import build_bindings, registration_errors
from campaign_behavior import convert_behaviors, native_catalog
from campaign_character_dependencies import CharacterPartAdapter
from campaign_ai_types import AITypeSource, AIEnumAdapter, AIStructureAdapter
from campaign_link_types import LinkTypeAdapter
from campaign_smart_areas import SmartAreaAdapter, area_bindings
from campaign_item_names import registered_item_name
from campaign_ai_registration import (NativeAIRegistry, MailboxAdapter, BrainAdapter,
    SubbrainAdapter, ConvertedTreeAdapter, SmartEntityAdapter, controller_definition, quest_controller_brains)
from campaign_doors import NativeDoorCatalog, AnimatedDoorAssetAdapter, DoorEntityConversion
from campaign_dependencies import DependencyImporter, import_binding_dependencies
from campaign_buffs import BuffAdapter, native_contract as native_buff_contract
from campaign_dependency_adapters import (EntityAdapter, InventoryAdapter, ItemTextAdapter,
                                          LazyAdapter, SoulAdapter, SourceTables, StaticAssetAdapter)
from campaign_linker import build_links, link_entities, entity_index
from campaign_entity_links import native_guid
from entity_visuals import initial_layer
from campaign_quest_bridge import convert_state_operations, controller_scopes, scoped_operation
from campaign_quest_graph import emit_project, identifier, validate_model, xml
from campaign_registration import assemble_registrations
from campaign_work_helpers import WorkHelperAdapter
from campaign_profiles import WorldProfileAdapter, WorldLayerAdapter, ProfileLifecycleAdapter, STREAMING_SOURCE
from campaign_navigation import NavigationSource, BaseNavigationAdapter, ProfileNavigationAdapter
from campaign_sources import RetailSourceReader
from quest_import import import_quest, literal, walk
from setup_progress import progress
from game_paths import GameLibrary
from upgrade_map import read
from world_item_definitions import native_items, native_item_registry, WorldItemAdapter
from world_item_clothing import ClothingAdapter
from world_item_documents import DocumentImageAdapter
from character_person import native_xml
from campaign_dialogue import TABLES as DIALOGUE_TABLES
from campaign_quest_dialogues import discover as discover_dialogues, import_participants
from campaign_combat import assemble as assemble_combat


def quest_dependencies(model):
    """Discover references, not which branches should run on New Game."""
    result = []
    scopes = controller_scopes(model)
    for document, record in model['behavior_documents'].items():
        for tree, body in record['trees'].items():
            for index, node in enumerate(walk(body['root'])):
                if node['op'] not in ('SetQuest', 'SetQuestObjective', 'QuestCondition', 'QuestObjectiveCondition', 'QuestObjectiveGate'):
                    continue
                attrs = scoped_operation(node, scopes.get((document.lower(), tree)))['attributes']
                value = literal(attrs.get('quest', attrs.get('questName', '')))
                # Empty/current-owner references are resolved at their call site.
                if value in ('', '$this.name', 'this.name', model['quest']):
                    continue
                result.append(dict(quest=value, dynamic=not bool(re.fullmatch(r'[A-Za-z_]\w*', value)),
                                   site=dict(document=document, tree=tree, preorder_index=index,
                                             operation=node['op'], arguments=attrs)))
    from campaign_quest_dependencies import objective_dependencies
    result.extend(objective_dependencies(model))
    return result


def wire_bridge(files, project, bridge_files, report, instance_name='source_quest_bridge'):
    """Connect every generated signal/state port to a declared quest port."""
    project_path = project + '.xml'
    root = ET.fromstring(files[project_path])
    host = root.find('./Skald/Project')
    declarations = host.find('Definitions')
    nodes = host.find('Nodes')
    module_path = next(n for n in bridge_files if n.startswith('Quests/'))
    module_root = ET.fromstring(bridge_files[module_path])
    module = module_root.find('./Skald/Module')
    ET.SubElement(declarations, 'Definition', File=module_path.removeprefix('Quests/'))
    identifier(instance_name)
    instance = ET.SubElement(nodes, module.get('Name'), Name=instance_name)
    for additional in report.get('profile_modules', []) + report.get('auxiliary_modules', []):
        if additional['file'] not in bridge_files:
            raise ValueError('Missing converted profile module')
        ET.SubElement(declarations, 'Definition', File=additional['file'].removeprefix('Quests/'))
        ET.SubElement(nodes, additional['name'], Name=additional['instance'])
    quest_ports = {}
    for q in report['wiring']:
        name = q['quest']
        if name not in quest_ports:
            quest_root = ET.fromstring(files[project + '/' + name + '.xml'])
            quest_ports[name] = {p.get('Name'): (p.get('Direction'), p.get('Type'))
                                 for p in quest_root.findall('./Skald/Quest/Ports/Port')}
        expected = ('In', 'trigger') if q['direction'] == 'action' else ('Out', 'bool')
        if quest_ports[name].get(q['quest_port']) != expected:
            raise ValueError('Bridge refers to an undeclared quest port: ' + name + '.' + q['quest_port'])
        if q['direction'] == 'action':
            target = next(n for n in nodes if n.get('Name') == name)
            ET.SubElement(target, 'Edge', From=instance_name + '.' + q['bridge_port'], To=q['quest_port'])
        else:
            ET.SubElement(instance, 'Edge', From=name + '.' + q['quest_port'], To=q['bridge_port'])
    files[project_path] = xml(root)


def compile_campaign(source, target, level, output, project, entry_action='Libs/UI/UIActions/MM_NewGame.xml', registered=None,
                     engine_enum_evidence=None, baseline_objects=None, baseline_level=None, actor_roots=None):
    identifier(project)
    source, target, output = Path(source), Path(target), Path(output)
    if baseline_objects is not None and baseline_level is not None:
        raise ValueError('Choose either an object baseline or a complete level baseline')
    if output.exists():
        raise FileExistsError('Choose a new campaign conversion directory')
    if not (target / 'Data/Scripts.pak').is_file():
        raise ValueError('The installed retail target game is required')
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack, RetailSourceReader(source) as resolver, tempfile.TemporaryDirectory(prefix='.campaign-convert-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'stage'
        stage.mkdir()
        links = build_links(source, level, entry_action, source_reader=resolver)
        actor_roots = actor_roots or {}
        seeds = sorted({r['database_row']['quest_name'] for r in links['quests']} | set(actor_roots))
        if not seeds:
            raise ValueError('The source New Game link closure contains no quest registrations')
        table = resolver(source, {'quest': ('Tables.pak', 'Libs/Tables/quest/quest.xml')})['quest']
        known = {r.get('quest_name') for r in ET.fromstring(table['data']).findall('./table/rows/row')}
        tables = SourceTables(source, resolver)
        actor_dialogues = None
        if actor_roots:
            from campaign_actor_dialogues import branch_catalog, quest_dependencies as dialogue_quest_dependencies
            selected = sorted({name for names in actor_roots.values() for name in names})
            soul_ids = [tables.row('rpg/soul', 'soul_name', name)['soul_id'] for name in selected]
            actor_tables = {n: tables.get('text/' + n)['rows'] for n in (*DIALOGUE_TABLES, 'dialogue_functions')}
            actor_dialogues = branch_catalog(actor_tables['v_branch'], actor_tables['topictorole'],
                tables.get('rpg/soul2role')['rows'], tables.get('rpg/role')['rows'],
                tables.get('rpg/metarole')['rows'], soul_ids)
            actor_dialogues['quest_dependencies'] = dialogue_quest_dependencies(
                actor_tables, actor_dialogues, tables.get('quest/quest')['rows'])
            seeds = sorted(set(seeds) | {name for names in actor_dialogues['quest_dependencies'].values() for name in names})
        native_ai = NativeAIRegistry.load(target, project)
        from character_person_ai import BRAIN_ADAPTERS
        native_person_brains = {}
        for source_name, target_name in BRAIN_ADAPTERS.items():
            matches = [r for r in native_ai.tables['brain']['rows'] if r['brain_name'] == target_name]
            if len(matches) != 1:
                raise ValueError('Missing or ambiguous native person brain: ' + target_name)
            native_person_brains[source_name] = dict(matches[0])
        pending = deque(seeds)
        models, attempted, dependencies, failures = {}, set(), [], []
        while pending:
            name = pending.popleft()
            if name in attempted:
                continue
            attempted.add(name)
            progress('Converting campaign dependencies', len(attempted), len(attempted) + len(pending), name)
            try:
                definition = controller_definition(tables, tables.row('quest/quest', 'quest_name', name))
                bootstrap = sorted({p['file'] for p in definition['entry_points']}) if definition else []
                model, _ = import_quest(source, name, bootstrap=bootstrap, source_reader=resolver,
                                       require_behavior=False, profile_level=level,
                                       native_person_brains=native_person_brains, actor_roots=actor_roots.get(name, ()))
                model['controller_registration'] = definition
            except (ValueError, KeyError) as error:
                failures.append(dict(kind='quest_import', quest=name, reason=str(error)))
                continue
            models[name] = model
            path = stage / 'source-ir' / (name + '.json')
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(model, indent=2), encoding='utf-8')
            for dependency in quest_dependencies(model):
                dependencies.append(dict(owner=name, **dependency))
                if dependency['dynamic']:
                    continue
                if dependency['quest'] not in known:
                    failures.append(dict(kind='unknown_source_quest', owner=name, **dependency))
                elif dependency['quest'] not in attempted:
                    pending.append(dependency['quest'])
        with zipfile.ZipFile(source / 'Data/Levels' / level / 'level.pak') as archive:
            souls = ET.fromstring(read(archive, 'whdata_0'))
            profiles_data = read(archive, 'whdata_1')
            source_profiles = ET.fromstring(profiles_data)
            area_data = read(archive, 'triggerareas.fubar')
            # Follow controllers discovered through behavior references too.
            # They need not be directly linked from the New Game controller.
            documents = {n: read(archive, n) for n in archive.namelist()
                         if n.lower() == 'objects_mission0.xml' or
                         (n.lower().startswith('layers/') and n.lower().endswith('.xml'))}
            links['world'] = link_entities(documents, links['entry']['dispatch']['entity_name'],
                [m['tables']['quest']['rows'][0].get('smart_object') or m['quest'] for m in models.values()])
        bindings, missing = {}, {}
        for name, model in models.items():
            try:
                plan = build_bindings(model, links['world'], souls)
            except ValueError as error:
                failures.append(dict(kind='quest_bindings', quest=name, reason=str(error)))
                continue
            bindings[name] = plan
        # The importer owns registration. Callers need not prepare a hand-made
        # list of actors/items/areas before a quest can ask for those records.
        paths = Path(temporary) / 'paths.json'
        paths.write_text(json.dumps(dict(schema=1, kcd1=str(source.resolve()), kcd2=str(target.resolve()),
                                        build=str(stage.resolve()))), encoding='utf-8')
        cache = Path(temporary) / 'asset-cache'
        cache.mkdir()
        ai_types = AITypeSource.load(source, resolver, project, Path(temporary) / 'type-cache')
        link_types = LinkTypeAdapter.load(source, target, resolver, project, Path(temporary) / 'link-cache')
        export_version = int.from_bytes(hashlib.sha256(area_data + project.encode()).digest()[:4], 'little') or 1
        if baseline_level is not None:
            from campaign_trigger_areas import read_areas
            with zipfile.ZipFile(baseline_level) as baseline_archive:
                native_areas = read_areas(read(baseline_archive, 'triggerareas.fubar'))
            if native_areas['version'] != 3:
                raise ValueError('Baseline level must use native trigger geometry')
            export_version = native_areas['export_version']
        assets = LazyAdapter(lambda: StaticAssetAdapter(stack, GameLibrary(paths), project, cache))
        native_doors = NativeDoorCatalog(target, stack)
        animated_doors = AnimatedDoorAssetAdapter(assets, native_doors, project)
        door_entities = DoorEntityConversion(tables, animated_doors, native_doors)
        all_entities, _ = entity_index(documents)
        from campaign_person_registration import PersonRegistration
        person_registration = PersonRegistration(source, target, tables,
            [e for blob in documents.values() for e in ET.fromstring(blob).iter('Entity')],
            stage / 'person-assets', project,
            item_name=lambda identity: registered_item_name(identity, native_item_definitions, importer.files))
        from campaign_work_dependencies import for_models, templates, merge_world, import_all as import_work_graphs
        work_closures = for_models(models.values(), dict(entities=all_entities, links=[]))
        links['world'] = merge_world(links['world'], all_entities, work_closures)
        area_ids = area_bindings(bindings, dict(entities=all_entities), models.values())
        for guid, identities in templates(work_closures, 'SmartAreaShape', 'guidSmartAreaTemplate').items():
            for identity in identities:
                if identity not in area_ids.setdefault(guid, []):
                    area_ids[guid].append(identity)
        work_object_ids = templates(work_closures, 'SmartObjectHolder', 'guidSmartObjectType')
        for guid, identities in templates(work_closures, 'ItemSlot', 'guidSmartObjectType').items():
            work_object_ids.setdefault(guid, []).extend(identities)
        navigation_source = NavigationSource(
            stack.enter_context(zipfile.ZipFile(source / 'Data/Levels' / level / 'recast.pak')),
            stack.enter_context(zipfile.ZipFile(target / 'Data/Levels/trosecko/recast.pak')),
            stack.enter_context(zipfile.ZipFile(source / 'Data/Levels' / level / 'level.pak')),
            stack.enter_context(zipfile.ZipFile(target / 'Data/Levels/trosecko/level.pak')),
            source_profiles, project, level)
        controller_ids = {}
        for model in models.values():
            definition = model.get('controller_registration')
            if definition:
                smart = definition['smart_object']
                controller_ids.setdefault(smart['so_smart_object_name'], []).append(model['quest'] + '|' + smart['so_smart_object_id'])
        native_item_definitions = native_items(target)
        registrations = dict(registered or {})
        existing_items = registrations.get('items', {})
        if not isinstance(existing_items, dict):
            existing_items = {i:i for i in existing_items}
        registrations['items'] = dict(native_item_registry(native_item_definitions), **existing_items)
        def item_adapter():
            from campaign_item_types import native_tool_types
            # Use the same wearable/weapon/document conversion as world loot.
            # The former basic adapter rejected clothing requested by quests
            # and initial outfits even though its upgrader already existed.
            with zipfile.ZipFile(target / 'Data/Tables.pak') as archive:
                weapons = native_xml(read(archive, 'Libs/Tables/item/weapon_class.xml'))
                components = native_xml(read(archive, 'Libs/Tables/Character/CharacterComponent.xml'))
            return WorldItemAdapter(tables, project, native_item_definitions,
                                    {r.get('id'):r.get('name') for r in weapons[0]}, components,
                                    native_tool_types=native_tool_types(target))
        catalog = native_catalog(target)
        importer = DependencyImporter(dict(
            entities=EntityAdapter(dict(entities=all_entities), souls, area_data, export_version, door_entities, controller_ids,
                area_ids, navigation=navigation_source,
                smart_objects=work_object_ids),
            items=LazyAdapter(item_adapter), inventories=InventoryAdapter(tables, project,
                lambda identity: registered_item_name(identity, native_item_definitions, importer.files)),
            clothing=LazyAdapter(lambda: ClothingAdapter(tables, assets.materialize(), target, stack)),
            document_images=LazyAdapter(lambda: DocumentImageAdapter(source, resolver, project)),
            buffs=LazyAdapter(lambda: BuffAdapter(tables, native_buff_contract(target), project)),
            souls=SoulAdapter(tables, souls, person_registration),
            work_helpers=LazyAdapter(lambda: WorkHelperAdapter.load(source, target)),
            character_parts=CharacterPartAdapter(tables, target, stack, assets, project),
            ai_types=AIStructureAdapter(ai_types, catalog['variable_types']), ai_enums=AIEnumAdapter(ai_types), link_types=link_types,
            world_profiles=WorldProfileAdapter(source_profiles, project,
                [dict(archive='Data/Levels/' + level + '/level.pak', entry='whdata_1',
                      sha256=hashlib.sha256(profiles_data).hexdigest())]),
            navigation_base=BaseNavigationAdapter(navigation_source),
            profile_navigation=ProfileNavigationAdapter(navigation_source),
            mailboxes=MailboxAdapter(tables, native_ai),
            animated_props=animated_doors,
            strings=LazyAdapter(lambda: ItemTextAdapter(source, project, 'English')),
            person_names=LazyAdapter(lambda: ItemTextAdapter(source, project, 'English', 'text_ui_soul.xml')),
            assets=assets), registrations)
        importer.adapters['world_layers'] = WorldLayerAdapter(documents, project, importer.files)
        if engine_enum_evidence is not None:
            # Explicit developer metadata input; normal setup does not inspect
            # binaries or import optional disassembly dependencies. Re-extract
            # to validate both values and provenance against this installation.
            from campaign_engine_enums import extract_engine_enums
            evidence = json.loads(Path(engine_enum_evidence).read_text(encoding='utf-8'))
            recovered = extract_engine_enums(source / 'Bin/Win64/WHGame.dll', ['gameVersion'])
            supplied = [d for d in evidence['enum_declarations'] if d['name'] == 'gameVersion']
            if evidence['sha256'] != recovered['sha256'] or supplied != recovered['enum_declarations']:
                raise ValueError('Engine enum evidence does not match the installed source engine')
            importer.adapters['profile_lifecycle'] = ProfileLifecycleAdapter(source_profiles, project, recovered)
        eligible = []
        for name, model in models.items():
            try:
                validate_model(model)
            except ValueError as error:
                failures.append(dict(kind='native_quest_emission', quest=name, reason=str(error)))
            else:
                eligible.append(model)
        # A broken authoring record must be reported at its own quest, without
        # discarding the valid declarations for every other referenced quest.
        # It still blocks campaign readiness and calls into it stay unresolved.
        bridge_files, bridge = convert_state_operations(eligible, project)
        with zipfile.ZipFile(target / 'Data/Scripts.pak') as native_scripts:
            profile_streaming = read(native_scripts, STREAMING_SOURCE)
        behavior_files, behavior_bridge_files, behavior = convert_behaviors(
            eligible, project + '_behavior', catalog, importer.registered, importer, profile_streaming)
        importer.adapters.update(behavior_trees=ConvertedTreeAdapter(behavior, behavior_files),
            subbrains=SubbrainAdapter(tables, native_ai), brains=BrainAdapter(tables, native_ai, ai_types),
            smart_entities=SmartEntityAdapter(tables, native_ai),
            smart_areas=SmartAreaAdapter(tables, native_ai))
        controllers = quest_controller_brains(eligible, tables, importer)
        from campaign_profile_work import import_dependencies as import_population_dependencies
        population_imports = import_population_dependencies(eligible, importer)
        work_graph_imports = import_work_graphs(work_closures, importer)
        progress('Importing quest dependencies', 0, len(bindings), 'Actors, inventory, items, models and trigger areas')
        import_binding_dependencies(bindings, importer)
        dialogue_tables = {n: tables.get('text/' + n)['rows'] for n in (*DIALOGUE_TABLES, 'dialogue_functions')}
        dialogues = {}
        for name, model in models.items():
            closure = discover_dialogues(dialogue_tables, model['quest_id'], name, model)
            closure['participant_imports'] = import_participants(closure, dialogue_tables, tables, importer)
            dialogues[name] = closure
            for issue in closure['unresolved']:
                failures.append(dict(kind='dialogue_dependency', quest=name, detail=issue))
        # Controllers retain their original world identities. Initial-world
        # doors also need import even when no quest explicitly links to them.
        for record in all_entities.values():
            attrs = record['attributes']
            if attrs.get('EntityClass') == 'QuestObject' and attrs.get('Name') in controller_ids:
                kind = 'source_quest_controller'
            elif attrs.get('EntityClass') == 'AnimDoor':
                layer = attrs.get('Layer', '')
                if not (initial_layer(layer) or (record['source'].lower() == 'objects_mission0.xml' and not layer)):
                    continue
                kind = 'initial_world_door'
            else:
                continue
            guid = attrs.get('EntityGuid', '')
            if re.fullmatch('[0-9a-fA-F]{16}', guid):
                importer.ensure('entities', native_guid(int(guid, 16)), dict(kind=kind, source=record['source']))
        for name, plan in bindings.items():
            missing[name] = registration_errors(plan, importer.registered)
        native_files, quest_report, registration_files, registration_report = {}, None, {}, None
        combat_report = None
        population_schedule_report = None
        combat_files = {}
        if eligible:
            try:
                native_files, quest_report = emit_project(eligible, project, diagnostics=False)
                wire_bridge(native_files, project, bridge_files, bridge)
                wire_bridge(native_files, project, behavior_bridge_files, behavior, 'source_behavior_bridge')
                native_files, registration_files, registration_report = assemble_registrations(
                    project, native_files, {m['quest']: bindings[m['quest']] for m in eligible if m['quest'] in bindings},
                    importer.registered, importer.files, export_version,
                    source_world=links['world'], importer=importer)
                from campaign_population_schedule import assemble as assemble_population_schedule
                from character_person_world import native_wait_activity
                activity_contract, _ = native_wait_activity(target)
                registration_files, population_schedule_report = assemble_population_schedule(
                    eligible, registration_files, importer.registered, importer.files, activity_contract)
                failures.extend(dict(kind='population_schedule', **issue)
                                for issue in population_schedule_report['unresolved'])
                # The same converter used for package refreshes participates in
                # normal campaign assembly. Missing world/dialogue dependencies
                # are reported here instead of silently omitting the fight.
                combined = {'Quests/' + n: b for n, b in native_files.items()}
                combined.update(behavior_files)
                converted, registration_files, combat_report = assemble_combat(
                    eligible, {q: dict(b, _world=links['world']) for q, b in bindings.items()},
                    combined, registration_files, project, target)
                for path, data in converted.items():
                    if path.startswith('Quests/') and path.removeprefix('Quests/') in native_files:
                        native_files[path.removeprefix('Quests/')] = data
                    elif path not in combined or data != combined[path]:
                        combat_files[path] = data
                failures.extend(dict(kind='combat_conversion', **issue) for issue in combat_report['unresolved'])
                quest_report['files'] = {n: hashlib.sha256(b).hexdigest() for n, b in native_files.items()}
            except ValueError as error:
                failures.append(dict(kind='native_quest_emission', reason=str(error)))
        for name, data in person_registration.finalize().items():
            if name in importer.files and importer.files[name] != data:
                raise ValueError('Conflicting shared person resource: ' + name)
            importer.files[name] = data
        world_object_merge = None
        world_resource_merge = None
        if baseline_level is not None and 'world/registration/objects_mission0.xml' in registration_files:
            from campaign_world_assembly import assemble as assemble_world
            try:
                with zipfile.ZipFile(baseline_level) as baseline_archive:
                    baseline = {}
                    for path in registration_files:
                        if not path.startswith('world/registration/'):
                            continue
                        name = path.removeprefix('world/registration/')
                        try:
                            baseline[name] = read(baseline_archive, name)
                        except KeyError:
                            pass
                merged, world_resource_merge = assemble_world(baseline, registration_files, importer.jobs.values())
                registration_files.update({'world/assembled/' + name: data for name, data in merged.items()})
            except (ValueError, KeyError) as error:
                failures.append(dict(kind='world_resource_merge', reason=str(error)))
        if baseline_objects is not None and 'world/registration/objects_mission0.xml' in registration_files:
            from campaign_world_objects import merge as merge_world_objects
            try:
                merged, world_object_merge = merge_world_objects(
                    Path(baseline_objects).read_bytes(),
                    registration_files['world/registration/objects_mission0.xml'],
                    importer.jobs.values())
                # Keep the original fragment for other world resources and
                # explicitly label this as an object-only assembly.
                registration_files['world/assembled/objects_mission0.xml'] = merged
            except ValueError as error:
                failures.append(dict(kind='world_object_merge', reason=str(error)))
        # Conversion fragments are deliberately not an installable Data tree.
        # They cannot be mistaken for the complete source behavior program.
        for name, payload in {**{'Quests/' + n: b for n, b in native_files.items()}, **bridge_files,
                              **behavior_bridge_files, **behavior_files, **importer.files, **registration_files,
                              **combat_files}.items():
            path = stage / 'native-fragments' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, Path):
                shutil.copyfile(payload, path)
            else:
                path.write_bytes(payload)
        counts = Counter()
        for model in models.values():
            document = 'libs/ai/quests/' + model['quest'].lower() + '.xml'
            for tree in model['behavior_documents'].get(document, {}).get('trees', {}).values():
                counts.update(n['op'] for n in walk(tree['root']))
        report = dict(schema=1, project=project, source_level=level,
                      source_entry=links['entry']['dispatch'], source_entry_provenance=links['entry_provenance'],
                      seed_quests=seeds, actor_roots=actor_roots, quests=sorted(models), attempted_quests=sorted(attempted),
                      dependencies=dependencies, import_failures=failures,
                      registration_errors=missing, quest_state_conversion=quest_report,
                      dependency_import_counts=importer.report()['counts'],
                      controller_brains=controllers,
                      population_actor_requests=len(population_imports),
                      population_schedules=population_schedule_report,
                      world_object_merge=world_object_merge,
                      world_resource_merge=world_resource_merge,
                      combat_conversion=combat_report,
                      dialogue_dependency_counts={name: len(r['branches']) for name, r in dialogues.items()},
                      behavior_operation_counts=dict(sorted(counts.items())),
                      source_behavior_executed=False, executable=False, ready_to_play=False,
                      blockers=['Behavior trees with unsupported operations or dependencies remain unconverted',
                                'World/profile, actor, item and dialogue dependency conversion must pass',
                                'Retail New Game and native save/load require end-to-end validation'])
        for name, value in [('campaign-links.json', links), ('bindings.json', bindings),
                            ('quest-bridge.json', bridge), ('behavior-conversion.json', behavior),
                            ('dependency-imports.json', importer.report()), ('native-behavior-catalog.json', catalog),
                            ('native-registrations.json', registration_report),
                            ('dialogue-dependencies.json', dialogues),
                            ('actor-dialogue-dependencies.json', actor_dialogues),
                            ('population-dependencies.json', population_imports),
                            ('work-graph-dependencies.json', dict(closures=work_closures, imports=work_graph_imports)),
                            ('population-schedules.json', population_schedule_report),
                            ('combat-conversion.json', combat_report),
                            ('campaign-conversion.json', report)]:
            (stage / name).write_text(json.dumps(value, indent=2), encoding='utf-8')
        report['files'] = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in stage.rglob('*') if p.is_file() and p.name != 'campaign-conversion.json'}
        (stage / 'campaign-conversion.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        stage.rename(output)
    return dict(project=project, quests=len(models), source_references=len(dependencies),
                converted_state_operations=len(bridge['converted']), state_operations_pending=len(bridge['unresolved']),
                binding_plans=len(bindings), import_failures=len(failures), executable=False,
                dependency_imports=importer.report()['counts'],
                converted_combats=len((combat_report or {}).get('converted', [])),
                unresolved_combats=len((combat_report or {}).get('unresolved', [])),
                ready_to_play=False, report=str(output / 'campaign-conversion.json'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kcd1', type=Path, required=True)
    p.add_argument('--kcd2', type=Path, required=True)
    p.add_argument('--level', required=True)
    p.add_argument('--project', required=True)
    p.add_argument('--entry-action', default='Libs/UI/UIActions/MM_NewGame.xml')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--registered', type=Path, help='Destination entities/souls/items from conversion manifests')
    p.add_argument('--engine-enums', type=Path, help='Explicit source-engine enum evidence; revalidated read-only against WHGame.dll')
    p.add_argument('--baseline-objects', type=Path, help='Converted objects_mission0.xml to assemble gameplay placements into')
    p.add_argument('--baseline-level', type=Path, help='Converted level.pak to assemble native world registration resources into')
    p.add_argument('--actor-roots', type=Path, help='JSON mapping source quest owners to actor names; imports their authored routines and work dependencies')
    a = p.parse_args()
    import setup_progress
    setup_progress.sink = lambda label, current, total, detail='': print(
        f'{label} [{current}/{total}] {detail}', flush=True)
    registered = json.loads(a.registered.read_text(encoding='utf-8')) if a.registered else None
    actor_roots = json.loads(a.actor_roots.read_text(encoding='utf-8')) if a.actor_roots else None
    if actor_roots is not None and (not isinstance(actor_roots, dict) or any(
            not isinstance(names, list) or not all(isinstance(n, str) and n for n in names)
            for names in actor_roots.values())):
        p.error('--actor-roots must map quest names to lists of source actor names')
    print(json.dumps(compile_campaign(a.kcd1, a.kcd2, a.level, a.output, a.project, a.entry_action, registered, a.engine_enums, a.baseline_objects, a.baseline_level, actor_roots), indent=2))


if __name__ == '__main__':
    main()
