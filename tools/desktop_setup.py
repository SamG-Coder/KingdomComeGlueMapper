"""Desktop setup backend. All conversion output stays in a private job directory."""
from contextlib import redirect_stdout, redirect_stderr, nullcontext
import cProfile
from datetime import datetime
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import pstats
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

from setup_campaign import MOD_ID, RECEIPT, build_campaign, validate_games, verify_package
import setup_progress

APP_VERSION = '0.3.22'
GIB = 1024 ** 3
BUILD_WORKSPACE_GIB = 200
INSTALL_STAGING_GIB = 64
CONVERTERS = ('upgrade_map', 'build_vegetation_probe', 'build_water_probe',
              'build_buildings_probe', 'build_water_surface_probe', 'complete_instances',
              'build_entity_probe', 'build_landscape_surface_probe', 'repair_tree_materials',
              'world_ledges',
              'region_travel', 'prepare_spatial_hlod', 'build_recipe', 'campaign_start_policy',
              'setup_package_upgrade', 'setup_tavern_upgrade', 'campaign_merchant_work',
              'setup_merchant_upgrade', 'source_merchant_upgrade', 'character_station_work',
              'campaign_dialogue_requests', 'campaign_script_import', 'item_icons', 'item_icon_atlas',
              'smart_area_registration', 'area_geometry_contract',
              'hlod_spatial_metadata', 'lod_ratio_metadata', 'lod_ratio_repair', 'setup_section_repair', 'terrain_object_tree',
              'terrain_surface_placement', 'setup_animal_population',
              'build_animal_population_upgrade', 'animal_population',
              'animal_population_sources', 'animal_native_templates', 'setup_profile_population',
              'build_profile_population_upgrade', 'profile_population_upgrade', 'character_market_jobs',
              'character_city_work', 'character_city_benches', 'character_seated_work', 'character_shop_guard_work',
              'character_fence_work', 'character_home_rest', 'character_farmer_work', 'character_horse_handler_work',
              'character_chopping_work', 'character_chopping_source', 'character_chopping_native',
              'character_household_work', 'character_work_pool',
              'character_water_source', 'character_water_native', 'character_water_work',
              'character_sleep_work', 'character_sleep_clock', 'character_sleep_beds', 'character_sleep_native',
              'setup_quest_starts', 'campaign_quest_start_placement',
              'setup_opening_clock', 'campaign_opening_clock',
              'setup_training_upgrade', 'campaign_training_handoff', 'campaign_training_host',
              'campaign_training_worker', 'campaign_training_equipment', 'campaign_training_schedule',
              'campaign_training_native', 'campaign_training_sources',
              'forge_loft', 'forge_loft_layout', 'room_layout', 'world_brush_edits', 'home_bed_assignment',
              'procedural_lights', 'designer_collision_repair', 'training_presentation_repair', 'well_collision_repair', 'market_visual_repair',
              'setup_static_gate_repair', 'training_handoff_repair',
              'campaign_training_entry', 'training_entry_repair', 'campaign_intro_preparation',
              'character_pub_cleaning', 'character_pub_rest', 'setup_pub_cleaning', 'campaign_work_speech',
              'source_work_coverage', 'setup_work_coverage', 'character_dice_resources',
              'character_dice_policy', 'character_gambler_work', 'setup_gambler_work', 'campaign_work_metaroles',
              'campaign_market_speech', 'setup_work_speech', 'gambler_dialogue_registration',
              'gambler_schedule_activation', 'setup_world_encounters', 'world_encounter_graphs',
              'world_encounter_sites', 'world_encounter_registration')


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
    from setup_shaders import tool_root
    sdk = tool_root(target,config.get('mod_tools'))
    workspace = Path(config['workspace']).resolve()
    for game in (source, target):
        if workspace.is_relative_to(game) or game.is_relative_to(workspace):
            raise ValueError('Choose a build folder outside both game installations.')
    if action == 'build_travel':
        world = travel_world(config)
        for name in ('level.pak', 'terrain.pak'):
            if not (world / name).is_file():
                raise ValueError('Region travel needs an installed converted world. Build and install the base world first: ' + str(world))
        if game_running(): raise ValueError('Close Kingdom Come before installing region travel.')
        if (target/'Mods/gluemappertravel').exists():
            raise ValueError('A legacy separate gluemappertravel mod is installed. Move it outside Mods before installing the combined mod.')
        installed = target/'Mods'/MOD_ID
        verify_package(installed)
        needed = sum(p.stat().st_size for p in world.glob('*.pak'))
        base_bytes = sum(p.stat().st_size for p in installed.rglob('*') if p.is_file())
        needed += (target / 'Data/Levels/trosecko/level.pak').stat().st_size
        needed += (source / 'Data/Levels/rataje/recast.pak').stat().st_size + GIB
        needed += base_bytes
        if free_space(workspace) < needed:
            raise ValueError(f'Region travel build needs at least {needed / GIB:.1f} GiB free.')
        staging = base_bytes + needed
        if workspace.anchor.lower() == target.anchor.lower(): staging += needed
        if free_space(target) < staging:
            raise ValueError('Insufficient space to stage the combined travel mod and preserve the installed backup.')
        return f'KCD2 {version}. Build and install region travel into {installed}.'
    if action != 'build_only':
        if game_running(): raise ValueError('Close Kingdom Come before installing the mod.')
        installed = target / 'Mods' / MOD_ID
        if installed.exists():
            receipt = json.loads((installed / RECEIPT).read_text(encoding='utf-8')) if (installed / RECEIPT).is_file() else {}
            if receipt.get('owner') != MOD_ID:
                raise ValueError('Existing mod folder is not an owned setup installation; preserve it manually.')
    build_bytes = BUILD_WORKSPACE_GIB * GIB if action != 'install' else 100 * 1024 ** 2
    install_bytes = INSTALL_STAGING_GIB * GIB
    if action == 'install':
        package = Path(config.get('package', '')).resolve()
        receipt_path = package / RECEIPT
        if not receipt_path.is_file(): raise ValueError('Choose a generated package containing gluemapper-install.json.')
        if not json.loads(receipt_path.read_text(encoding='utf-8')).get('travel_bridge'):
            raise ValueError('This is an old world-only package. Use Build and install to generate the complete world and travel mod.')
        install_bytes = sum(p.stat().st_size for p in package.rglob('*') if p.is_file()) + GIB
    if free_space(workspace) < build_bytes:
        raise ValueError(f'Build folder needs at least {build_bytes / GIB:.1f} GiB free; {free_space(workspace) / GIB:.1f} GiB available.')
    if action != 'build_only':
        needed = install_bytes + (build_bytes if workspace.anchor.lower() == target.anchor.lower() else 0)
        if free_space(target) < needed:
            raise ValueError(f'KCD2 drive needs {needed / GIB:.1f} GiB free for staging; {free_space(target) / GIB:.1f} GiB available. Existing installs are backed up.')
    return f'KCD2 {version}. Modding Tools: {sdk}. Build drive: {free_space(workspace) / GIB:.1f} GiB free. Mod destination: {target / "Mods" / MOD_ID}'


