"""Optional audited surface baking inside a newly built, private map package."""
import json
import hashlib
import os
import uuid
from pathlib import Path
import shutil
from setup_campaign import RECEIPT,digest,verify_package

PRESETS={'none':'No Baking','buildings':'Buildings','all':'All (Forest)'}

def restore_baked_exposure(level_pak,native_pak):
    """Give a baked level native eye adaptation again (#27).

    The map stage caps EV Min because unbaked interiors are dark; once the bake
    lights them, the cap leaves menus and daylight pale. The archive is rewritten
    to a new file, so a hardlinked input package is never modified.
    """
    from interior_exposure import restore
    from setup_package_upgrade import rewrite
    from upgrade_map import read
    import zipfile
    name='mission_mission0.xml'
    if not level_pak.is_file() or not native_pak.is_file():return {'restored_exposure_profiles':0}
    with zipfile.ZipFile(level_pak) as archive:
        if name not in archive.namelist():return {'restored_exposure_profiles':0}
        blob=read(archive,name)
    with zipfile.ZipFile(native_pak) as archive:native=read(archive,name)
    restored,report=restore(blob,native)
    if report['restored_exposure_profiles']:rewrite(level_pak.parent,level_pak.name,{name:restored})
    return report

def upgrade_baked_exposure(package,receipt,game):
    """Give packages baked before restore_baked_exposure their native exposure.

    A level whose bake report already records its exposure is left alone, so
    this runs once per package. Returns the rewritten archives, relative to
    the package, for the caller to re-hash in the receipt.
    """
    native=Path(game)/'Data/Levels/trosecko/level.pak' if game is not None else None
    if native is None or not native.is_file():return []
    changed=[]
    for report in receipt.get('baking',{}).get('levels',[]):
        if 'exposure' in report:continue
        relative=f"Data/Levels/{report['level']}/level.pak"
        report['exposure']=restore_baked_exposure(Path(package)/relative,native)
        if report['exposure']['restored_exposure_profiles']:changed.append(relative)
    return changed

def bake_package(package,game,workspace,preset,progress,cancelled):
    from setup_build_cache import root, Cache
    from setup_resume import job_lock
    if root() is None or preset == 'none':
        return _bake_package(package,game,workspace,preset,progress,cancelled)
    cache = Cache(root()/'lighting')
    with job_lock(cache.folder):
        return _bake_package(package,game,workspace,preset,progress,cancelled,cache)


