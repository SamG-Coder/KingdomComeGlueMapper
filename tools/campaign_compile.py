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
from campaign_ai_registration import (NativeAIRegistry, MailboxAdapter, BrainAdapter,
    SubbrainAdapter, ConvertedTreeAdapter, SmartEntityAdapter, controller_definition, quest_controller_brains)
from campaign_doors import NativeDoorCatalog, AnimatedDoorAssetAdapter, DoorEntityConversion
from campaign_dependencies import DependencyImporter, import_binding_dependencies
from campaign_dependency_adapters import (EntityAdapter, InventoryAdapter, ItemAdapter, ItemTextAdapter,
                                          LazyAdapter, SoulAdapter, SourceTables, StaticAssetAdapter)
from campaign_linker import build_links, link_entities, entity_index
from campaign_entity_links import native_guid
from entity_visuals import initial_layer
from campaign_quest_bridge import convert_state_operations
from campaign_quest_graph import emit_project, identifier, validate_model, xml
from campaign_registration import assemble_registrations
from campaign_sources import RetailSourceReader
from quest_import import import_quest, literal, walk
from setup_progress import progress
from game_paths import GameLibrary
from upgrade_map import read


def quest_dependencies(model):
    """Discover references, not which branches should run on New Game."""
    result = []
    for document, record in model['behavior_documents'].items():
        for tree, body in record['trees'].items():
            for index, node in enumerate(walk(body['root'])):
                if node['op'] not in ('SetQuest', 'SetQuestObjective', 'QuestCondition', 'QuestObjectiveCondition', 'QuestObjectiveGate'):
                    continue
                attrs = node['attributes']
                value = literal(attrs.get('quest', attrs.get('questName', '')))
                # Empty/current-owner references are resolved at their call site.
                if value in ('', '$this.name', 'this.name'):
                    continue
                result.append(dict(quest=value, dynamic=not bool(re.fullmatch(r'[A-Za-z_]\w*', value)),
                                   site=dict(document=document, tree=tree, preorder_index=index,
                                             operation=node['op'], arguments=attrs)))
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


