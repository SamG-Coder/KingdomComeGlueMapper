"""Exercise production quest-state conversion offline for every source record.

This is a conversion dry run, not installation or a replay of actor behaviors.
Converter omissions must survive even when the emitted graph itself executes.
"""
from collections import Counter, defaultdict
import copy
from pathlib import Path
import xml.etree.ElementTree as E
import zipfile

from campaign_sources import MissingRetailSource, RetailSourceReader, key
from campaign_quest_graph import emit_project
from campaign_objective_source import objective_differences
from quest_import import parse_graph, resolve_null_tracking_edges
from .audit import quest_audit
from .source import Source


def source_differences(graph, tables):
    """Use the same reconciliation evidence as the production importer."""
    rows = {r['objective_id']:r for r in tables['quest_objective']['rows']}
    evidence = objective_differences(graph, tables)
    evidence.update(empty_objective_names=[i for i,r in rows.items() if not r.get('objective_name')],
                    unresolved_edges=graph['unresolved_edges'], null_tracking_edges=graph.get('null_tracking_edges', []))
    return evidence


def ordered_records(records, first=()):
    """Prioritize explicit source identities without dropping later quests."""
    first = list(first)
    if len(first) != len(set(first)):
        raise ValueError('Duplicate priority quest')
    missing = set(first) - {r['quest_name'] for r in records}
    if missing:
        raise ValueError('Unknown priority quests: ' + ', '.join(sorted(missing)))
    rank = {name: index for index, name in enumerate(first)}
    return sorted(records, key=lambda r: (rank.get(r['quest_name'], len(rank)),
                                         r.get('group', ''), r['quest_name']))


def run(game, progress=None, *, first=(), checkpoint=None, model_ready=None):
    game = Path(game)
    results = []
    with RetailSourceReader(game) as reader:
        with zipfile.ZipFile(game / 'Data/Tables.pak') as archive:
            names = [n for n in archive.namelist() if key(n).startswith('libs/tables/quest/')
                     and key(n).endswith('.xml')]
        tables = {}; by_id = {}
        for path in names:
            record = reader(game, {'table': ('Tables.pak', path)})['table']
            table = E.fromstring(record['data']).find('table')
            name = table.get('name')
            if name in tables: raise ValueError('Duplicate source table: ' + name)
            rows = [dict(r.attrib) for r in table.findall('./rows/row')]
            tables[name] = dict(rows=rows, columns=[dict(c.attrib) for c in table.findall('./header/column')],
                                provenance=record['candidates'])
            by_id[name] = defaultdict(list)
            for row in rows: by_id[name][row.get('quest_id')].append(row)
        records = ordered_records(tables['quest']['rows'], first)
        for index, row in enumerate(records):
            name, identity = row['quest_name'], row['quest_id']
            result = dict(name=name, group=row.get('group', ''), quest_id=identity,
                          status='NOT_RUN', native_runtime_verified=False, actor_behaviors_executed=False)
            try:
                record = reader(game, {'graph': ('GameData.pak', 'Libs/quests/flowgraphs/' + name + '.xml')})['graph']
                graph = parse_graph(record['data'], name)
                selected = {n: dict(rows=copy.deepcopy(by_id[n].get(identity, [])),
                                    columns=t['columns'], provenance=t['provenance']) for n,t in tables.items()}
                resolve_null_tracking_edges(graph, selected)
                result['source_differences'] = source_differences(graph, selected)
                model = dict(quest=name, quest_id=identity, tables=selected, graph=graph)
                result['source_graph_provenance'] = record['candidates']
                blobs, conversion = emit_project([model], 'QuestLabSource')
                if model_ready:
                    model_ready(model)
                result['converter_unsupported'] = conversion['quests'][0]['unsupported']
                result['reconciled_objectives'] = conversion['quests'][0]['objective_table_differences']
                result['converter_unsupported_kinds'] = dict(Counter(x['kind'] for x in result['converter_unsupported']))
                source = Source.blobs(blobs)
                path, host = source.definition(name)
                tested = quest_audit(source, path, host)
                result.update(status='DRY_RUN_PARTIAL' if result['converter_unsupported'] or
                              tested['status'] != 'LOCAL_PROBES_EXECUTED' else 'LOCAL_STATE_PROBES_EXECUTED',
                              interpreter_status=tested['status'], probe_counts=tested.get('probe_counts', {}),
                              nodes=tested.get('nodes'), visited_nodes=tested.get('visited_nodes'),
                              unsupported=tested.get('unsupported', []), error=tested.get('error'),
                              emitted_files=tested.get('files', {}))
                # Retain every stimulus and outcome; full snapshots for thousands
                # of hypothetical branches would obscure the actionable evidence.
                result['probes'] = [{k:p[k] for k in ('name','events','advance_hours','status','error')}
                                    for p in tested['probes']]
            except MissingRetailSource as exc: result.update(status='SOURCE_GRAPH_ABSENT', error=str(exc))
            except (ValueError, KeyError, TypeError, AssertionError) as exc:
                result.update(status='CONVERSION_BLOCKED', error=type(exc).__name__ + ': ' + str(exc))
            results.append(result)
            if checkpoint:
                checkpoint(results, len(records))
            if progress:
                progress(index + 1, len(records), name, result['status'])
    return dict(scope='Original source quest-state graphs passed through the current production emitter in memory',
                limits=['These generated graphs are not installed.',
                        'Actor behaviors, dialogue, world events and cross-quest startup are not executed.',
                        'Source records include tests, obsolete authoring data and DLC.',
                        'Converter unsupported records prevent a clean local-state result even if all probes execute.'],
                quests=results, statuses=dict(Counter(r['status'] for r in results)),
                probes=sum(sum(r.get('probe_counts', {}).values()) for r in results))