def checkpoint_workspace(game,workspace,preset,receipt):
    """Use the same input signature for both resume planning and the baker."""
    from world_surface_tiles import code_digest
    policy=[preset,receipt['files'],code_digest()]
    for name in ('world_surface_scene.py','world_surface_landscape.py','stage_world_surfaces.py'):
        policy.append(hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest())
    policy.append([(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(Path(game).glob('Data/**/*.pak'))])
    engine=Path(game)/'Engine/Engine.pak'
    if engine.exists():policy.append((str(engine),engine.stat().st_size,engine.stat().st_mtime_ns))
    signature=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()
    return Path(workspace).resolve()/signature


def _bake_package(package,game,workspace,preset,progress,cancelled,cache=None):
    if preset not in PRESETS:raise ValueError('Unknown baking preset')
    if preset=='none':return
    from world_surface_scene import plan
    from world_surface_landscape import augment as landscape
    from world_surface_coverage import augment as coverage
    from world_surface_tiles import build
    from stage_world_surfaces import stage
    package=Path(package).resolve();workspace=Path(workspace).resolve()
    from setup_resume import restore_swap
    restore_swap(package)
    receipt=verify_package(package)
    if receipt.get('baking',{}).get('preset')==preset:return
    # Separate incompatible plans, while retaining interrupted work for inspection.
    workspace=checkpoint_workspace(game,workspace,preset,receipt)
    workspace.mkdir(parents=True,exist_ok=True)
    overrides=[str(p.resolve()) for p in sorted((package/'Data').glob('*.pak'))]
    reports=[];staged=[]
    levels = sorted((package/'Data/Levels').iterdir())
    destination = receipt.get('travel_bridge', {}).get('destination')
    if destination:
        if Path(destination).name != destination:
            raise ValueError('Invalid playable bake destination')
        levels = [package/'Data/Levels'/destination]
        if not (levels[0]/'terrain.pak').is_file():
            raise ValueError('Playable world terrain is missing')
    for level in levels:
        if not (level/'terrain.pak').exists():continue
        if cancelled():raise InterruptedError('Cancelled before baking '+level.name)
        cache_key = None
        if cache is not None:
            from setup_bake_cache import key, restore, publish
            from setup_build_cache import forced
            progress('Check shared lighting cache',0,0,level.name)
            from setup_gpu import hybrid_environment
            with hybrid_environment(): cache_key = key(level,game,overrides,preset)
            hit = None if forced() else cache.lookup(cache_key)
            if not hit and not forced():
                from setup_cache_import import approved
                hit=approved('lighting',level=level.name,preset=preset)
            if hit:
                output = workspace/('cached-'+uuid.uuid4().hex[:12])
                report = restore(hit,level,output)
                print('Reusing '+PRESETS[preset]+' lighting from '+str(report.get('imported_from',cache.folder)),flush=True)
                staged.append((level.name,output/'packages'))
                reports.append(dict(level=level.name,tiles=report['tiles'],limitations=report['limitations'],
                                    imported_from=report.get('imported_from'),
                                    vegetation_gi_upgrade=report.get('vegetation_gi_upgrade')))
                continue
            print('Lighting cache: '+('regeneration requested' if forced() else
                  'exact input key not found; checking existing workspace caches against the static scene'),flush=True)
        levelwork=workspace/level.name;levelwork.mkdir(exist_ok=True)
        if cache is not None and forced():
            levelwork=levelwork/('rebuild-'+uuid.uuid4().hex[:12]);levelwork.mkdir()
        ready=levelwork/'ready.json'
        if ready.exists():
            attempt=json.loads(ready.read_text())['attempt']
            if Path(attempt).name!=attempt:raise ValueError('Invalid saved bake attempt')
            root=levelwork/attempt
            progress('Inspect bake geometry',0,0,level.name+' · Reusing completed scene plan')
        else:
            root=levelwork/('plan-'+uuid.uuid4().hex[:12])
            progress('Inspect bake geometry',0,0,level.name+' · '+PRESETS[preset])
            plan(level,Path(game)/'Data',root/'base',overrides)
            landscape(root/'base',experimental_base_materials=True)
            coverage(root/'base',root/'scene',include_vegetation=preset=='all')
            scene_plan=json.loads((root/'scene/plan.json').read_text())
            if not scene_plan['errors']:
                pending=ready.with_suffix('.tmp');pending.write_text(json.dumps(dict(attempt=root.name)))
                pending.replace(ready)
        from setup_gpu import hybrid_environment
        scene_key = None
        if cache is not None:
            from setup_bake_cache import scene_identity, matching_workspace_cache
            progress('Check existing workspace lighting',0,0,'Matching static geometry and materials; no previous job required')
            with hybrid_environment():
                hit = None if forced() else matching_workspace_cache(cache,root/'scene',preset,
                    notify=lambda message: progress('Check existing workspace lighting',0,0,message))
            if hit:
                output = workspace/('cached-'+uuid.uuid4().hex[:12])
                progress('Reuse cached lighting',0,0,f'{hit[1]["tiles"]} tiles from {cache.folder}')
                report = restore(hit,level,output)
                print(f'Reusing {report["tiles"]} lighting tiles from {cache.folder}; validation: {report["cache_validation"]}',flush=True)
                if report['input_compatibility']=='not_recorded':
                    print('Existing lighting output hashes and tile layout checked. The older cache did not record scene inputs.',flush=True)
                staged.append((level.name,output/'packages'))
                reports.append(dict(level=level.name,tiles=report['tiles'],limitations=report['limitations'],
                    vegetation_gi_upgrade=report.get('vegetation_gi_upgrade'),cache_key=report['cache_key'],
                    cache_validation=report['cache_validation'],input_compatibility=report['input_compatibility']))
                continue
            print('Building lighting: '+('regeneration requested' if forced() else
                  'no complete matching workspace cache; details are recorded in events.jsonl'),flush=True)
        print('Hybrid bake: compiled CPU below 1,000,000 triangles (2 workers); '
              'OptiX for dense tiles, 64-brick batches with CPU overlap. '
              'GPU failures are logged and fall back to CPU.',flush=True)
        with hybrid_environment():
            results=build(root/'scene',experimental_base_materials=True,backend='auto',workers=2,
                          progress=progress,cancelled=cancelled)
        counts={name:sum(r.get('backend')==name for r in results) for name in ('compiled','optix')}
        print(f'Hybrid bake results: {counts["compiled"]} CPU tiles, {counts["optix"]} OptiX tiles, '
              f'{sum(bool(r.get("gpu_fallback_reason")) for r in results)} GPU fallbacks.',flush=True)
        if any(r['status']!='built' for r in results):raise ValueError('Lighting tile failed; see bake reports. Nothing installed.')
        if cancelled():raise InterruptedError('Cancelled before staging bake')
        output=root/('staged-'+uuid.uuid4().hex[:12])
        report=stage(root/'scene',output)
        if cache is not None:
            progress('Record shared lighting cache',0,0,'Recording static scene inputs for future jobs')
            with hybrid_environment(): scene_key = scene_identity(root/'scene',preset)
            publish(cache,cache_key,output,report,cancelled,
                scene_identity=scene_key,preset=preset,level=level.name)
        staged.append((level.name,output/'packages'))
        reports.append(dict(level=level.name,tiles=report['tiles'],limitations=report['limitations'],
                            vegetation_gi_upgrade=report.get('vegetation_gi_upgrade')))
    if not reports:raise ValueError('No converted levels to bake')
    if cancelled():raise InterruptedError('Cancelled before applying complete bake')
    # Construct a replacement privately. Hardlinks are only read; every changed
    # file is replaced, so failure cannot corrupt the verified input package.
    replacement=package.with_name(package.name+'-baked-'+uuid.uuid4().hex[:12])
    def link_or_copy(src,dst):
        try:os.link(src,dst)
        except OSError:shutil.copy2(src,dst)
    shutil.copytree(package,replacement,copy_function=link_or_copy)
    for name,files in staged:
        level=replacement/'Data/Levels'/name
        for old in level.glob('svo-part*.pak'):old.unlink()
        for source in files.iterdir():
            target=level/source.name
            target.unlink(missing_ok=True);shutil.copy2(source,target)
        exposure=restore_baked_exposure(level/'level.pak',Path(game)/'Data/Levels/trosecko/level.pak')
        next(r for r in reports if r['level']==name)['exposure']=exposure
    receipt['baking']=dict(preset=preset,experimental=True,runtime_verified=False,levels=reports)
    receipt['files']={p.relative_to(replacement).as_posix():digest(p) for p in sorted(replacement.rglob('*')) if p.is_file() and p.name!=RECEIPT}
    (replacement/RECEIPT).unlink()
    (replacement/RECEIPT).write_text(json.dumps(receipt,indent=2))
    verify_package(replacement)
    if cancelled():raise InterruptedError('Cancelled before applying complete bake; outputs retained')
    previous=package.with_name(package.name+'-before-bake')
    if previous.exists():previous.rename(package.with_name(package.name+'-before-bake-'+uuid.uuid4().hex[:12]))
    package.rename(previous)
    try:replacement.rename(package)
    except BaseException:
        previous.rename(package)
        raise
    if previous.resolve().parent != package.resolve().parent or previous.is_symlink():
        raise ValueError('Unsafe completed bake staging directory')
    shutil.rmtree(previous)
