"""Build the Windows setup EXE without bundling game content."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from desktop_setup import APP_VERSION, CONVERTERS
from lighting_cubemaps import compiler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('outputs/release'))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    name = f'KingdomComeGlueMapper-Setup-{APP_VERSION}-win64'
    texconv = compiler(repo/'outputs/build-dependencies')
    from setup_gpu import GPU_FILES, validate_bundle
    gpu = repo / 'outputs/cuda'
    validate_bundle(gpu, repo / 'tools')
    optix_license = repo / 'outputs/optix-dev/LICENSE.txt'
    if not optix_license.is_file():raise RuntimeError('Missing OptiX license; build the GPU backend first')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
               '--name', name, '--distpath', str(output), '--workpath', str(repo / 'outputs/pyinstaller-build'),
               '--specpath', str(repo / 'outputs'), '--paths', str(repo / 'tools'),
               '--add-data', f'{repo / "runtime"}:runtime', '--add-data', f'{repo / "LICENSE"}:.',
               '--add-data', f'{repo / "setup-ui"}:setup-ui',
               '--add-data', f'{repo / "assets/fishing/gm_fishing_rod_icon.dds"}:assets/fishing',
               '--add-data', f'{repo / "tools/region_travel_horse_recovery.lua"}:.',
               '--add-binary', f'{texconv}:.',
               '--add-data', f'{repo / "third_party/DirectXTex-LICENSE.txt"}:third_party',
               '--add-data', f'{repo / "third_party/xatlas-LICENSE.txt"}:third_party',
               '--collect-all', 'xatlas',
               '--exclude-module', 'lupa','--exclude-module','playwright']
    for resource in ('fishing_game.lua','fishing_runtime.lua','fishing_visuals.lua','fishing_interaction.lua'):
        command.extend(['--add-data',f'{repo / "tools" / resource}:tools'])
    command.extend(['--add-data', f'{optix_license}:third_party/OptiX'])
    for resource in GPU_FILES:
        command.extend(['--add-binary' if resource.endswith('.dll') else '--add-data',
                        f'{gpu / resource}:bake-gpu'])
    # Raster signatures read their own sources, including GPU sources.
    # The setup's lighting cache folder is keyed by lighting_cubemaps.py.
    for resource in ('lighting_cubemaps.py','world_surface_scene.py','world_surface_landscape.py','stage_world_surfaces.py','world_surface_tiles.py','voxel_surfaces.py','voxel_raster_fast.py','voxel_raster_cuda.py','voxel_raster_cuda.cu','voxel_optix_shared.h','voxel_optix_device.cu','voxel_optix_host.h','geometry_opacity.py','world_surface_coverage.py'):
        command.extend(['--add-data',f'{repo / "tools" / resource}:.'])
    command.extend(['--hidden-import','voxel_raster_fast','--collect-all','numba','--collect-all','llvmlite'])
    for module in CONVERTERS: command.extend(['--hidden-import', module])
    command.append(str(repo / 'tools/setup_app.py'))
    subprocess.run(command, cwd=repo, check=True)
    exe = output / (name + '.exe')
    result = output / 'self-test.json'
    result.unlink(missing_ok=True)
    subprocess.run([str(exe), '--self-test', str(result)], check=True, timeout=90)
    if not result.is_file(): raise RuntimeError('Frozen self-test did not finish')
    report = json.loads(result.read_text(encoding='utf-8'))
    if report.get('status') != 'passed' or not report.get('frozen') or report.get('version') != APP_VERSION:
        raise RuntimeError('Frozen self-test returned an unexpected result')
    with exe.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {exe.name}\n', encoding='ascii')
    print(exe)
    print(result.read_text())


if __name__ == '__main__': main()
