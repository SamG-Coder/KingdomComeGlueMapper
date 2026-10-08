"""Read campaign evidence from retail KCD1 archives; no editor database required."""
from collections import Counter
import hashlib
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from upgrade_map import read


OPENING_FILES = {
    'new_game': ('GameData.pak', 'Libs/UI/UIActions/MM_NewGame.xml'),
    'master': ('Scripts.pak', 'Libs/AI/quests/q_master_main.xml'),
    'quest_graph': ('GameData.pak', 'Libs/quests/flowgraphs/q_skalitz.xml'),
    'quest_behavior': ('Scripts.pak', 'Libs/AI/quests/q_skalitz.xml'),
}


def key(name):
    return name.replace('\\', '/').lower()


def opening_spawn(game):
    """Read the original retail level's default player start, not a cutscene spot."""
    archive_path = 'Data/Levels/rataje/level.pak'
    entry = 'objects_mission0.xml'
    with zipfile.ZipFile(Path(game) / archive_path) as archive:
        data = read(archive, entry)
    root = ET.fromstring(data)
    candidates = [e for e in root.findall('Entity')
                  if e.get('EntityClass') == 'SpawnPoint' and e.get('Name') == 'spawnStart']
    if len(candidates) != 1:
        raise ValueError('Expected exactly one retail KCD1 spawnStart')
    entity = candidates[0]
    position = entity.get('Pos', '')
    rotation = entity.get('Rotate', '1,0,0,0')
    xyz = [float(n) for n in position.split(',')]
    quat = [float(n) for n in rotation.split(',')]
    if (len(xyz) != 3 or len(quat) != 4 or not all(math.isfinite(n) for n in xyz + quat)
            or not (0 < xyz[0] < 4096 and 0 < xyz[1] < 4096 and -1000 < xyz[2] < 10000)
            or abs(sum(n * n for n in quat) - 1) > 0.001):
        raise ValueError('Invalid retail opening spawn transform')
    return {'archive': archive_path, 'entry': entry,
            'sha256': hashlib.sha256(data).hexdigest(),
            'entity': entity.get('Name'), 'entity_guid': entity.get('EntityGuid'),
            'position': position, 'rotation': rotation}


def opening_sources(game):
    """Resolve whole-file patch candidates, retaining every provenance record.

    Patch revision order is explicit, not filename lexical order (which groups
    all IPL patches before all ordinary patches). Conflicting files at the same
    revision are rejected until their engine precedence has been established.
    """
    game = Path(game).resolve()
    results = {}
    for label, (pak, entry) in OPENING_FILES.items():
        with zipfile.ZipFile(game / 'Data' / pak) as archive:
            actual = {key(n): n for n in archive.namelist()}[key(entry)]
            data = read(archive, actual)
        results[label] = {'entry': entry, 'data': data, 'candidates': [
            {'archive': 'Data/' + pak, 'sha256': hashlib.sha256(data).hexdigest(), 'revision': 'base'}]}
    wanted = {key(value['entry']): label for label, value in results.items()}
    patches = []
    for path in (game / 'Data/patch').glob('*.pak'):
        match = re.fullmatch(r'(?:ipl_)?patch_(\d+)([a-z]?)(?:_hd)?\.pak', path.name.lower())
        if not match:
            raise ValueError(f'Unrecognized patch ordering: {path.name}')
        patches.append(((int(match[1]), match[2]), path))
    for revision, path in sorted(patches):
        with zipfile.ZipFile(path) as archive:
            for entry in archive.namelist():
                if key(entry) not in wanted:
                    continue
                result = results[wanted[key(entry)]]
                data = read(archive, entry)
                digest = hashlib.sha256(data).hexdigest()
                version = str(revision[0]) + revision[1]
                previous = result['candidates'][-1]
                if previous['revision'] == version and previous['sha256'] != digest:
                    raise ValueError(f'Ambiguous same-revision campaign override: {entry}')
                result['data'] = data
                result['candidates'].append({'archive': path.relative_to(game).as_posix(),
                                             'revision': version, 'sha256': digest})
    return results


def audit_opening(game):
    sources = opening_sources(game)
    graph = ET.fromstring(sources['quest_graph']['data'])
    behavior = ET.fromstring(sources['quest_behavior']['data'])
    nodes = graph.findall('./Nodes/Node')
    ids = {n.get('Id') for n in nodes}
    edges = [dict(e.attrib) for e in graph.findall('./Edges/Edge')]
    dangling = [e for e in edges if e.get('nodeIn') not in ids or e.get('nodeOut') not in ids]
    return {
        'schema': 1, 'campaign': 'kcd1', 'opening_quest': 'q_skalitz',
        'source_policy': 'Installed retail archives only; modding tools are not inputs.',
        'patch_policy': 'Whole-file revision order; conflicting same-revision entries rejected.',
        'sources': {label: {'entry': value['entry'], 'selected': value['candidates'][-1],
                            'candidates': value['candidates']} for label, value in sources.items()},
        'graph': {'node_classes': dict(sorted(Counter(n.get('Class') for n in nodes).items())),
                  'nodes': [dict(n.attrib, inputs=dict(n.find('Inputs').attrib) if n.find('Inputs') is not None else {}) for n in nodes],
                  'edges': edges, 'dangling_edges': dangling},
        'behavior': {'element_counts': dict(sorted(Counter(n.tag for n in behavior.iter()).items())),
                     'persistent_variables': [dict(n.attrib) for n in behavior.iter('Variable') if n.get('isPersistent') == '1']},
        'source_graph_notes': 'Missing node references are retained verbatim; node 0 may be a sentinel. Do not drop or invent its semantics.',
        'ready_to_play': False,
        'blockers': ['Retail menu action and isolated campaign bootstrap',
                     'Quest behavior translation and dependency closure',
                     'Native save/load round-trip validation',
                     'Packaged world, actors, dialogue, audio and navigation validation'],
    }
