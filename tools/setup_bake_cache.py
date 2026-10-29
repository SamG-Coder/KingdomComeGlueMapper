"""Reuse completed voxel shards while preserving the new package's quest data."""
from pathlib import Path
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile

from setup_build_cache import fingerprint, file_hash, copy_payload

SOURCES = ('setup_bake_cache.py','setup_baking.py','world_surface_scene.py',
           'world_surface_landscape.py','stage_world_surfaces.py','vegetation_gi.py',
           'compiled_terrain.py','water_volumes.py','vegetation.py','world_voxels.py',
           'validate_world_surfaces.py','clothing_regions.py','voxel_lighting.py')


def key(level, game, overrides, preset):
    from geometry_opacity import Assets
    from world_surface_tiles import code_digest, gpu_configuration
    from upgrade_map import read
    assets = Assets(Path(game)/'Data', overrides)
    # Same effective precedence as the voxelizer. Archive names, timestamps and
    # job paths aren't content. ZIP CRC/size is its existing catalog contract.
    entries = {}
    try:
        for path in sorted({v[0] for v in assets.entries.values() if v is not None}):
            with zipfile.ZipFile(path) as z:
                for item in z.infolist():
                    name = item.filename.replace('\\','/').lower()
                    if name.startswith(('quests/','scripts/','ai/','libs/tables/')): continue
                    if assets.entries.get(name) == (path,item.filename):
                        entries[name] = [item.CRC,item.file_size]
        for name, value in assets.entries.items():
            if value is None: entries[name] = 'ambiguous'
    finally: assets.close()
    with zipfile.ZipFile(Path(level)/'level.pak') as z:
        import hashlib
        geometry = {n:hashlib.sha256(read(z,n)).hexdigest()
                    for n in ('terrain/hlods.dat','leveldata.xml')}
    # Terrain includes height, roads, vegetation transforms and their tables.
    with zipfile.ZipFile(Path(level)/'terrain.pak') as z:
        terrain = sorted((i.filename.replace('\\','/').lower(),i.CRC,i.file_size) for i in z.infolist())
    return fingerprint(dict(version=1,preset=preset,geometry=geometry,terrain=terrain,
        assets=entries,code=code_digest(),
        sources={n:file_hash(Path(__file__).with_name(n)) for n in SOURCES},
        backend='auto',samples=2,experimental_base_materials=True,gpu=gpu_configuration()))


def restore(hit, level, output):
    """Only immutable SVO shards are cached; reconstruct mutable world archives."""
    from build_horse_trader_test import rewrite
    from world_voxels import configure_environment
    from vegetation_gi import upgrade_terrain
    from upgrade_map import read
    payload, report = hit
    packages = Path(output)/'packages'
    copy_payload(payload, packages)
    with zipfile.ZipFile(Path(level)/'level.pak') as z:
        mission = read(z,'mission_mission0.xml')
    rewrite(Path(level)/'level.pak',packages/'level.pak',
            {'mission_mission0.xml':configure_environment(mission,surface_channels=True)})
    if report.get('vegetation_gi_upgrade') is not None:
        with zipfile.ZipFile(Path(level)/'terrain.pak') as z:
            data,_ = upgrade_terrain(read(z,'terrain/terrain.dat'))
        rewrite(Path(level)/'terrain.pak',packages/'terrain.pak',{'terrain/terrain.dat':data})
    return report


def publish(cache, identity, output, report, cancelled, *, scene_identity=None, preset=None, level=None):
    files = {p.name:p for p in (Path(output)/'packages').glob('svo-part*.pak')}
    if not files: raise ValueError('No completed voxel shards to cache')
    metadata = {k:report.get(k) for k in ('tiles','limitations','vegetation_gi_upgrade')}
    if scene_identity is not None:
        metadata.update(scene_identity=scene_identity,preset=preset,level=level)
    cache.publish(identity, files, metadata, cancelled)


