"""Reproducible original-controller state conversion with source registrations."""
from pathlib import Path

from campaign_sources import RetailSourceReader
from campaign_dependency_adapters import SourceTables
from campaign_ai_registration import controller_definition
from campaign_compile import quest_dependencies, wire_bridge
from campaign_quest_bridge import convert_state_operations
from campaign_quest_graph import emit_project, validate_model
from quest_import import import_quest
from .campaign import write
from .model_pool import ModelPool
from .actor_queries import inspect as actor_queries


def audit(game, names, output, progress=None, follow_dependencies=False, target_game=None):
    names = list(names)
    if not names or len(names) != len(set(names)):
        raise ValueError('Select unique source quest names')
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh controller audit directory')
    models, records = [], []
    pool = ModelPool()
    with RetailSourceReader(Path(game)) as reader:
        tables = SourceTables(Path(game), reader)
        # Resolve all requested identities before creating output or importing.
        roots = {name: tables.row('quest/quest', 'quest_name', name) for name in names}
        pending, discovered = list(names), set(names)
        while pending:
            name = pending.pop(0)
            if progress: progress(len(records) + 1, len(discovered), name)
            record = dict(quest=name, status='CONVERSION_BLOCKED')
            try:
                row = roots[name] if name in roots else tables.row('quest/quest', 'quest_name', name)
                definition = controller_definition(tables, row)
                bootstrap = sorted({entry['file'] for entry in (definition or {}).get('entry_points', [])})
                model, _ = import_quest(Path(game), name, bootstrap=bootstrap,
                                       source_reader=reader, require_behavior=False)
                model['controller_registration'] = definition
                validate_model(model)
                dependencies = quest_dependencies(model)
                write(output / 'source-ir' / (name + '.json'), model)
                models.append(pool.add(model))
                record.update(status='STATE_MODEL_ACCEPTED', dependencies=dependencies)
                queries = actor_queries(model)
                write(output / 'actor-queries' / (name + '.json'), queries)
                record['actor_link_queries'] = len(queries)
                record['implicit_origin_queries'] = sum(q['origin_requires_engine_context'] for q in queries)
                if follow_dependencies:
                    for dependency in dependencies:
                        target = dependency['quest']
                        if not dependency['dynamic'] and target not in discovered:
                            discovered.add(target)
                            pending.append(target)
            except (ValueError, KeyError, FileNotFoundError) as exc:
                record['error'] = type(exc).__name__ + ': ' + str(exc)
            records.append(record)
            write(output / 'checkpoint.json', dict(status='IN_PROGRESS', quests=records,
                                                   pending_quests=pending,
                                                   native_runtime_verified=False))
    report = dict(quests=records, native_runtime_verified=False, campaign_completed=False,
                  root_quests=names, follow_dependencies=follow_dependencies,
                  scope='Registered source controller quest-state operations; not whole behavior execution')
    if models:
        files, states = emit_project(models, 'QuestLabControllers')
        bridges, operations = convert_state_operations(models, 'QuestLabControllerBridge')
        wire_bridge(files, 'QuestLabControllers', bridges, operations)
        files.update({name.removeprefix('Quests/'): blob for name, blob in bridges.items()
                      if name.startswith('Quests/')})
        for name, blob in files.items():
            path = output / 'compiled' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        report.update(state_conversion=states, controller_operations=operations)
        # Emitter hashes predate bridge wiring; record final files separately.
        import hashlib
        report['compiled_files'] = {name:hashlib.sha256(blob).hexdigest() for name,blob in files.items()}
        accepted = {model['quest'] for model in models}
        report['missing_quest_dependencies'] = sorted({d['quest'] for record in records
            for d in record.get('dependencies', []) if not d['dynamic'] and d['quest'] not in accepted})
        if target_game is not None:
            from .controller_coverage import audit as behavior_audit
            if progress: progress(len(records),len(records),'Whole controller conversion and structural coverage')
            report['behavior_coverage'] = behavior_audit(models,game,target_game,output/'behavior-coverage')
    write(output / 'report.json', report)
    write(output / 'checkpoint.json', dict(status='AUDIT_FINISHED', quests=records,
                                           native_runtime_verified=False))
    return report