def travel_world(config):
    return Path(config['kcd2']).resolve() / 'Mods' / MOD_ID / 'Data/Levels/kcd1_rataje'


def lighting_cache(build):
    """Probe conversions shared by runs in one workspace, keyed by the converter's code.

    Cubemaps entries are keyed by their input textures only, so the folder name
    includes a hash of lighting_cubemaps.py: a changed converter starts afresh.
    """
    code = hashlib.sha256(Path(__file__).with_name('lighting_cubemaps.py').read_bytes()).hexdigest()[:16]
    from setup_build_cache import root, forced
    if root() is not None:
        # Forced runs get a separate scratch cache; ordinary runs share inputs.
        if forced(): return Path(build).parent/'lighting-cache-rebuilt'/code
        return root()/'cubemaps'/code
    return Path(build).resolve().parents[1] / 'lighting-cache' / code


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
        ('Designer collision and barrier volumes', 'build_buildings_probe', common + ['--base-level', 'setup_landscape', '--level', 'setup_designer', '--all', '--designer-collision']),
        ('Doors and additional object visuals', 'build_entity_probe', common + ['--base-level', 'setup_designer', '--level', 'setup_world', '--character-visuals']),
        ('Tree material compatibility', 'repair_tree_materials', ['--data', str(data), '--backup', str(Path(build).parent / 'tree-material-backup'), '--apply']),
        ('Spatial groups and distant proxies', 'prepare_spatial_hlod', common + ['--level', 'setup_world']),
        ('Vault points and ledge data', 'world_ledges', common + ['--data', str(data), '--level', 'setup_world', '--apply']),
    ]


