"""Publish the current asset set with temporary rendering diagnostics enabled."""
import json
from pathlib import Path
import shutil
import zipfile
from current_build import paths, working_environment, publish, write_json
from travel_world_package import prepare_world_for_travel
from retail_pak import PakSet
from region_travel_ai_trace import resources


def refresh_ai_trace(travel, target):
    """Replace only existing diagnostic resources; preserve the player hooks."""
    with zipfile.ZipFile(Path(target) / 'Data/Scripts.pak') as native:
        replacements, lua = resources(native, 'kcd1_travel')
    with PakSet(Path(travel) / 'Data') as source:
        by_lower = {n.lower(): n for n in source.namelist()}
        player_name = by_lower['scripts/entities/actor/player.lua']
        player = source.read(player_name)
        marker = b'local glueTraceCounts,'
        ending = b"System.LogAlways('GLUE_AI_TRACE installed; bounded native command/reaction tracing')"
        if player.count(marker) != 1 or not player.rstrip().endswith(ending):
            raise ValueError('Existing player trace tail changed; refusing to replace gameplay hooks')
        replacements[player_name] = player[:player.index(marker)] + lua
        grouped = {}
        for name, payload in replacements.items():
            actual = by_lower[name.lower()]
            grouped.setdefault(Path(source.entries[actual].filename), {})[actual] = payload
    for path, changes in grouped.items():
        temporary = path.with_suffix('.diagnostic.tmp')
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED,
                allowZip64=False, compresslevel=1) as dst:
            for name in src.namelist():
                dst.writestr(name, changes[name] if name in changes else src.read(name))
        temporary.replace(path)


def build(layout, config):
    if layout['next'].exists(): raise FileExistsError('Inspect pending build before diagnosing rendering')
    shutil.copytree(layout['current'], layout['next'])
    world = layout['next'] / 'Mods/kingdomcomegluemapper'
    prepare_world_for_travel(world, render_diagnostics=True)
    refresh_ai_trace(layout['next'] / 'Mods/gluemappertravel', config['target'])
    write_json(world / 'render-diagnostics.json', dict(enabled=True, maximum_seconds=600,
        interval_seconds=30, rendering_changed=False, runtime_verified=False))
    return publish(layout, config)


if __name__ == '__main__':
    layout = paths(Path(__file__).resolve().parents[1] / 'outputs')
    config = json.loads((layout['cache'] / 'build-inputs.json').read_text())
    with working_environment(layout): receipt = build(layout, config)
    print(json.dumps(receipt['validation'], indent=2))
