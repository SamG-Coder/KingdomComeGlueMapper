"""Read campaign evidence from retail KCD1 archives; no editor database required."""
from collections import Counter
from contextlib import ExitStack
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
    return retail_sources(game, OPENING_FILES)


class MissingRetailSource(ValueError):
    """The entry is absent from every applicable installed retail archive."""


class RetailSourceReader:
    """A scoped retail resolver for a whole campaign import.

    Open archives and hash each effective source once. Keeping this cache local
    to a build avoids both repeated multi-quest scans and stale cross-run data.
    The source/patch ordering is the same as retail_sources().
    """
    def __init__(self, game):
        self.game = Path(game).resolve()
        self.stack = ExitStack()
        self.archives = {}
        self.cache = {}
        self.absent = set()
        self.patches = []
        for path in (self.game / 'Data/patch').glob('*.pak'):
            match = re.fullmatch(r'(?:ipl_)?patch_(\d+)([a-z]?)(?:_hd)?\.pak', path.name.lower())
            if not match:
                raise ValueError('Unrecognized patch ordering: ' + path.name)
            self.patches.append(((int(match[1]), match[2]), path))
        self.patches.sort()

    def family_archives(self, pak):
        """Include installed DLC members of the requested content family.

        New entries need no guessed precedence. Conflicting base/DLC versions
        are rejected; numbered whole-file patches are applied afterwards.
        """
        base = self.game / 'Data' / pak
        paths = [base]
        if base.parent == self.game / 'Data':
            pattern = re.compile(re.escape(base.stem) + r'_dlc\d+\.pak', re.I)
            paths.extend(sorted(p for p in base.parent.glob('*.pak') if pattern.fullmatch(p.name)))
        return paths

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def archive(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.game / 'Data'):
            raise ValueError('Source archive escapes retail Data directory')
        if path not in self.archives:
            archive = self.stack.enter_context(zipfile.ZipFile(path))
            names = {}
            for name in archive.namelist():
                normalized = key(name)
                if normalized in names:
                    raise ValueError('Duplicate case-insensitive retail entry: ' + name)
                names[normalized] = name
            self.archives[path] = archive, names
        return self.archives[path]

    def __call__(self, game, files):
        if Path(game).resolve() != self.game:
            raise ValueError('Retail source cache used with a different installation')
        result = {}
        for label, (pak, entry) in files.items():
            cache_key = (key(pak), key(entry))
            if cache_key in self.absent:
                raise MissingRetailSource('Missing effective retail source: ' + entry)
            if cache_key not in self.cache:
                data, candidates = None, []
                for path in self.family_archives(pak):
                    archive, names = self.archive(path)
                    if key(entry) not in names:
                        continue
                    incoming = read(archive, names[key(entry)])
                    digest = hashlib.sha256(incoming).hexdigest()
                    if data is not None and digest != candidates[-1]['sha256']:
                        raise ValueError('Ambiguous base/DLC campaign override: ' + entry)
                    candidates.append(dict(archive=path.relative_to(self.game).as_posix(), revision='base', sha256=digest))
                    data = incoming
                for revision, path in self.patches:
                    patch, patch_names = self.archive(path)
                    if key(entry) not in patch_names:
                        continue
                    incoming = read(patch, patch_names[key(entry)])
                    digest = hashlib.sha256(incoming).hexdigest()
                    version = str(revision[0]) + revision[1]
                    if candidates and candidates[-1]['revision'] == version and candidates[-1]['sha256'] != digest:
                        raise ValueError('Ambiguous same-revision campaign override: ' + entry)
                    candidates.append(dict(archive=path.relative_to(self.game).as_posix(), revision=version, sha256=digest))
                    data = incoming
                if data is None:
                    self.absent.add(cache_key)
                    raise MissingRetailSource('Missing effective retail source: ' + entry)
                self.cache[cache_key] = dict(entry=entry, data=data, candidates=candidates)
            result[label] = self.cache[cache_key]
        return result


def retail_sources(game, files):
    """Resolve whole-file patch candidates, retaining every provenance record.

    Patch revision order is explicit, not filename lexical order (which groups
    all IPL patches before all ordinary patches). Conflicting files at the same
    revision are rejected until their engine precedence has been established.
    """
    with RetailSourceReader(game) as reader:
        return reader(game, files)


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
