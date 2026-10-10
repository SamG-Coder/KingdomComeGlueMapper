"""Build the Windows setup EXE without bundling game content."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from desktop_setup import APP_VERSION, CONVERTERS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('outputs/release'))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    name = f'KingdomComeGlueMapper-Setup-{APP_VERSION}-win64'
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
               '--name', name, '--distpath', str(output), '--workpath', str(repo / 'outputs/pyinstaller-build'),
               '--specpath', str(repo / 'outputs'), '--paths', str(repo / 'tools'),
               '--add-data', f'{repo / "runtime"}:runtime', '--add-data', f'{repo / "LICENSE"}:.',
               '--add-data', f'{repo / "tools/region_travel_horse_recovery.lua"}:.',
               '--exclude-module', 'lupa']
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
