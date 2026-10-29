"""Estimate additional bytes for retained jobs, without reserving a new build."""
import json
from pathlib import Path

GIB = 1024**3


def lighting(config, receipt, notify=lambda message: None):
    """A verified cache needs copies of its output, not another full bake budget.

    A saved scene is accepted only in the baker's current input-signature folder.
    Missing/incompatible scenes keep the normal conservative bake allowance.
    """
    preset = config['baking']
    result = dict(bytes=0, install_bytes=0, replaced=set(), pending=False, messages=[])
    if preset == 'none' or receipt.get('baking',{}).get('preset') == preset:
        return result
    result.update(bytes=(30 if preset=='buildings' else 60)*GIB, pending=True)
    if config.get('rebuild_cache'):
        result['messages'].append('Lighting regeneration requested; retaining the full bake space allowance.')
        return result
    from setup_baking import checkpoint_workspace
    from setup_bake_cache import matching_workspace_cache
    from setup_build_cache import Cache
    from setup_gpu import hybrid_environment
    root = Path(config['resume_from'])
    package = root/'package/map'
    cache_folder = Path(config['workspace'])/'cache/lighting'
    if not cache_folder.is_dir(): return result
    work = checkpoint_workspace(config['kcd2'],root/'baking',preset,receipt)
    levels = sorted((package/'Data/Levels').iterdir())
    destination = receipt.get('travel_bridge',{}).get('destination')
    if destination:
        if Path(destination).name != destination: raise ValueError('Invalid playable bake destination')
        levels = [package/'Data/Levels'/destination]
    output_bytes = 0; largest_rewrite = 0; replaced = set(); tiles = 0
    for level in levels:
        if not (level/'terrain.pak').is_file(): continue
        ready = work/level.name/'ready.json'
        if not ready.is_file(): return result
        try:
            attempt = json.loads(ready.read_text())['attempt']
            if not isinstance(attempt,str) or Path(attempt).name != attempt or attempt in ('.','..'):
                return result
            scene = ready.parent/attempt/'scene'
            with hybrid_environment():
                hit = matching_workspace_cache(Cache(cache_folder),scene,preset,notify=notify)
        except (OSError,ValueError,KeyError,TypeError):
            return result
        if hit is None: return result
        payload, meta = hit
        shards = sum(p.stat().st_size for p in payload.iterdir() if p.is_file())
        mutable = sum((level/name).stat().st_size for name in ('level.pak','terrain.pak'))
        output_bytes += shards + mutable
        largest_rewrite = max(largest_rewrite,(level/'level.pak').stat().st_size)
        tiles += meta['tiles']
        prefix = level.relative_to(package).as_posix()+'/'
        replaced.update(n for n in receipt['files'] if n.startswith(prefix) and
                        (n.endswith(('/level.pak','/terrain.pak')) or Path(n).name.startswith('svo-part')))
    if not tiles: return result
    # restore() writes one private output; applying it makes one more copy.
    # Exposure rewrite temporarily needs another level.pak. Existing package
    # files are retained through the baker's hardlink-and-replace transaction.
    result.update(bytes=2*output_bytes+largest_rewrite,install_bytes=output_bytes,replaced=replaced)
    result['messages'].append(f'Found {tiles} reusable lighting tiles in the workspace cache. '
                              f'Allowing {result["bytes"]/GIB:.1f} GiB for applying cached output; no full rebake allowance.')
    return result


def installation(package, target, receipt, lighting_plan):
    """Match the installer's reuse estimate; installation rechecks all hashes."""
    from setup_campaign import MOD_ID, RECEIPT, verify_package
    package, target = Path(package), Path(target)
    destination = target/'Mods'/MOD_ID
    previous = verify_package(destination,hashes=False) if destination.exists() else {'files':{}}
    changed = sum((package/n).stat().st_size for n,h in receipt['files'].items()
                  if n not in lighting_plan['replaced'] and previous['files'].get(n) != h)
    return changed + lighting_plan['install_bytes'] + (package/RECEIPT).stat().st_size + GIB
