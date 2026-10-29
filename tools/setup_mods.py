"""Selected-mod jobs and receipt-verified installation for the web setup."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import traceback
import uuid
from contextlib import redirect_stdout,redirect_stderr

import desktop_setup as legacy
from setup_campaign import digest, game_version
from setup_fishing import MOD_ID, RECEIPT, build as build_fishing
from setup_baking import PRESETS,bake_package
from setup_cutscenes import build as build_opening, check_tools

CATALOG=[
    dict(id='map',name='KCD1 Map',requirements='KCD1 + KCD2 + KCD2 Modding Tools',version='0.2.0-alpha.4',modid='kingdomcomegluemapper'),
    dict(id='mp',name='KCD1:MP',requirements='Completed KCD1 Map + KCD:MP 0.44.0',version='0.1.0',modid='gluemapperkcdmp'),
    dict(id='fishing',name='KCD2 Fishing',requirements='KCD2 only',version='0.3.0',modid=MOD_ID)]


def checked_config(raw):
    mods=raw.get('mods',[])
    if not isinstance(mods,list) or not mods or len(set(mods))!=len(mods) or any(m not in ('map','fishing','mp') for m in mods):
        raise ValueError('Select KCD1 Map, KCD1:MP or KCD2 Fishing.')
    if 'mp' in mods and 'fishing' in mods:
        raise ValueError('Build KCD1:MP separately from Fishing; its multiplayer profile enables only the map.')
    if raw.get('action') not in ('build_install','build_only','install'):raise ValueError('Choose a setup operation.')
    for key in ('kcd2','workspace'):
        if not isinstance(raw.get(key),str) or not raw[key].strip():raise ValueError('Choose '+key+' folder.')
    if 'map' in mods and not str(raw.get('kcd1','')).strip():raise ValueError('KCD1 Map also requires a KCD1 installation.')
    config={k:raw.get(k,'') for k in ('kcd1','kcd2','workspace','package','action')};config['mods']=mods
    config['mod_tools']=raw.get('mod_tools','')
    if not isinstance(config['mod_tools'],str):raise ValueError('Invalid Modding Tools folder.')
    config['baking']=raw.get('baking','none')
    config['rebuild_cache']=raw.get('rebuild_cache',False)
    config['imported_cache']=raw.get('imported_cache','')
    if (not isinstance(config['imported_cache'],str) or
            (config['imported_cache'] and (len(config['imported_cache'])!=64 or
             any(c not in '0123456789abcdef' for c in config['imported_cache'])))):
        raise ValueError('Invalid imported cache selection.')
    if raw.get('rebuild_cache') or 'map' not in mods or config['action']=='install':config['imported_cache']=''
    if not isinstance(config['rebuild_cache'],bool):raise ValueError('Invalid rebuild cache option.')
    if 'map' not in mods or config['action']=='install':config['rebuild_cache']=False
    if config['baking'] not in PRESETS:raise ValueError('Choose No Baking, Buildings or All (Forest).')
    if 'map' not in mods or config['action']=='install':config['baking']='none'
    config['cutscenes']=raw.get('cutscenes','none')
    if config['cutscenes'] not in ('none','opening'):raise ValueError('Choose whether to install the KCD1 opening movie.')
    config['converter']=raw.get('converter','')
    if not isinstance(config['converter'],str):raise ValueError('Invalid converter path.')
    if 'map' not in mods or config['action']=='install':config['cutscenes']='none'
    if 'mp' in mods:
        from setup_multiplayer import settings
        config.update(settings(raw))
    return config


def verify_fishing(package):
    package=Path(package).absolute()
    if package.is_symlink() or package.resolve()!=package:raise ValueError('Redirected fishing package')
    r=json.loads((package/RECEIPT).read_text())
    if r.get('owner')!=MOD_ID or r.get('schema')!=1:raise ValueError('Unrecognized fishing package')
    for n,h in r['files'].items():
        p=package/n
        if not n or ':' in n or '\\' in n or Path(n).is_absolute() or '..' in Path(n).parts or p.resolve()!=p or not p.is_file():
            raise ValueError('Unsafe package path: '+n)
        if digest(p)!=h:raise ValueError('Modified fishing package: '+n)
    actual={p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
    if actual!=set(r['files'])|{RECEIPT}:raise ValueError('Unexpected files in fishing package')
    return r


def preflight(raw):
    c=checked_config(raw);target=Path(c['kcd2']).resolve();workspace=Path(c['workspace']).resolve()
    if workspace.is_relative_to(target) or target.is_relative_to(workspace):raise ValueError('Choose a build folder outside the game.')
    messages=[]
    if c['action']!='build_only' and (target/'.gluemapper-kcdmp/active.json').exists():
        raise ValueError('Restore single-player from KCD1:MP before changing installed mods.')
    if 'mp' in c['mods']:
        from setup_multiplayer import preflight as mp_preflight
        messages.append(mp_preflight(c))
    if 'map' in c['mods']:
        mc=dict(c)
        if c['action']=='install':mc['package']=str(Path(c['package'])/'map')
        messages.append(legacy.preflight(mc))
        if c['cutscenes']=='opening':
            check_tools(c['converter'])
            required=(legacy.BUILD_WORKSPACE_GIB+10+(legacy.INSTALL_STAGING_GIB if workspace.anchor.lower()==target.anchor.lower() and c['action']=='build_install' else 0))*legacy.GIB
            if legacy.free_space(workspace)<required:raise ValueError('Opening conversion needs an additional 10 GiB of build space.')
            messages.append('KCD1 opening movie: RAD and FFmpeg found. Converts and installs English video/subtitles; replaces the shared New Game intro for both games.')
        if c['baking']!='none':
            import numba
            extra=30 if c['baking']=='buildings' else 60
            required=(legacy.BUILD_WORKSPACE_GIB+extra+(10 if c['cutscenes']=='opening' else 0)+(legacy.INSTALL_STAGING_GIB if workspace.anchor.lower()==target.anchor.lower() and c['action']=='build_install' else 0))*legacy.GIB
            if legacy.free_space(workspace)<required:raise ValueError(f'{PRESETS[c["baking"]]} requires {required//legacy.GIB} GiB free for conversion, bake working files and staging. Actual use varies.')
            messages.append(PRESETS[c['baking']]+': experimental CPU lighting bake. Additional space allowance '+str(extra)+' GiB; full-world duration is not yet benchmarked.')
    if 'fishing' in c['mods']:
        if game_version(target)!='1.5.6':raise ValueError('KCD2 Fishing currently supports KCD2 1.5.6.')
        for n in ('Animations.pak','Tables.pak','Scripts.pak','IPL_Objects-part3.pak','IPL_Objects-part5.pak','Levels/trosecko/terrain.pak','Levels/kutnohorsko/terrain.pak'):
            if not (target/'Data'/n).is_file():raise ValueError('KCD2 file missing: '+n)
        if legacy.free_space(workspace)<legacy.GIB:raise ValueError('Fishing setup needs 1 GiB free in the build folder.')
        if c['action']!='build_only':
            if legacy.game_running():raise ValueError('Close Kingdom Come before installing.')
            if legacy.free_space(target)<legacy.GIB:raise ValueError('KCD2 drive needs 1 GiB free for staging.')
            dest=target/'Mods'/MOD_ID
            if dest.exists():verify_fishing(dest)
            mapmod=target/'Mods/kingdomcomegluemapper/Data/gluemappertravel.pak'
            if mapmod.exists():
                import zipfile
                with zipfile.ZipFile(mapmod) as z:
                    if 'Scripts/GMFishing/runtime.lua' in z.namelist():
                        raise ValueError('This map contains the older embedded fishing test. Preserve it and install a clean map package before adding standalone fishing.')
        if c['action']=='install':verify_fishing(Path(c['package'])/'fishing')
        messages.append('KCD2 Fishing: standalone package for Trosky and Kuttenberg. Rods available from general merchants after stock refresh.')
    return dict(ok=True,messages=messages,config=c)


def install_fishing(package,game,notify,cancelled):
    package,game=Path(package).resolve(),Path(game).resolve();receipt=verify_fishing(package)
    mods=game/'Mods';dest=mods/MOD_ID;order=mods/'mod_order.txt'
    for p in (mods,dest,order,game/'GlueMapper Backups'):
        if p.is_symlink() or p.resolve()!=p:raise ValueError('Redirected installation path')
    if legacy.game_running():raise ValueError('Close the game before installing.')
    if dest.exists():verify_fishing(dest)
    mods.mkdir(exist_ok=True)
    old=order.read_bytes() if order.exists() else None
    lines=old.decode('utf-8-sig').splitlines() if old is not None else [p.name for p in sorted(mods.iterdir()) if p.is_dir() and (p/'mod.manifest').exists()]
    lines=[n for n in lines if n.strip().lower()!=MOD_ID];lines.append(MOD_ID)
    backup=game/'GlueMapper Backups'/('fishing-'+uuid.uuid4().hex[:12]);backup.mkdir(parents=True)
    if old is not None:(backup/'mod_order.txt').write_bytes(old)
    moved=False;placed=False
    with tempfile.TemporaryDirectory(prefix='.fishing-',dir=game) as temp:
        stage=Path(temp)/MOD_ID
        for i,n in enumerate([*receipt['files'],RECEIPT]):
            if cancelled():raise legacy.Cancelled('Cancelled before replacement')
            p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(package/n,p)
            notify('Stage fishing files',i+1,len(receipt['files'])+1,n)
        verify_fishing(stage)
        if legacy.game_running() or cancelled():raise legacy.Cancelled('Installation stopped before replacement')
        try:
            if dest.exists():dest.rename(backup/MOD_ID);moved=True
            stage.rename(dest);placed=True
            temporder=Path(temp)/'mod_order.txt';temporder.write_text('\n'.join(lines)+'\n',encoding='utf-8');temporder.replace(order)
        except BaseException:
            if placed:dest.rename(stage)
            if moved:(backup/MOD_ID).rename(dest)
            if old is None:order.unlink(missing_ok=True)
            else:order.write_bytes(old)
            raise
    return dict(installed=str(dest),backup=str(backup))


def worker(path):
    from setup_resume import job_lock
    path=Path(path).resolve()
    raw=json.loads(path.read_text())
    root=Path(raw.get("resume_from",path.parent)).resolve()
    try:
        from setup_build_cache import environment
        with job_lock(root), environment(raw):return _worker(path)
    except (OSError,ValueError) as e:
        events=legacy.EventLog(path.parent/"events.jsonl")
        events.emit("error",message=str(e));events.stream.close();events.text.close()
        return 1


def _worker(path):
    path=Path(path).resolve();job=path.parent;c=json.loads(path.read_text());events=legacy.EventLog(job/'events.jsonl')
    import setup_progress
    previous_sink=setup_progress.sink;setup_progress.sink=events.progress
    previous_work_sink=setup_progress.work_sink;setup_progress.work_sink=events.work_progress
    cancel=lambda:(job/'cancel.requested').exists()
    try:
        resumed=bool(c.get('resume_from'))
        if resumed:
            from setup_resume import load_job, preflight as resume_preflight, map_receipt
            c=load_job(c['resume_from']);root=Path(c['resume_from'])
            events.emit('stage',index=0,total=1,label='Verify saved work')
            check=resume_preflight(c)
            for message in (check or {}).get('messages',[]):events.emit('log',message=message)
            saved=map_receipt(root,hashes=False) if 'map' in c['mods'] and c['action']!='install' else None
        else:
            c=checked_config(c);root=job;saved=None
            preflight(c)
        if cancel():raise legacy.Cancelled('Cancelled before setup began.')
        package=Path(c['package']).resolve() if c['action']=='install' else root/'package'
        package.mkdir(parents=True,exist_ok=True)
        if 'map' in c['mods']:
            mapjob=job/'map-job';mapjob.mkdir(exist_ok=True);mc=dict(c)
            mc['action']='install' if c['action']=='install' else 'build_only'
            if c['action']=='install':mc['package']=str(package/'map')
            cp=mapjob/'job.json';cp.write_text(json.dumps(mc))
            events.emit('stage',index=0,total=len(c['mods']),label='KCD1 Map')
            if saved:
                events.emit('log',message='Reusing verified KCD1 Map. Saved bake checkpoints are reused before checking gameplay upgrades.')
            else:
                code=legacy.worker(cp)
                setup_progress.sink=events.progress
                setup_progress.work_sink=events.work_progress
                if code==2:raise legacy.Cancelled('KCD1 Map cancelled. Build output retained.')
                if code:
                    detail = 'See map-job/setup.log.'
                    child_events = mapjob/'events.jsonl'
                    if child_events.is_file():
                        for line in child_events.read_text(encoding='utf-8').splitlines():
                            try: event = json.loads(line)
                            except ValueError: continue
                            if event.get('kind') == 'error': detail = event.get('message') or detail
                    raise ValueError('KCD1 Map did not complete: '+detail)
            if c['action']!='install':
                if not saved:
                    if (package/'map').exists():(package/'map').rename(job/'incomplete-map')
                    (mapjob/'package').rename(package/'map')
                if c['cutscenes']=='opening' and not (saved or {}).get('opening_movie'):
                    events.emit('stage',index=0,total=len(c['mods']),label='Convert and package KCD1 opening movie')
                    from setup_resume import update_private_package
                    update_private_package(package/'map',lambda staged:build_opening(staged,c['kcd1'],c['kcd2'],c['converter'],job/'opening-movie',events.progress,cancel),cancel)
                if c.get('baking','none')!='none' and (saved or {}).get('baking',{}).get('preset')!=c['baking']:
                    events.emit('stage',index=0,total=len(c['mods']),label='KCD1 Map · '+PRESETS[c['baking']]+' lighting bake')
                    output=legacy.LogWriter(events)
                    with redirect_stdout(output),redirect_stderr(output):
                        bake_package(package/'map',c['kcd2'],root/'baking',c['baking'],events.progress,cancel)
                        output.flush()
                if saved and saved.get('travel_bridge'):
                    from setup_resume import update_private_package
                    from setup_package_upgrade import finalize
                    from setup_section_repair import stale_sections, repair
                    from build_recipe import VERSION
                    if stale_sections(package/'map'):
                        events.emit('stage',index=0,total=len(c['mods']),label='Repair stale map section')
                        update_private_package(package/'map',lambda staged:repair(staged,
                            repair_roots=(c['workspace'],), notify=events.progress, cancelled=cancel),cancel)
                    if not saved.get('single_playable_world') or saved.get('build_recipe',{}).get('version') != VERSION:
                        events.emit('stage',index=0,total=len(c['mods']),label='Upgrade saved gameplay and keep one playable world')
                        update_private_package(package/'map',lambda staged:finalize(staged,
                            source=c['kcd1'], target=c['kcd2'], repair_roots=(c['workspace'],),
                            notify=events.progress, cancelled=cancel),cancel)
                if c['action']=='build_install':
                    events.emit('stage',index=0,total=len(c['mods']),label='Install and verify KCD1 Map')
                    legacy.install_transaction(package/'map',c['kcd2'],events.progress,cancel)
        if cancel():raise legacy.Cancelled('Cancelled; completed mod operations are retained.')
        if 'fishing' in c['mods']:
            events.emit('stage',index=c['mods'].index('fishing'),total=len(c['mods']),label='KCD2 Fishing')
            if c['action']!='install':
                if resumed and (package/'fishing'/RECEIPT).is_file():
                    verify_fishing(package/'fishing');events.emit('log',message='Reusing verified KCD2 Fishing package.')
                else:
                    fishwork=job/'fishing-build'
                    build_fishing(c['kcd2'],fishwork,package/'map' if 'map' in c['mods'] else None,events.progress,cancel)
                    if (package/'fishing').exists():(package/'fishing').rename(job/'incomplete-fishing')
                    fishwork.rename(package/'fishing')
            if c['action']!='build_only':install_fishing(package/'fishing',c['kcd2'],events.progress,cancel)
        mp_profile=None
        if 'mp' in c['mods']:
            from setup_multiplayer import run
            events.emit('stage',index=c['mods'].index('mp'),total=len(c['mods']),label='KCD1:MP')
            mp_profile=run(c,package,lambda message:events.emit('log',message=message),cancel)
        message='Selected packages are ready.' if c['action']=='build_only' else 'Selected mods installed.'
        if 'map' in c['mods'] and c['action']!='build_only':message+=' Use Launch mod in the Library to play with its shaders.'
        if mp_profile:message+=' Manage KCD1:MP from its Library settings. Runtime playtest still required.'
        events.emit('done',message=message,package=str(package),mp_profile=mp_profile);return 0
    except (legacy.Cancelled,InterruptedError) as e:events.emit('cancelled',message=str(e));return 2
    except BaseException as e:events.emit('error',message=str(e),traceback=traceback.format_exc());return 1
    finally:
        setup_progress.sink=previous_sink
        setup_progress.work_sink=previous_work_sink
        events.stream.close();events.text.close()
