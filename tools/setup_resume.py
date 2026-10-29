"""Resume saved jobs at verified package boundaries; retain every attempt's logs."""
import json
import os
import shutil
import uuid
from pathlib import Path
from contextlib import contextmanager


def load_job(folder):
    from setup_mods import checked_config
    root=Path(folder).resolve()
    raw=json.loads((root/'job.json').read_text(encoding='utf-8'))
    if raw.get('resume_from'):
        root=Path(raw['resume_from']).resolve()
        raw=json.loads((root/'job.json').read_text(encoding='utf-8'))
    config=checked_config(raw)
    config['resume_from']=str(root)
    return config


@contextmanager
def job_lock(root):
    """OS lock is released even after a crash; a leftover file is harmless."""
    path=Path(root)/'setup.lock'
    with path.open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as e:
            raise ValueError('This setup job is already running. Wait for it to stop before resuming.') from e
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def restore_swap(package):
    """Recover an interrupted atomic bake-directory swap before verification."""
    package=Path(package)
    previous=package.with_name(package.name+'-before-bake')
    if not package.exists() and previous.is_dir():previous.rename(package)
    previous=package.with_name(package.name+'-before-update')
    if not package.exists() and previous.is_dir():previous.rename(package)


def update_private_package(package,operation,cancelled):
    """Optional stages cannot invalidate the last verified package on failure."""
    from setup_campaign import verify_package
    package=Path(package)
    replacement=package.with_name(package.name+'-update-'+uuid.uuid4().hex[:12])
    def link_or_copy(src,dst):
        try:os.link(src,dst)
        except OSError:shutil.copy2(src,dst)
    shutil.copytree(package,replacement,copy_function=link_or_copy)
    try:
        operation(replacement)
        verify_package(replacement)
        if cancelled():raise InterruptedError('Cancelled before applying optional package update')
    except BaseException:
        # The original remains verified. Discard only this attempt's private
        # staging directory instead of accumulating failed package copies.
        if replacement.resolve().parent != package.resolve().parent or replacement.is_symlink():
            raise ValueError('Unsafe failed update staging directory')
        shutil.rmtree(replacement)
        raise
    previous=package.with_name(package.name+'-before-update')
    if previous.exists():previous.rename(previous.with_name(previous.name+'-'+uuid.uuid4().hex[:12]))
    package.rename(previous)
    try:replacement.rename(package)
    except BaseException:
        previous.rename(package)
        raise
    if previous.resolve().parent != package.resolve().parent or previous.is_symlink():
        raise ValueError('Unsafe completed update staging directory')
    shutil.rmtree(previous)


def map_receipt(root, hashes=True):
    from setup_campaign import verify_package, RECEIPT
    package=Path(root)/'package/map'
    restore_swap(package)
    if not (package/RECEIPT).exists():return None
    receipt=verify_package(package,hashes=hashes)
    if not receipt.get('travel_bridge') or receipt.get('build_recipe',{}).get('status')!='complete':
        raise ValueError('Saved map package is incomplete. Its build recipe must finish before it can be reused.')
    from build_recipe import require_current
    try:
        require_current(receipt)
    except ValueError:
        from setup_package_upgrade import eligible
        eligible(receipt)
    return receipt


def preflight(config):
    import desktop_setup as legacy
    from setup_mods import preflight as fresh_preflight
    root=Path(config['resume_from'])
    receipt=map_receipt(root) if 'map' in config['mods'] and config['action']!='install' else None
    if receipt is None:return fresh_preflight(config)
    target=Path(config['kcd2']).resolve();workspace=Path(config['workspace']).resolve()
    if workspace.is_relative_to(target) or target.is_relative_to(workspace):
        raise ValueError('Choose a build folder outside the game.')
    if legacy.validate_games(Path(config['kcd1']),target)!='1.5.6':raise ValueError('KCD2 1.5.6 is required.')
    from setup_shaders import tool_root
    tool_root(target,config.get('mod_tools'))
    from setup_resume_space import lighting, installation
    import setup_progress
    lighting_plan=lighting(config,receipt,notify=lambda message:
        setup_progress.progress('Check resume lighting cache',0,0,message))
    messages=['Verified completed KCD1 Map. Continuing the same job; completed map conversion is retained.',
              *lighting_plan['messages']]
    extra=legacy.GIB+lighting_plan['bytes']
    unfinished=lighting_plan['pending']
    if config['baking']!='none' and receipt.get('baking',{}).get('preset')!=config['baking']:
        import numba
    if config['cutscenes']=='opening' and not receipt.get('opening_movie'):
        from setup_cutscenes import check_tools
        check_tools(config['converter']);extra+=10*legacy.GIB;unfinished=True
    stage=0
    if config['action']!='build_only':
        if legacy.game_running():raise ValueError('Close Kingdom Come before installing.')
        stage=installation(root/'package/map',target,receipt,lighting_plan)
        # An install-space shortage must not prevent finishing retained build
        # work. The installer checks actual output bytes again before staging.
        required=stage+(extra if workspace.anchor.lower()==target.anchor.lower() else 0)
        available=legacy.free_space(target)
        if available<required:
            detail=(f'Installation estimate: {required/legacy.GIB:.1f} GiB additional space '
                    f'including remaining work on that drive; {available/legacy.GIB:.1f} GiB free. '
                    'Existing build files are retained. Space is checked again before installation.')
            if not unfinished:raise ValueError(detail)
            messages.append('Build work can resume. '+detail)
    if legacy.free_space(workspace)<extra:
        raise ValueError(f'Resuming this job needs {extra/legacy.GIB:.1f} GiB additional build space; '
                         f'{legacy.free_space(workspace)/legacy.GIB:.1f} GiB free. Completed work is retained.')
    messages.append(f'Remaining build space allowance: {extra/legacy.GIB:.1f} GiB; completed files are already on disk.')
    if 'fishing' in config['mods']:
        fish=dict(config,mods=['fishing']);fresh_preflight(fish)
    if 'mp' in config['mods']:
        from setup_multiplayer import preflight as mp_preflight
        mp=dict(config,mods=['mp'])
        if (root/'package/mp/profile.json').is_file():
            mp.update(action='install',package=str(root/'package'))
        elif not mp.get('mp_source'):mp['mp_source']=str(root/'package/map')
        mp_preflight(mp)
    return dict(ok=True,config=config,messages=messages)
