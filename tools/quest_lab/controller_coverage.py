"""Whole-tree conversion and structural interpreter coverage, never a playthrough."""
from collections import Counter
from pathlib import Path
import hashlib
import zipfile

from campaign_behavior import convert_behaviors, native_catalog
from campaign_ai_types import AITypeSource, AIEnumAdapter, AIStructureAdapter
from campaign_dependencies import DependencyImporter
from campaign_sources import retail_sources, RetailSourceReader
from campaign_dependency_adapters import SourceTables
from campaign_ai_registration import NativeAIRegistry, MailboxAdapter
from campaign_link_types import LinkTypeAdapter
from campaign_profiles import STREAMING_SOURCE
from upgrade_map import read
from campaign_quest_graph import emit_project
from campaign_compile import wire_bridge
from .behavior import Behavior
from .graph import Graph
from .source import Source
from .campaign import write


def inspect(models, catalog, importer, profile_streaming=None):
    behaviors, bridges, conversion = convert_behaviors(models, 'ControllerCoverage', catalog,
        importer=importer, profile_streaming=profile_streaming)
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
        scope='Full behavior trees with the listed dependency adapters; remaining actor, world and native consumers are not simulated',
        dependency_adapters=sorted(importer.adapters),
        native_profile_streaming_available=profile_streaming is not None,
        files={p:hashlib.sha256(b).hexdigest() for p,b in files.items()})
    return files, report


def audit(models, source_game, target_game, output):
    output = Path(output)
    if output.exists(): raise ValueError('Choose a fresh behavior coverage directory')
    catalog = native_catalog(Path(target_game))
    types = AITypeSource.load(Path(source_game), retail_sources, 'ControllerCoverage', output/'types')
    with RetailSourceReader(source_game) as reader, zipfile.ZipFile(Path(target_game)/'Data/Scripts.pak') as scripts:
        tables = SourceTables(Path(source_game), reader)
        native = NativeAIRegistry.load(target_game, 'ControllerCoverage')
        importer = DependencyImporter(dict(ai_types=AIStructureAdapter(types,catalog['variable_types']),
            ai_enums=AIEnumAdapter(types), mailboxes=MailboxAdapter(tables,native),
            link_types=LinkTypeAdapter.load(Path(source_game),Path(target_game),reader,
                                           'ControllerCoverage',output/'links')))
        files, report = inspect(models, catalog, importer, read(scripts,STREAMING_SOURCE))
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
