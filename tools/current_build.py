"""Build and validate one current world + NPC mod set on the workspace drive.

Generated packages are replaced, not archived. The previous current directory
exists only during promotion so a failed rename can be rolled back.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


REPO = Path(__file__).resolve().parents[1]


def paths(root):
    root = Path(root).resolve()
    return dict(root=root, current=root / 'CurrentBuild', cache=root / 'BuildCache',
                work=root / 'BuildWork', logs=root / 'BuildLogs',
                next=root / 'BuildWork/next', previous=root / 'BuildWork/previous',
                backups=root.parent / 'backups/CurrentBuild')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def remove_generated(path, parent):
    """Constrain replacement to one owned subtree; never follow junctions."""
    path, parent = Path(path), Path(parent).resolve()
    resolved = path.resolve()
    if resolved == parent or not resolved.is_relative_to(parent):
        raise ValueError('Generated cleanup escaped its working directory')
    if not path.exists(): return
    for base, directories, files in os.walk(path, followlinks=False):
        for item in (Path(base), *(Path(base) / n for n in directories + files)):
            if item.is_symlink() or item.is_junction():
                raise ValueError('Generated cleanup refuses links or junctions: ' + str(item))
    shutil.rmtree(path)


@contextmanager
def working_environment(layout):
    """Child tools and Python temporaries use D: with this repository, not C:."""
    layout['work'].mkdir(parents=True, exist_ok=True)
    temporary = layout['work'] / 'temp'
    temporary.mkdir(exist_ok=True)
    lock = layout['work'] / 'build.lock'
    with lock.open('x') as stream: stream.write(str(os.getpid()))
    old = {name: os.environ.get(name) for name in ('TEMP', 'TMP', 'TMPDIR')}
    old_tempdir = tempfile.tempdir
    try:
        for name in old: os.environ[name] = str(temporary)
        tempfile.tempdir = str(temporary)
        yield
    finally:
        tempfile.tempdir = old_tempdir
        for name, value in old.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value
        lock.unlink()


def source_digest():
    result = hashlib.sha256()
    for path in sorted((REPO / 'tools').glob('*.py')):
        result.update(path.name.encode()); result.update(path.read_bytes())
    return result.hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_manifest(directory):
    result = {}
    for base, directories, files in os.walk(directory, followlinks=False):
        for name in directories + files:
            path = Path(base) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError('Build must contain real files, not links: ' + str(path))
        for name in files:
            path = Path(base) / name
            relative = path.relative_to(directory).as_posix()
            if relative != 'build.json': result[relative] = file_hash(path)
    return result


def preserve_edits(layout):
    """Back up only changed/extra files, never a copy of unchanged generated PAKs."""
    current = layout['current']
    if not current.exists(): return
    receipt_path = current / 'build.json'
    if not receipt_path.is_file(): raise ValueError('CurrentBuild has no generated-file receipt; preserve it manually first')
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('generated') is not True or 'files' not in receipt:
        raise ValueError('CurrentBuild is not a managed generated build')
    actual = file_manifest(current)
    changed = {name: digest for name, digest in actual.items() if receipt['files'].get(name) != digest}
    missing = sorted(set(receipt['files']) - set(actual))
    if not changed and not missing: return
    backup = layout['backups'] / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(parents=True)
    for name, digest in changed.items():
        destination = backup / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current / name, destination)
        if file_hash(destination) != digest: raise ValueError('Modified-file backup verification failed: ' + name)
    write_json(backup / 'backup.json', dict(reason='User changes to generated output', files=changed, deleted=missing,
                                         previous_build=receipt))


def validate_build(layout, build, config):
    from validate_character_population import validate
    from retail_pak import validate_paks
    build = Path(build)
    world = build / 'Mods/kingdomcomegluemapper'
    travel = build / 'Mods/gluemappertravel'
    for path in (world / 'mod.manifest', world / 'Data/Levels/kcd1_rataje/level.pak',
                 world / 'Data/Levels/kcd1_rataje/terrain.pak'):
        if not path.is_file(): raise FileNotFoundError(path)
    if not list((world / 'Data').glob('kingdomcomegluemapper_world_*.pak')):
        raise ValueError('CurrentBuild is missing the converted world asset shards')
    tables = layout['work'] / 'tables'
    remove_generated(tables, layout['work'])
    checks = validate(layout['cache'] / 'Population', travel, tables)
    checks['retail_archives'] = validate_paks(build / 'Mods')
    layout['logs'].mkdir(parents=True, exist_ok=True)
    with (layout['logs'] / 'native-tables.log').open('w', encoding='utf-8') as log:
        subprocess.run([config.get('powershell', 'pwsh'), '-NoProfile', '-File',
                        str(REPO / 'tools/validate_database_tables.ps1'),
                        '-ModToolsPath', config['mod_tools'], '-TablesPath', str(tables)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    checks['native_tables_verified'] = True
    return checks


def promote(layout):
    """Publish checked output, restoring the old current if the rename fails."""
    current, pending, previous = (layout[k] for k in ('current', 'next', 'previous'))
    if previous.exists():
        raise FileExistsError('Interrupted promotion: inspect BuildWork/previous before proceeding')
    if not (pending / 'build.json').is_file(): raise ValueError('Pending build has no validation receipt')
    preserve_edits(layout)
    if current.exists(): current.rename(previous)
    try:
        pending.rename(current)
    except BaseException:
        if previous.exists(): previous.rename(current)
        raise
    remove_generated(previous, layout['work'])


def publish(layout, config):
    checks = validate_build(layout, layout['next'], config)
    receipt = dict(schema=1, generated=True, checked_at=datetime.now(timezone.utc).isoformat(),
                   population_profile=checks.get('population_profile', 'full'),
                   tools_sha256_at_validation=source_digest(), validation=checks,
                   mods=['kingdomcomegluemapper', 'gluemappertravel'],
                   files=file_manifest(layout['next']),
                   installed=False, runtime_verified=False,
                   pending=['conditional NPC layers and controllers', 'full source daily activities',
                            'non-outfit inventory and weapons', 'voices and dialogue', 'live AI tests'])
    write_json(layout['next'] / 'build.json', receipt)
    promote(layout)
    return receipt


def build(layout, config, refresh_base=False, refresh_population=False, cached_only=False):
    from character_population import stage
    from package_character_population import assemble
    base = layout['cache'] / 'TravelBase'
    population = layout['cache'] / 'Population'
    world = layout['cache'] / 'World/kingdomcomegluemapper'
    profile = config.get('population_profile', 'full')
    if profile not in ('full', 'services-only'):
        raise ValueError('Unknown population build profile: ' + profile)
    if profile == 'services-only' and (refresh_base or refresh_population):
        raise ValueError('The services-only test preserves cached inputs; select full for a refresh')
    if cached_only and (refresh_base or refresh_population):
        raise ValueError('A read-only cached test build cannot refresh its inputs')
    if refresh_population:
        remove_generated(population, layout['cache'])
    if refresh_base or not (base / 'gluemappertravel/mod.manifest').is_file():
        from region_travel import build as travel_build
        candidate = layout['work'] / 'travel-base'
        remove_generated(candidate, layout['work'])
        travel_build(config['source'], config['target'], world / 'Data/Levels/kcd1_rataje', candidate)
        remove_generated(base, layout['cache'])
        candidate.rename(base)
    if profile == 'services-only':
        from population_build_profile import assemble as assemble_services
        remove_generated(layout['next'], layout['work'])
        mods = layout['next'] / 'Mods'
        mods.mkdir(parents=True)
        current_mods = layout['current'] / 'Mods'
        existing = (current_mods / 'gluemappertravel' / 'mod.manifest').is_file()
        # A population A/B test must retain the currently tested scenery. Fresh
        # builds can still generate the same profile from the cached base.
        scenery = current_mods / world.name if existing else world
        shutil.copytree(scenery, mods / world.name)
        assemble_services(base / 'gluemappertravel',
                          (current_mods if existing else base) / 'gluemappertravel',
                          mods / 'gluemappertravel', target=config['target'])
        if not existing:
            from upgrade_world_streaming import upgrade
            upgrade(mods, config, layout['work'])
        return publish(layout, config)
    stage(Path(config['source']), Path(config['target']), base / 'gluemappertravel', population,
          include_conditional=True, cached_only=cached_only)
    remove_generated(layout['next'], layout['work'])
    mods = layout['next'] / 'Mods'
    mods.mkdir(parents=True)
    # Real copies isolate the installed/current world from subsequent cache edits.
    shutil.copytree(world, mods / world.name)
    from travel_world_package import prepare_world_for_travel
    prepare_world_for_travel(mods / world.name)
    assemble(population, mods / 'gluemappertravel', snapshot=cached_only)
    from upgrade_world_streaming import upgrade
    upgrade(mods, config, layout['work'])
    return publish(layout, config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'build', 'publish', 'validate'))
    parser.add_argument('--outputs', type=Path, default=REPO / 'outputs')
    parser.add_argument('--refresh-base', action='store_true')
    parser.add_argument('--refresh-population', action='store_true',
                        help='Regenerate the appearance cache after changing conversion policy')
    parser.add_argument('--cached-only', action='store_true', help='Package completed source conversions for the next in-game checkpoint')
    parser.add_argument('--population-profile', choices=('full', 'services-only'),
                        help='Build with the full population or only original service NPCs; remembered after a successful build')
    args = parser.parse_args()
    layout = paths(args.outputs)
    config = json.loads((layout['cache'] / 'build-inputs.json').read_text())
    if args.population_profile:
        if args.action != 'build': parser.error('--population-profile applies to build only')
        config['population_profile'] = args.population_profile
    if args.action == 'status':
        receipt = layout['current'] / 'build.json'
        print(json.dumps(dict(paths={k: str(v) for k, v in layout.items()},
                              current=json.loads(receipt.read_text()) if receipt.exists() else None), indent=2))
        return
    with working_environment(layout):
        if args.action == 'build': result = build(layout, config, args.refresh_base, args.refresh_population, args.cached_only)
        elif args.action == 'publish': result = publish(layout, config)
        else: result = validate_build(layout, layout['current'], config)
        if args.action == 'build' and args.population_profile:
            write_json(layout['cache'] / 'build-inputs.json', config)
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
