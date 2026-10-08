"""Package a converted world and its imported dependencies for retail installation.

The converted-data directory is a build artifact, not an editor dependency.
Legacy quest sources are isolated pending translation; they are not target quests.
"""
from collections import deque
from bisect import bisect_left
from contextlib import ExitStack
import hashlib
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

from campaign_sources import opening_sources
from upgrade_map import read, xml
from setup_progress import progress

LEVEL = 'kcd1_rataje'
LEVEL_ID = 1000
PREFIXES = ('gluebuild', 'glueitems', 'gluelandscape', 'gluemapper',
            'gluenpc', 'glueveg', 'gluewater')
REFERENCE = re.compile(rb'(?:' + b'|'.join(p.encode() for p in PREFIXES)
                       + rb')/[A-Za-z0-9_./\\-]+', re.I)


def write_asset_shards(directory, entries, max_bytes=1024 ** 3, max_entries=50000, total_entries=0):
    """Write stored ZIP32 archives below retail's size and entry-count limits."""
    directory = Path(directory)
    reports, assets = [], []
    archive = None
    size, count = 22, 0
    try:
        for name, payload in entries:
            name = safe_name(name)
            cost = len(payload) + 76 + 2 * len(name.encode('utf-8'))
            if cost + 22 > max_bytes:
                raise ValueError(f'Asset exceeds retail archive limit: {name}')
            if archive is None or size + cost > max_bytes or count >= max_entries:
                if archive is not None:
                    archive.close()
                path = directory / f'kingdomcomegluemapper_world_{len(reports):03d}.pak'
                archive = zipfile.ZipFile(path, 'x', zipfile.ZIP_STORED, allowZip64=False)
                reports.append(path.name)
                size, count = 22, 0
            item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            item.create_system = 0
            archive.writestr(item, payload)
            size += cost
            count += 1
            assets.append({'path': name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
            if len(assets) % 100 == 0 or len(assets) == total_entries:
                progress('Packaging imported assets', len(assets), total_entries, name)
    finally:
        if archive is not None:
            archive.close()
    return reports, assets


def safe_name(name):
    name = name.replace('\\', '/').lower()
    if name.startswith('/') or ':' in name or any(p in ('', '.', '..') for p in name.split('/')):
        raise ValueError(f'Unsafe asset path: {name}')
    return name


def references(payload):
    return {safe_name(m.decode('ascii')) for m in REFERENCE.findall(payload)}


def dependency_closure(seeds, index, load):
    """Resolve imported paths transitively, including streamed texture/mesh parts."""
    queue, selected = deque(sorted(seeds)), set()
    names = sorted(index)
    while queue:
        requested = queue.popleft()
        if requested in index:
            name = requested
        else:
            candidates = [requested + ext for ext in ('.mtl', '.cgf') if requested + ext in index]
            if len(candidates) != 1:
                raise ValueError(f'Unresolved or ambiguous imported asset: {requested}')
            name = candidates[0]
        if name in selected:
            continue
        selected.add(name)
        if not re.search(r'\.dds(?:\.|$)', name):
            queue.extend(sorted(references(load(name)) - selected))
        # CryEngine streams texture mip slices and external geometry separately.
        start = bisect_left(names, name + '.')
        for offset in range(start, len(names)):
            sibling = names[offset]
            if not sibling.startswith(name + '.'):
                break
            if sibling not in selected:
                queue.append(sibling)
        if name + 'm' in index and name + 'm' not in selected:
            queue.append(name + 'm')
    return sorted(selected)


def package_world(converted_data, source_level, destination):
    root, destination = Path(converted_data).resolve(), Path(destination)
    if not re.fullmatch(r'[A-Za-z0-9_]+', source_level):
        raise ValueError('Invalid source level name')
    source = root / 'Levels' / source_level
    for filename in ('level.pak', 'terrain.pak'):
        if not (source / filename).is_file():
            raise ValueError(f'Missing converted world: {source / filename}')
    target = destination / 'Data/Levels' / LEVEL
    target.mkdir(parents=True)
    seeds = set()
    # Preserve terrain streams byte-for-byte; change only the level identity XML.
    for filename in ('level.pak', 'terrain.pak'):
        with zipfile.ZipFile(source / filename) as z:
            for name in z.namelist():
                safe_name(name)
                payload = read(z, name)
                seeds.update(references(payload))
            if filename == 'level.pak':
                with zipfile.ZipFile(target / filename, 'x', zipfile.ZIP_STORED) as out:
                    for name in z.namelist():
                        payload = read(z, name)
                        if name.lower() in ('levelinfo.xml', 'leveldata.xml'):
                            doc = ET.fromstring(payload)
                            info = doc if name.lower() == 'levelinfo.xml' else doc.find('LevelInfo')
                            if info is None:
                                raise ValueError('Missing LevelInfo in converted level')
                            info.set('Name', 'data/levels/' + LEVEL if name.lower() == 'levelinfo.xml' else LEVEL)
                            payload = xml(doc)
                            if name.lower() == 'levelinfo.xml':
                                (target / 'levelinfo.xml').write_bytes(payload)
                        out.writestr(name, payload)
        if filename == 'terrain.pak':
            shutil.copyfile(source / filename, target / filename)
    with ExitStack() as stack:
        index = {}
        for path in sorted(root.glob('gluemapper_*.pak')):
            z = stack.enter_context(zipfile.ZipFile(path))
            for name in z.namelist():
                key = safe_name(name)
                if key.split('/')[0] not in PREFIXES:
                    raise ValueError(f'Unexpected generated archive asset: {name}')
                if key in index:
                    raise ValueError(f'Conflicting generated archive asset: {key}')
                index[key] = (z, name)
        # Development loose assets override earlier generated packages explicitly.
        for prefix in PREFIXES:
            for path in sorted((root / prefix).rglob('*')):
                if path.is_file():
                    if not path.resolve().is_relative_to(root):
                        raise ValueError(f'Converted asset escapes build directory: {path}')
                    index[safe_name(path.relative_to(root).as_posix())] = path
        def load(name):
            entry = index[name]
            return entry.read_bytes() if isinstance(entry, Path) else read(*entry)
        selected = dependency_closure(seeds, index, load)
        shards, assets = write_asset_shards(destination / 'Data', ((name, load(name)) for name in selected), total_entries=len(selected))
    return {'level': LEVEL, 'path': 'Levels/' + LEVEL,
            'source_level': source_level, 'imported_assets': assets, 'asset_archives': shards,
            'native_dependencies_validated': False,
            'navigation_and_initial_state_validated': False}


def package_opening(source, destination):
    """Retain exact patched retail inputs without overriding native quest systems."""
    result = {}
    with zipfile.ZipFile(Path(destination) / 'Data/kingdomcomegluemapper_sources.pak', 'x', zipfile.ZIP_STORED) as out:
        for label, item in opening_sources(source).items():
            name = 'GlueMapper/CampaignSource/' + item['entry']
            out.writestr(name, item['data'])
            result[label] = {'path': name, 'source': item['candidates'][-1], 'executable': False}
    return result


def level_registration(target):
    """Add a separate level-table row, refusing collisions with native levels."""
    with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as z:
        original = ET.fromstring(read(z, 'Libs/Tables/level.xml'))
    levels = original.find('levels')
    if levels is None:
        raise ValueError('Unsupported native level table')
    for row in levels:
        if row.get('LevelId') == str(LEVEL_ID) or row.get('LevelName', '').lower() == LEVEL:
            raise ValueError('Campaign level identity conflicts with native level table')
    output = ET.Element(original.tag, original.attrib)
    entries = ET.SubElement(output, 'levels', levels.attrib)
    ET.SubElement(entries, 'LevelData', LevelId=str(LEVEL_ID), LevelName=LEVEL, CompassOffset='0')
    return xml(output)


def level_table_shells(target):
    """Native level-local table containers, pending conversion of their records.

    Do not copy another world's NPC schedules or entity GUIDs into KCD1.
    Read container names/versions from the installed target build instead.
    """
    result = {}
    with zipfile.ZipFile(Path(target) / 'Data/Levels/trosecko/level.pak') as z:
        for name in z.namelist():
            if not name.lower().startswith('tables/') or not name.lower().endswith('.xml'):
                continue
            source = ET.fromstring(read(z, name))
            if source.tag != 'database':
                raise ValueError(f'Unsupported level table: {name}')
            output = ET.Element(source.tag, source.attrib)
            for container in source:
                ET.SubElement(output, container.tag, container.attrib)
            result[name.lower()] = xml(output)
    if not result:
        raise ValueError('Native level contains no level-local table schemas')
    return result


def campaign_spawn_objects(data, spawn):
    """Use the native SpawnPoint lifecycle; save restoration needs no Lua teleport."""
    root = ET.fromstring(data)
    if root.tag != 'Objects':
        raise ValueError('Unsupported level entity container')
    name = 'gluemapper_new_game_spawn'
    for entity in list(root):
        if entity.get('Name') == name:
            if entity.get('EntityClass') != 'SpawnPoint':
                raise ValueError('Campaign spawn name conflicts with an existing entity')
            root.remove(entity)
        elif entity.get('EntityClass') == 'SpawnPoint':
            raise ValueError('Converted world already has another SpawnPoint; resolve start selection explicitly')
    entity = ET.SubElement(root, 'Entity', Name=name, EntityClass='SpawnPoint',
                           Pos=spawn['position'], Rotate=spawn['rotation'], PerInstanceStreamable='0')
    # Match the source single-player start. KCD2 SinglePlayer.OnClientConnect
    # selects the first registered default spawn and applies its view angles.
    ET.SubElement(entity, 'Properties', bEnabled='1', bInitialSpawn='0',
                  bDoVisTest='1', bSaved_by_game='0', groupName='', teamName='')
    return xml(root)


def add_level_tables(source, destination, target, spawn=None):
    """Preserve converted records, add native containers and optionally the start."""
    shells = level_table_shells(target)
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(destination, 'x', zipfile.ZIP_STORED) as out:
        existing = {name.lower() for name in incoming.namelist()}
        if spawn is not None and 'objects_mission0.xml' not in existing:
            raise ValueError('Converted world is missing objects_mission0.xml for the opening spawn')
        for name in incoming.namelist():
            data = read(incoming, name)
            if spawn is not None and name.lower() == 'objects_mission0.xml':
                data = campaign_spawn_objects(data, spawn)
            out.writestr(name, data)
        for name, data in shells.items():
            if name not in existing:
                out.writestr(name, data)
    return sorted(set(shells) - existing)


def mounted_level_tables(level_pak, mod_id):
    """Expose level tables at the path used by retail's database loader.

    Retail discovers a mod level as Mods/<id>/Data/Levels/<level>, then its
    database loader prefixes that path with Data/. Root Data archives must
    therefore contain this mod-relative alias. Read the packaged tables so
    existing converted records, not the empty bootstrap shells, are exposed.
    """
    if safe_name(mod_id) != mod_id or '/' in mod_id:
        raise ValueError('Invalid mod identity for level table mounting')
    with zipfile.ZipFile(level_pak) as z:
        return {f'Mods/{mod_id}/Data/Levels/{LEVEL}/{name}': read(z, name)
                for name in z.namelist() if name.lower().startswith('tables/')}
