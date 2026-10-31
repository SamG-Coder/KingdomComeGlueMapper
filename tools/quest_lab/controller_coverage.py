"""Whole-tree conversion and structural interpreter coverage, never a playthrough."""
from collections import Counter
from pathlib import Path
import hashlib

from campaign_behavior import convert_behaviors, native_catalog
from campaign_ai_types import AITypeSource, AIEnumAdapter, AIStructureAdapter
from campaign_dependencies import DependencyImporter
from campaign_sources import retail_sources
from campaign_quest_graph import emit_project
from campaign_compile import wire_bridge
from .behavior import Behavior
from .graph import Graph
from .source import Source
from .campaign import write


def inspect(models, catalog, importer):
    behaviors, bridges, conversion = convert_behaviors(models, 'ControllerCoverage', catalog, importer=importer)
    files, states = emit_project(models, 'ControllerCoverageProject')
    wire_bridge(files, 'ControllerCoverageProject', bridges, conversion)
    files.update({p.removeprefix('Quests/'):b for p,b in bridges.items() if p.startswith('Quests/')})
    files.update(behaviors)
    files.update({p:b.read_bytes() if isinstance(b,Path) else b for p,b in importer.files.items()})
    source = Source.blobs(files)
    graph = Graph(source, 'ControllerCoverageProject')
    controllers = []
    for path, blob in sorted(behaviors.items()):
        behavior = Behavior(path, blob, graph)
        controllers.append(dict(file=path, trees=sorted(behavior.trees),
                                indexed_nodes=len(behavior.nodes), findings=behavior.unsupported,
                                executed=False))
    reasons = Counter(issue['reason'] for tree in conversion['trees'] for issue in tree['unresolved'])
    report = dict(status='STRUCTURAL_AUDIT_ONLY', source_quests=[m['quest'] for m in models],
        conversion=conversion, state_conversion=states, dependency_imports=importer.report(),
        conversion_blockers=dict(reasons.most_common()), controllers=controllers,
        graph_findings=graph.unsupported, native_runtime_verified=False, campaign_completed=False,
        scope='Full behavior trees with script type/enum adapters; actor, world and native consumer dependencies remain unresolved',
        files={p:hashlib.sha256(b).hexdigest() for p,b in files.items()})
    return files, report


def audit(models, source_game, target_game, output):
    output = Path(output)
    if output.exists(): raise ValueError('Choose a fresh behavior coverage directory')
    catalog = native_catalog(Path(target_game))
    types = AITypeSource.load(Path(source_game), retail_sources, 'ControllerCoverage', output/'types')
    importer = DependencyImporter(dict(ai_types=AIStructureAdapter(types,catalog['variable_types']),
                                       ai_enums=AIEnumAdapter(types)))
    files, report = inspect(models, catalog, importer)
    for name, blob in files.items():
        path = output/'compiled'/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(blob)
    write(output/'report.json',report)
    return dict(report=str(output/'report.json'),
                whole_trees_emitted=report['conversion']['whole_trees_emitted'],
                blocked_trees=report['conversion']['blocked_trees'],
                structural_findings=sum(len(c['findings']) for c in report['controllers']),
                status=report['status'], native_runtime_verified=False)