def compile_campaign(source, target, level, output, project, entry_action='Libs/UI/UIActions/MM_NewGame.xml', registered=None):
    identifier(project)
    source, target, output = Path(source), Path(target), Path(output)
    if output.exists():
        raise FileExistsError('Choose a new campaign conversion directory')
    if not (target / 'Data/Scripts.pak').is_file():
        raise ValueError('The installed retail target game is required')
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack, RetailSourceReader(source) as resolver, tempfile.TemporaryDirectory(prefix='.campaign-convert-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'stage'
        stage.mkdir()
        links = build_links(source, level, entry_action, source_reader=resolver)
        seeds = sorted({r['database_row']['quest_name'] for r in links['quests']})
        if not seeds:
            raise ValueError('The source New Game link closure contains no quest registrations')
        table = resolver(source, {'quest': ('Tables.pak', 'Libs/Tables/quest/quest.xml')})['quest']
        known = {r.get('quest_name') for r in ET.fromstring(table['data']).findall('./table/rows/row')}
        tables = SourceTables(source, resolver)
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
                model, _ = import_quest(source, name, bootstrap=bootstrap, source_reader=resolver, require_behavior=False)
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
        native_ai = NativeAIRegistry.load(target, project)
        export_version = int.from_bytes(hashlib.sha256(area_data + project.encode()).digest()[:4], 'little') or 1
        assets = LazyAdapter(lambda: StaticAssetAdapter(stack, GameLibrary(paths), project, cache))
        native_doors = NativeDoorCatalog(target, stack)
        animated_doors = AnimatedDoorAssetAdapter(assets, native_doors, project)
        door_entities = DoorEntityConversion(tables, animated_doors, native_doors)
        all_entities, _ = entity_index(documents)
        controller_ids = {}
        for model in models.values():
            definition = model.get('controller_registration')
            if definition:
                smart = definition['smart_object']
                controller_ids.setdefault(smart['so_smart_object_name'], []).append(model['quest'] + '|' + smart['so_smart_object_id'])
        importer = DependencyImporter(dict(
            entities=EntityAdapter(dict(entities=all_entities), souls, area_data, export_version, door_entities, controller_ids),
            items=ItemAdapter(tables, project), inventories=InventoryAdapter(tables, project),
            souls=SoulAdapter(tables, souls),
            character_parts=CharacterPartAdapter(tables, target, stack, assets, project),
            ai_types=AIStructureAdapter(ai_types), ai_enums=AIEnumAdapter(ai_types),
            mailboxes=MailboxAdapter(tables, native_ai),
            animated_props=animated_doors,
            strings=LazyAdapter(lambda: ItemTextAdapter(source, project, 'English')),
            assets=assets), registered)
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
        catalog = native_catalog(target)
        behavior_files, behavior_bridge_files, behavior = convert_behaviors(
            eligible, project + '_behavior', catalog, importer.registered, importer)
        importer.adapters.update(behavior_trees=ConvertedTreeAdapter(behavior, behavior_files),
            subbrains=SubbrainAdapter(tables, native_ai), brains=BrainAdapter(tables, native_ai, ai_types),
            smart_entities=SmartEntityAdapter(tables, native_ai))
        controllers = quest_controller_brains(eligible, tables, importer)
        progress('Importing quest dependencies', 0, len(bindings), 'Actors, inventory, items, models and trigger areas')
        import_binding_dependencies(bindings, importer)
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
        if eligible:
            try:
                native_files, quest_report = emit_project(eligible, project, diagnostics=False)
                wire_bridge(native_files, project, bridge_files, bridge)
                wire_bridge(native_files, project, behavior_bridge_files, behavior, 'source_behavior_bridge')
                native_files, registration_files, registration_report = assemble_registrations(
                    project, native_files, {m['quest']: bindings[m['quest']] for m in eligible if m['quest'] in bindings},
                    importer.registered, importer.files, export_version)
                quest_report['files'] = {n: hashlib.sha256(b).hexdigest() for n, b in native_files.items()}
            except ValueError as error:
                failures.append(dict(kind='native_quest_emission', reason=str(error)))
        # Conversion fragments are deliberately not an installable Data tree.
        # They cannot be mistaken for the complete source behavior program.
        for name, payload in {**{'Quests/' + n: b for n, b in native_files.items()}, **bridge_files,
                              **behavior_bridge_files, **behavior_files, **importer.files, **registration_files}.items():
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
                      seed_quests=seeds, quests=sorted(models), attempted_quests=sorted(attempted),
                      dependencies=dependencies, import_failures=failures,
                      registration_errors=missing, quest_state_conversion=quest_report,
                      dependency_import_counts=importer.report()['counts'],
                      controller_brains=controllers,
                      behavior_operation_counts=dict(sorted(counts.items())),
                      source_behavior_executed=False, executable=False, ready_to_play=False,
                      blockers=['Behavior trees with unsupported operations or dependencies remain unconverted',
                                'World/profile, actor, item and dialogue dependency conversion must pass',
                                'Retail New Game and native save/load require end-to-end validation'])
        for name, value in [('campaign-links.json', links), ('bindings.json', bindings),
                            ('quest-bridge.json', bridge), ('behavior-conversion.json', behavior),
                            ('dependency-imports.json', importer.report()), ('native-behavior-catalog.json', catalog),
                            ('native-registrations.json', registration_report),
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
    a = p.parse_args()
    registered = json.loads(a.registered.read_text(encoding='utf-8')) if a.registered else None
    print(json.dumps(compile_campaign(a.kcd1, a.kcd2, a.level, a.output, a.project, a.entry_action, registered), indent=2))


if __name__ == '__main__':
    main()