def scene_identity(scene, preset):
    """Fingerprint the planned static scene and its actual material dependencies.

    Excludes source archive/job paths and unreferenced NPC, quest and inventory
    assets. NPZ member checksums exclude the ZIP wrapper's creation timestamp.
    """
    from geometry_opacity import Assets
    from world_surface_tiles import code_digest, gpu_configuration
    scene = Path(scene)
    plan = json.loads((scene/'plan.json').read_text())
    geometry = {k:plan[k] for k in ('tiles','vegetation','category_policy') if k in plan}
    if geometry.get('vegetation'):
        # The actual array members are fingerprinted below. Its outer ZIP hash
        # can change when the same arrays are repackaged by another job.
        geometry['vegetation'] = {k:v for k,v in geometry['vegetation'].items() if k!='sha256'}
    meshes, material_names = {}, set()
    for path in sorted((scene/'meshes').glob('*.json')):
        record = json.loads(path.read_text())
        material_names.update(name if name.endswith('.mtl') else name+'.mtl' for name,_ in record['bindings'])
        meshes[path.stem] = record
    arrays = {}
    # Mesh cache keys already identify source geometry + material bindings;
    # opening thousands of derived NPZ files adds minutes of antivirus/file I/O.
    # The scene-wide vegetation array has no per-mesh source key, so include it.
    for path in sorted(scene.glob('*.npz')):
        with zipfile.ZipFile(path) as archive:
            arrays[path.relative_to(scene).as_posix()] = sorted((i.filename,i.CRC,i.file_size) for i in archive.infolist())
    assets = Assets(plan['pak_root'], plan['overrides'])
    materials, dependencies, textures = {}, {}, set()
    try:
        for name in sorted(material_names):
            blob = assets.get(name)
            materials[name] = hashlib.sha256(blob).hexdigest()
            textures.update(t.get('File').replace('\\','/').lower() for t in ET.fromstring(blob).iter('Texture') if t.get('File'))
        for name, location in assets.entries.items():
            base = re.sub(r'\.(?:\d+a?|a)$', '', name)
            if name not in textures and base not in textures: continue
            if location is None:
                dependencies[name] = 'ambiguous'; continue
            archive_path, member = location
            # Retain only ZIP metadata here; checking the cache payload hashes
            # is separate from identifying source texture content.
            dependencies[name] = location
        by_archive = {}
        for name, location in dependencies.items():
            if location != 'ambiguous': by_archive.setdefault(location[0],[]).append((name,location[1]))
        for archive_path, members in by_archive.items():
            with zipfile.ZipFile(archive_path) as archive:
                for name, member in members:
                    info = archive.getinfo(member)
                    dependencies[name] = [info.CRC,info.file_size]
    finally: assets.close()
    return fingerprint(dict(version=1,preset=preset,geometry=geometry,meshes=meshes,arrays=arrays,
        placements=file_hash(scene/'placements.json'),materials=materials,textures=dependencies,
        code=code_digest(),sources={n:file_hash(Path(__file__).with_name(n)) for n in SOURCES
                                   if n not in ('setup_bake_cache.py','setup_baking.py')},
        samples=2,experimental_base_materials=True,gpu=gpu_configuration()))


def matching_workspace_cache(cache, scene, preset, identity=None, notify=lambda message: None):
    """Discover complete cache payloads directly; the originating job is optional.

    Legacy caches recorded only output hashes. They may be reused as existing
    lighting after tile-layout checks, but never labelled an input-verified hit.
    Modern entries must match their recorded scene identity.
    """
    from validate_world_surfaces import stem
    plan = json.loads((Path(scene)/'plan.json').read_text())
    if plan.get('errors') or plan.get('pending_categories') or not plan.get('tiles'):
        notify('Cannot reuse lighting for an incomplete or failed scene plan')
        return None
    expected = {'terrain/svo/'+stem(job)+ext for job in plan['tiles'] for ext in ('.idx','.vox')}
    matches = []
    for pointer in sorted(cache.folder.glob('*.json')):
        if not re.fullmatch('[a-f0-9]{64}',pointer.stem): continue
        try:
            entry = json.loads(pointer.read_text())['entry']
            if Path(entry).name != entry or not entry.startswith(pointer.stem+'-'): continue
            manifest = json.loads((cache.folder/entry/'manifest.json').read_text())
            meta = manifest['metadata']
            if meta.get('preset',preset) != preset or meta.get('level',Path(plan['level']).name) != Path(plan['level']).name:
                notify('Skipping lighting cache for a different level or preset: '+pointer.stem); continue
            if meta.get('scene_identity'):
                if identity is None:
                    notify('Checking the static scene and its referenced materials')
                    identity = scene_identity(scene,preset)
                if meta['scene_identity'] != identity:
                    notify('Skipping lighting cache: static scene, materials or baker settings changed'); continue
            if meta.get('tiles') != len(plan['tiles']):
                notify('Skipping lighting cache: tile count differs'); continue
            notify('Checking cached lighting archives: '+entry)
            hit = cache.lookup(pointer.stem)
            if hit is None:
                notify('Skipping lighting cache: missing, incomplete or changed payload'); continue
            payload, meta = hit
            shards = sorted(payload.glob('svo-part*.pak'))
            if not shards or set(p.name for p in payload.iterdir()) != {p.name for p in shards}: continue
            actual = set()
            for path in shards:
                with zipfile.ZipFile(path) as archive:
                    names = [n.replace('\\','/').lower() for n in archive.namelist()]
                    if len(names) != len(set(names)) or actual.intersection(names):
                        raise ValueError('Duplicate cached lighting tile')
                    actual.update(names)
            if actual != expected:
                notify('Skipping lighting cache: tile layout differs'); continue
            mode = 'scene_identity' if meta.get('scene_identity') else 'legacy_output_and_tile_layout'
            matches.append((payload,dict(meta,cache_key=pointer.stem,cache_validation=mode,
                input_compatibility='verified' if mode=='scene_identity' else 'not_recorded',
                imported_from=str(cache.folder)),manifest['files']))
        except (OSError,ValueError,KeyError,TypeError,AttributeError,zipfile.BadZipFile) as error:
            notify('Skipping invalid lighting cache: '+str(error))
    verified = [r for r in matches if r[1]['input_compatibility']=='verified']
    matches = verified or matches
    if not matches: return None
    if any(row[2] != matches[0][2] for row in matches[1:]):
        notify('Multiple different lighting caches match the tile layout; rebuilding rather than choosing arbitrarily')
        return None
    return matches[0][:2]