def install_transaction(package, target, notify=lambda *args: None, cancelled=lambda: False):
    """Stage and verify first, then swap only the owned mod, preserving rollback."""
    package, target = Path(package).resolve(), Path(target).resolve()
    # Package bytes are hashed while staging below, so the package is read only once.
    receipt = verify_package(package, hashes=False)
    from setup_pak_limits import validate as validate_pak_limits
    validate_pak_limits(package)
    from build_recipe import require_current
    require_current(receipt)
    if receipt.get('kind') != 'retail-campaign-package':
        raise ValueError('Select a full campaign package, not a loader-only probe')
    from setup_campaign import game_version
    if receipt['target_version'] != game_version(target): raise ValueError('Package/game version mismatch')
    mods = target / 'Mods'
    destination = mods / MOD_ID
    if mods.resolve() != mods or mods.is_symlink() or destination.resolve() != destination:
        raise ValueError('Refusing redirected mod directory')
    order = mods / 'mod_order.txt'
    legacy = mods/'gluemappertravel'
    if order.is_symlink() or order.resolve() != order:
        raise ValueError('Refusing redirected mod order file')
    migrate_legacy = bool(receipt.get('travel_bridge')) and legacy.exists()
    if migrate_legacy:
        import xml.etree.ElementTree as ET
        if legacy.resolve() != legacy or legacy.is_symlink():
            raise ValueError('Refusing redirected legacy mod directory')
        if not (legacy/'mod.manifest').is_file() or ET.parse(legacy/'mod.manifest').findtext('info/modid') != 'gluemappertravel':
            raise ValueError('Unrecognized legacy travel folder; preserve it manually before installing')
    old_order = order.read_bytes() if order.exists() else None
    new_order = old_order
    if old_order is not None:
        lines = old_order.decode('utf-8-sig').splitlines()
        if receipt.get('travel_bridge'):
            lines = [line for line in lines if line.strip().lower() != 'gluemappertravel']
        if MOD_ID not in [line.strip().lower() for line in lines]: lines.append(MOD_ID)
        new_order = ('\n'.join(lines)+'\n').encode('utf-8')
    if game_running(): raise ValueError('Close the game before installing')
    previous_receipt = verify_package(destination) if destination.exists() else {'files': {}}
    if package == destination or package.is_relative_to(destination): raise ValueError('Select a separate source package')
    total = sum((package / n).stat().st_size for n in receipt['files'])
    reusable = {n for n,h in receipt['files'].items() if previous_receipt['files'].get(n) == h}
    remaining_copy = sum((package / n).stat().st_size for n in receipt['files'] if n not in reusable)
    remaining_copy += (package / RECEIPT).stat().st_size
    if free_space(target) < remaining_copy + GIB: raise ValueError('Insufficient free space to stage the mod safely')
    mods.mkdir(exist_ok=True)
    backup = None
    legacy_backup = None
    order_backup = None
    with tempfile.TemporaryDirectory(prefix='.gluemapper-install-', dir=target) as temporary:
        stage = Path(temporary) / MOD_ID
        count = 0
        for name in [*receipt['files'], RECEIPT]:
            if cancelled(): raise Cancelled('Installation cancelled before replacing the mod')
            dest = stage / name; dest.parent.mkdir(parents=True, exist_ok=True)
            linked = False
            if name in reusable:
                try:
                    os.link(destination / name, dest)
                    linked = True
                except OSError:
                    if free_space(target) < remaining_copy + (package/name).stat().st_size + GIB:
                        raise ValueError('Cannot reuse unchanged files on this filesystem; insufficient space for a copy')
            hashed = hashlib.sha256()
            with (package / name).open('rb') as inp, (nullcontext(None) if linked else dest.open('xb')) as out:
                while chunk := inp.read(4 * 1024 ** 2):
                    if cancelled(): raise Cancelled('Installation cancelled before replacing the mod')
                    if out is not None: out.write(chunk)
                    hashed.update(chunk); count += len(chunk)
                    notify('Verifying reused mod files' if linked else 'Copying mod files', min(count, total), total, name)
            if name != RECEIPT and hashed.hexdigest() != receipt['files'][name]:
                raise ValueError(f'Package changed: {name}')
            if name not in reusable: remaining_copy -= (package/name).stat().st_size
        verify_package(stage)
        if cancelled(): raise Cancelled('Installation cancelled before replacing the mod')
        if game_running(): raise ValueError('Game started during setup; close it and retry installation')
        # Keep rollback only for this transaction. Generated mods are reproducible;
        # successful installs must not accumulate full historical mod directories.
        backup_path = Path(temporary)/'previous-mod'
        if new_order != old_order:
            order_backup = backup_path.with_name(backup_path.name+'-mod_order.txt')
            order_backup.write_bytes(old_order)
            staged_order = Path(temporary)/'mod_order.txt'
            staged_order.write_bytes(new_order)
        if destination.exists():
            backup = backup_path
            destination.rename(backup)
        try:
            if migrate_legacy:
                legacy_backup = backup_path.with_name(backup_path.name+'-legacy-travel')
                legacy.rename(legacy_backup)
            stage.rename(destination)
            if new_order != old_order: staged_order.replace(order)
        except BaseException:
            if destination.exists(): destination.rename(stage)
            if legacy_backup is not None and legacy_backup.exists(): legacy_backup.rename(legacy)
            if backup is not None: backup.rename(destination)
            raise
    return {'installed': str(destination), 'backup': None,
            'shader_launcher': 'setup' if (destination/'ShaderCache/shader-build.json').is_file() else None,
            'legacy_backup': None, 'mod_order_backup': None}


