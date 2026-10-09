"""Desktop setup backend. All conversion output stays in a private job directory."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

from setup_campaign import MOD_ID, RECEIPT, build_campaign, validate_games, verify_package
import setup_progress

APP_VERSION = '0.2.0-alpha.2'
GIB = 1024 ** 3
CONVERTERS = ('upgrade_map', 'build_vegetation_probe', 'build_water_probe',
              'build_buildings_probe', 'build_water_surface_probe', 'complete_instances',
              'build_entity_probe', 'build_landscape_surface_probe', 'repair_tree_materials',
              'region_travel')


class Cancelled(Exception):
    pass


def game_running():
    if os.name != 'nt': return False
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq KingdomCome.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode: raise ValueError('Unable to check running games')
    return 'kingdomcome.exe' in result.stdout.lower()


def free_space(path):
    path = Path(path).resolve()
    while not path.exists(): path = path.parent
    return shutil.disk_usage(path).free


def preflight(config):
    source, target = (Path(config[k]).resolve() for k in ('kcd1', 'kcd2'))
    version = validate_games(source, target)
    if version != '1.5.6':
        raise ValueError(f'This experimental release supports KCD2 1.5.6; found {version}.')
    action = config['action']
    if action not in ('build_install', 'build_only', 'install', 'build_travel'):
        raise ValueError('Unknown setup operation')
    workspace = Path(config['workspace']).resolve()
    for game in (source, target):
        if workspace.is_relative_to(game) or game.is_relative_to(workspace):
            raise ValueError('Choose a build folder outside both game installations.')
    if action == 'build_travel':
        world = travel_world(config)
        for name in ('level.pak', 'terrain.pak'):
            if not (world / name).is_file():
                raise ValueError('Region travel needs an installed converted world. Build and install the base world first: ' + str(world))
        needed = sum(p.stat().st_size for p in world.glob('*.pak'))
        needed += (target / 'Data/Levels/trosecko/level.pak').stat().st_size
        needed += (source / 'Data/Levels/rataje/recast.pak').stat().st_size + GIB
        if free_space(workspace) < needed:
            raise ValueError(f'Region travel build needs at least {needed / GIB:.1f} GiB free.')
        return f'KCD2 {version}. Build region travel from {world}. Output requires manual installation; existing mods stay unchanged.'
    if action != 'build_only':
        if game_running(): raise ValueError('Close Kingdom Come before installing the mod.')
        order = target / 'Mods/mod_order.txt'
        if order.exists() and MOD_ID not in [s.strip() for s in order.read_text(encoding='utf-8-sig').splitlines()]:
            raise ValueError(f'Add {MOD_ID} to {order} before installing. Other mods are preserved.')
        installed = target / 'Mods' / MOD_ID
        if installed.exists():
            receipt = json.loads((installed / RECEIPT).read_text(encoding='utf-8')) if (installed / RECEIPT).is_file() else {}
            if receipt.get('owner') != MOD_ID:
                raise ValueError('Existing mod folder is not an owned setup installation; preserve it manually.')
    build_bytes = 60 * GIB if action != 'install' else 100 * 1024 ** 2
    install_bytes = 25 * GIB
    if action == 'install':
        package = Path(config.get('package', '')).resolve()
        receipt_path = package / RECEIPT
        if not receipt_path.is_file(): raise ValueError('Choose a generated campaign package containing gluemapper-install.json.')
        install_bytes = sum(p.stat().st_size for p in package.rglob('*') if p.is_file()) + GIB
    if free_space(workspace) < build_bytes:
        raise ValueError(f'Build folder needs at least {build_bytes / GIB:.1f} GiB free; {free_space(workspace) / GIB:.1f} GiB available.')
    if action != 'build_only':
        needed = install_bytes + (build_bytes if workspace.anchor.lower() == target.anchor.lower() else 0)
        if free_space(target) < needed:
            raise ValueError(f'KCD2 drive needs {needed / GIB:.1f} GiB free for staging; {free_space(target) / GIB:.1f} GiB available. Existing installs are backed up.')
    return f'KCD2 {version}. Build drive: {free_space(workspace) / GIB:.1f} GiB free. Mod destination: {target / "Mods" / MOD_ID}'


def travel_world(config):
    return Path(config['kcd2']).resolve() / 'Mods' / MOD_ID / 'Data/Levels/kcd1_rataje'


def conversion_plan(library, build):
    data = Path(build) / 'Data'
    common = ['--library', str(library)]
    return [
        ('Terrain and original materials', 'upgrade_map', common + ['--output', str(data / 'Levels/setup_terrain'), '--original-materials']),
        ('Trees, grass and merged vegetation', 'build_vegetation_probe', common + ['--base-level', 'setup_terrain', '--level', 'setup_vegetation', '--all', '--merged']),
        ('Water volumes and environment', 'build_water_probe', common + ['--base-level', 'setup_vegetation', '--level', 'setup_water', '--native-water-materials']),
        ('Buildings and fences', 'build_buildings_probe', common + ['--base-level', 'setup_water', '--level', 'setup_buildings', '--all']),
        ('Streams and waterfalls', 'build_buildings_probe', common + ['--base-level', 'setup_buildings', '--level', 'setup_streams', '--all', '--streams-only']),
        ('Water surface compatibility', 'build_water_surface_probe', ['--tools', str(build), '--base-level', 'setup_streams', '--level', 'setup_surfaces', '--suppress-surface', '0x44bfc6118f6b83a9']),
        ('Props and furnishings', 'build_buildings_probe', common + ['--base-level', 'setup_surfaces', '--level', 'setup_props', '--all', '--props-only']),
        ('Remaining foliage and instanced props', 'complete_instances', common + ['--base-level', 'setup_props', '--level', 'setup_instances', '--original-report', str(data / 'Levels/setup_vegetation/vegetation-report.json')]),
        ('Roads and ground decals', 'build_landscape_surface_probe', common + ['--base-level', 'setup_instances', '--level', 'setup_roads', '--all']),
        ('Landscape rocks and riverbeds', 'build_buildings_probe', common + ['--base-level', 'setup_roads', '--level', 'setup_landscape', '--all', '--landscape-designers-only']),
        ('Doors and additional object visuals', 'build_entity_probe', common + ['--base-level', 'setup_landscape', '--level', 'setup_world', '--character-visuals']),
        ('Tree material compatibility', 'repair_tree_materials', ['--data', str(data), '--backup', str(Path(build).parent / 'tree-material-backup'), '--apply']),
    ]


def install_transaction(package, target, notify=lambda *args: None, cancelled=lambda: False):
    """Stage and verify first, then swap only the owned mod, preserving rollback."""
    package, target = Path(package).resolve(), Path(target).resolve()
    receipt = verify_package(package)
    if receipt.get('kind') != 'retail-campaign-package':
        raise ValueError('Select a full campaign package, not a loader-only probe')
    from setup_campaign import game_version
    if receipt['target_version'] != game_version(target): raise ValueError('Package/game version mismatch')
    mods = target / 'Mods'
    destination = mods / MOD_ID
    if mods.resolve() != mods or mods.is_symlink() or destination.resolve() != destination:
        raise ValueError('Refusing redirected mod directory')
    order = mods / 'mod_order.txt'
    if order.exists() and MOD_ID not in [s.strip() for s in order.read_text(encoding='utf-8-sig').splitlines()]:
        raise ValueError('mod_order.txt excludes this mod')
    if game_running(): raise ValueError('Close the game before installing')
    if destination.exists(): verify_package(destination)
    if package == destination or package.is_relative_to(destination): raise ValueError('Select a separate source package')
    total = sum((package / n).stat().st_size for n in receipt['files'])
    if free_space(target) < total + GIB: raise ValueError('Insufficient free space to stage the mod safely')
    mods.mkdir(exist_ok=True)
    backup = None
    with tempfile.TemporaryDirectory(prefix='.gluemapper-install-', dir=target) as temporary:
        stage = Path(temporary) / MOD_ID
        count = 0
        for name in [*receipt['files'], RECEIPT]:
            if cancelled(): raise Cancelled('Installation cancelled before replacing the mod')
            dest = stage / name; dest.parent.mkdir(parents=True, exist_ok=True)
            with (package / name).open('rb') as inp, dest.open('xb') as out:
                while chunk := inp.read(4 * 1024 ** 2):
                    if cancelled(): raise Cancelled('Installation cancelled before replacing the mod')
                    out.write(chunk); count += len(chunk)
                    notify('Copying mod files', min(count, total), total, name)
        verify_package(stage)
        if cancelled(): raise Cancelled('Installation cancelled before replacing the mod')
        if game_running(): raise ValueError('Game started during setup; close it and retry installation')
        if destination.exists():
            backup = target / 'GlueMapper Backups' / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
            backup.parent.mkdir(exist_ok=True)
            destination.rename(backup)
        try:
            stage.rename(destination)
        except OSError:
            if backup is not None: backup.rename(destination)
            raise
    return {'installed': str(destination), 'backup': str(backup) if backup else None}


class EventLog:
    def __init__(self, path):
        self.stream = Path(path).open('a', encoding='utf-8', buffering=1)
        self.text = Path(path).with_name('setup.log').open('a', encoding='utf-8', buffering=1)
        self.last_progress = 0

    def emit(self, kind, **values):
        self.stream.write(json.dumps(dict(kind=kind, time=time.time(), **values)) + '\n')
        if kind != 'progress':
            self.text.write(f'[{datetime.now():%H:%M:%S}] {values.get("message", values.get("label", kind))}\n')
            if 'traceback' in values: self.text.write(values['traceback'] + '\n')

    def progress(self, label, current, total, detail=''):
        now = time.monotonic()
        if now - self.last_progress < 0.2 and current != total: return
        self.last_progress = now
        self.emit('progress', label=label, current=current, total=total, detail=detail)


class LogWriter(io.TextIOBase):
    def __init__(self, events): self.events, self.buffer = events, ''
    def write(self, value):
        self.buffer += value
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            if line.strip(): self.events.emit('log', message=line.rstrip())
        return len(value)
    def flush(self):
        if self.buffer: self.events.emit('log', message=self.buffer); self.buffer = ''


def worker(config_path):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    job = config_path.parent
    events = EventLog(job / 'events.jsonl')
    output = LogWriter(events)
    setup_progress.sink = events.progress
    cancel = lambda: (job / 'cancel.requested').exists()
    original_cwd, original_argv = Path.cwd(), sys.argv[:]
    try:
        events.emit('log', message=preflight(config))
        os.chdir(job)
        with redirect_stdout(output), redirect_stderr(output):
            if config['action'] == 'build_travel':
                from region_travel import build
                if cancel(): raise Cancelled('Cancelled before building region travel')
                events.emit('stage', index=0, total=1, label='Build region travel, coachman and road arrivals')
                events.emit('log', message='Reading both games and the installed converted world. Existing mods are not modified.')
                destination = job / 'region-travel'
                build(config['kcd1'], config['kcd2'], travel_world(config), destination)
                if cancel(): raise Cancelled('Travel build retained; cancellation requested before completion')
                events.emit('done', message='Travel package built. Close the game, back up any existing gluemappertravel folder, then copy the generated gluemappertravel folder into KCD2/Mods. Keep the base mod installed.',
                            package=str(destination / 'gluemappertravel'))
                return 0
            if config['action'] != 'install':
                build = job / 'conversion'
                (build / 'Data').mkdir(parents=True)
                paths = job / 'game-paths.json'
                paths.write_text(json.dumps(dict(schema=1, kcd1=config['kcd1'], kcd2=config['kcd2'], build=str(build))), encoding='utf-8')
                plan = conversion_plan(paths, build)
                total = len(plan) + 1 + (config['action'] == 'build_install')
                for index, (label, module, args) in enumerate(plan):
                    if cancel(): raise Cancelled('Cancelled after the previous conversion stage')
                    events.emit('stage', index=index, total=total, label=label)
                    sys.argv = [module, *args]
                    importlib.import_module(module).main()
                    output.flush()
                if cancel(): raise Cancelled('Cancelled before packaging')
                events.emit('stage', index=len(plan), total=total, label='Package and verify retail mod')
                package = job / 'package'
                build_campaign(config['kcd1'], config['kcd2'], build / 'Data', 'setup_world', package, start_probe=True)
            else:
                package = Path(config['package']).resolve()
                total = 1
            result = {'package': str(package)}
            if config['action'] != 'build_only':
                if cancel(): raise Cancelled('Cancelled before installation')
                events.emit('stage', index=total - 1, total=total, label='Install verified mod')
                result.update(install_transaction(package, config['kcd2'], events.progress, cancel))
            events.emit('done', message='Setup completed. Quests and the original intro are not included yet.', **result)
        return 0
    except Cancelled as error:
        events.emit('cancelled', message=str(error)); return 2
    except BaseException as error:
        events.emit('error', message=str(error), traceback=traceback.format_exc()); return 1
    finally:
        output.flush(); events.stream.close(); events.text.close()
        setup_progress.sink = None
        os.chdir(original_cwd); sys.argv = original_argv