class EventLog:
    """Structured events plus setup.log, timing every stage; profile=True also saves cProfile output."""
    def __init__(self, path, profile=False):
        self.stream = Path(path).open('a', encoding='utf-8', buffering=1)
        self.text = Path(path).with_name('setup.log').open('a', encoding='utf-8', buffering=1)
        self.last_progress = 0
        self.directory, self.profile = Path(path).parent, profile
        self.stage, self.times = None, []

    def emit(self, kind, **values):
        if kind in ('stage', 'done', 'error', 'cancelled'):
            self.finish_stage('failed' if kind == 'error' else 'cancelled' if kind == 'cancelled' else 'complete')
        if kind in ('done', 'error', 'cancelled') and self.times:
            self.summarize_times()
        self.stream.write(json.dumps(dict(kind=kind, time=time.time(), **values)) + '\n')
        if kind != 'progress':
            self.text.write(f'[{datetime.now():%H:%M:%S}] {values.get("message", values.get("label", kind))}\n')
            if 'traceback' in values: self.text.write(values['traceback'] + '\n')
        if kind == 'stage':
            profiler = cProfile.Profile() if self.profile else None
            self.stage = (values['label'], time.perf_counter(), profiler)
            if profiler: profiler.enable()

    def finish_stage(self, status='complete'):
        if self.stage is None: return
        label, started, profiler = self.stage
        self.stage = None
        seconds = round(time.perf_counter() - started, 1)
        if profiler:
            profiler.disable()
            name = f'{len(self.times):02d}-' + re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')[:60]
            (self.directory / 'profiles').mkdir(exist_ok=True)
            report = io.StringIO()
            stats = pstats.Stats(profiler, stream=report)
            stats.sort_stats('tottime').print_stats(40)
            stats.sort_stats('cumulative').print_stats(40)
            (self.directory / 'profiles' / (name + '.txt')).write_text(report.getvalue(), encoding='utf-8')
            stats.dump_stats(self.directory / 'profiles' / (name + '.prof'))
        self.times.append(dict(label=label, seconds=seconds, status=status))
        verb={'complete':'Finished','failed':'Failed','cancelled':'Cancelled'}[status]
        self.emit('log', message=f'{verb} {label} '+('in' if status=='complete' else 'after')+f' {seconds:.1f} s', label=label, seconds=seconds, status=status)

    def summarize_times(self):
        (self.directory / 'stage-times.json').write_text(json.dumps(self.times, indent=2), encoding='utf-8')
        slowest = sorted(self.times, key=lambda t: -t['seconds'])[:5]
        self.emit('log', message=f'Stage times: {sum(t["seconds"] for t in self.times):.0f} s total; slowest '
                  + ', '.join(f'{t["label"]} {t["seconds"]:.0f} s' for t in slowest))

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
    original_sdk = os.environ.get('KCD2_MOD_TOOLS')
    # Workers are separate processes; keep the selected SDK consistent with preflight.
    if config.get('mod_tools'):os.environ['KCD2_MOD_TOOLS']=config['mod_tools']
    job = config_path.parent
    events = EventLog(job / 'events.jsonl', profile=os.environ.get('GLUEMAPPER_PROFILE') == '1')
    output = LogWriter(events)
    setup_progress.sink = events.progress
    cancel = lambda: (job / 'cancel.requested').exists()
    original_cwd, original_argv = Path.cwd(), sys.argv[:]
    shaders = None
    try:
        events.emit('log', message=preflight(config))
        os.chdir(job)
        from setup_build_cache import environment
        with redirect_stdout(output), redirect_stderr(output), environment(config):
            if config['action'] == 'build_travel':
                from region_travel import build
                from travel_bridge_package import combine
                if cancel(): raise Cancelled('Cancelled before building region travel')
                events.emit('stage', index=0, total=3, label='Build region travel, coachman and road arrivals')
                events.emit('log', message='Reading both games and the installed converted world. Existing mods are not modified.')
                destination = job / 'region-travel'
                build(config['kcd1'], config['kcd2'], travel_world(config), destination, modid=MOD_ID)
                if cancel(): raise Cancelled('Travel build retained; cancellation requested before completion')
                events.emit('stage', index=1, total=3, label='Combine world and travel as one verified mod')
                package = combine(Path(config['kcd2'])/'Mods'/MOD_ID, destination/MOD_ID,
                                  config['kcd2'], job/'package')
                from build_recipe import run as run_recipe
                run_recipe(config['kcd1'], config['kcd2'], package, job/'gameplay-recipe', events.progress, cancel)
                if cancel(): raise Cancelled('Combined package retained; cancelled before installation')
                events.emit('stage', index=2, total=3, label='Install combined world and travel mod')
                result = install_transaction(package, config['kcd2'], events.progress, cancel)
                events.emit('done', message='Region travel installed in KingdomComeGlueMapper. Continue your Trosky save and talk to the coachman: Travel to the Rattay region.',
                            package=str(package), **result)
                return 0
            if config['action'] != 'install':
                build = job / 'conversion'
                (build / 'Data').mkdir(parents=True)
                paths = job / 'game-paths.json'
                paths.write_text(json.dumps(dict(schema=1, kcd1=config['kcd1'], kcd2=config['kcd2'], build=str(build))), encoding='utf-8')
                plan = conversion_plan(paths, build)
                os.environ['GLUEMAPPER_LIGHTING_CACHE'] = str(lighting_cache(build))
                # The shader compiler reads only the installed games (and the shared
                # cache), so it runs beside the conversion and the recipe joins it at
                # its shader step.
                from setup_shaders import BackgroundBuild
                shaders = BackgroundBuild(config['kcd2'], job / 'shaders', cancel)
                total = len(plan) + 3 + (config['action'] == 'build_install')
                for index, (label, module, args) in enumerate(plan):
                    if cancel(): raise Cancelled('Cancelled after the previous conversion stage')
                    events.emit('stage', index=index, total=total, label=label)
                    sys.argv = [module, *args]
                    importlib.import_module(module).main()
                    output.flush()
                if cancel(): raise Cancelled('Cancelled before packaging')
                events.emit('stage', index=len(plan), total=total, label='Package converted world')
                base = job / 'world-package'
                build_campaign(config['kcd1'], config['kcd2'], build / 'Data', 'setup_world', base, start_probe=False)
                if cancel(): raise Cancelled('World package retained; cancelled before travel build')
                from region_travel import build as build_bridge
                from travel_bridge_package import combine
                events.emit('stage', index=len(plan)+1, total=total, label='Build coach travel, arrivals and Rattay services')
                destination = job / 'region-travel'
                build_bridge(config['kcd1'], config['kcd2'], base/'Data/Levels/kcd1_rataje', destination, modid=MOD_ID)
                if cancel(): raise Cancelled('Travel package retained; cancelled before combining')
                events.emit('stage', index=len(plan)+2, total=total, label='Combine and verify one complete mod')
                package = combine(base, destination/MOD_ID, config['kcd2'], job/'package')
                from build_recipe import run as run_recipe
                run_recipe(config['kcd1'], config['kcd2'], package, job/'gameplay-recipe', events.progress, cancel,
                           converted_data=job/'conversion/Data', shaders=shaders)
            else:
                package = Path(config['package']).resolve()
                total = 1
            result = {'package': str(package)}
            if config['action'] != 'build_only':
                if cancel(): raise Cancelled('Cancelled before installation')
                events.emit('stage', index=total - 1, total=total, label='Install verified mod')
                result.update(install_transaction(package, config['kcd2'], events.progress, cancel))
            shader_hint = (' Use Launch mod in the setup Library to enable its shaders.'
                           if (package/'ShaderCache/shader-build.json').is_file() else '')
            events.emit('done', message=('Complete world and travel package built.' if config['action']=='build_only' else 'World and travel installed.') + shader_hint + ' Continue your Trosky save and talk to the coachman: Travel to the Rattay region.', **result)
        return 0
    except (Cancelled, InterruptedError) as error:
        events.emit('cancelled', message=str(error)); return 2
    except BaseException as error:
        events.emit('error', message=str(error), traceback=traceback.format_exc()); return 1
    finally:
        if shaders is not None: shaders.stop()  # never leave ShaderCacheGen running after the job
        if original_sdk is None:os.environ.pop('KCD2_MOD_TOOLS',None)
        else:os.environ['KCD2_MOD_TOOLS']=original_sdk
        output.flush(); events.stream.close(); events.text.close()
        setup_progress.sink = None
        os.chdir(original_cwd); sys.argv = original_argv
