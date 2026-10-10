"""Install the validated CurrentBuild and record exactly what retail will load."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

from current_build import paths, file_hash, remove_generated, working_environment, write_json


def require_game_closed():
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-Command',
         "Get-Process | Where-Object { $_.ProcessName -match '^(KingdomCome|Sandbox)' } | Select-Object -ExpandProperty ProcessName"],
        capture_output=True, text=True, check=True)
    if result.stdout.strip():
        raise RuntimeError('Close the game/editor before installing: ' + result.stdout.strip())


def safe_path(root, relative):
    root = Path(root).resolve()
    candidate = root / relative
    if not candidate.resolve().is_relative_to(root) or candidate.resolve() == root:
        raise ValueError('Install path escapes its root: ' + relative)
    for part in (candidate, *candidate.parents):
        if part == root: break
        if part.is_symlink() or part.is_junction():
            raise ValueError('Install refuses linked paths: ' + str(part))
    return candidate


def install(layout, target, check_closed=require_game_closed):
    check_closed()
    target = Path(target).resolve()
    current = layout['current']
    receipt = json.loads((current / 'build.json').read_text())
    if receipt.get('generated') is not True or not receipt.get('validation', {}).get('native_tables_verified'):
        raise ValueError('CurrentBuild must pass native table validation before installation')
    files = receipt['files']
    changes = []
    obsolete = []
    before = {}
    previous_receipt = layout['logs'] / 'current-build-installation.json'
    if previous_receipt.exists():
        previous_install = json.loads(previous_receipt.read_text())
        if Path(previous_install['target']).resolve() == target:
            for relative, digest in previous_install['files'].items():
                if relative in files: continue
                parts = Path(relative).parts
                if len(parts) < 3 or parts[0] != 'Mods' or parts[1] not in receipt['mods']:
                    continue
                destination = safe_path(target, relative)
                if not destination.exists(): continue
                if file_hash(destination) != digest:
                    raise ValueError('Obsolete managed file was modified; refusing to remove it: ' + relative)
                before[relative] = digest
                obsolete.append(relative)
    for relative, digest in files.items():
        parts = Path(relative).parts
        if len(parts) < 3 or parts[0] != 'Mods' or parts[1] not in receipt['mods']:
            raise ValueError('Unexpected managed mod file: ' + relative)
        source, destination = safe_path(current, relative), safe_path(target, relative)
        if file_hash(source) != digest:
            raise ValueError('CurrentBuild changed since validation: ' + relative)
        before[relative] = file_hash(destination) if destination.exists() else None
        if before[relative] != digest: changes.append(relative)
    work = layout['work'] / 'install'
    if work.exists():
        raise FileExistsError('Inspect the previous interrupted installation first: ' + str(work))
    work.mkdir(parents=True)
    committed = []
    try:
        for relative in changes:
            staged = safe_path(work / 'new', relative)
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(safe_path(current, relative), staged)
            if file_hash(staged) != files[relative]:
                raise ValueError('Staged copy verification failed: ' + relative)
        check_closed()
        for relative in obsolete + changes:
            destination = safe_path(target, relative)
            actual = file_hash(destination) if destination.exists() else None
            if actual != before[relative]:
                raise ValueError('Installed file changed during staging: ' + relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            previous = safe_path(work / 'previous', relative)
            if destination.exists():
                previous.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(previous)
            committed.append(relative)
            if relative not in obsolete:
                safe_path(work / 'new', relative).replace(destination)
        for relative, digest in files.items():
            if file_hash(safe_path(target, relative)) != digest:
                raise ValueError('Installed copy verification failed: ' + relative)
        result = dict(installed_at=datetime.now(timezone.utc).isoformat(), target=str(target),
                      build_checked_at=receipt['checked_at'], changed_files=changes,
                      removed_obsolete_files=obsolete,
                      verified_files=len(files), files=files, runtime_verified=False)
        write_json(layout['logs'] / 'current-build-installation.json', result)
    except BaseException:
        for relative in reversed(committed):
            destination = safe_path(target, relative)
            previous = safe_path(work / 'previous', relative)
            if previous.exists(): previous.replace(destination)
            elif before[relative] is None and destination.exists(): destination.unlink()
        raise
    finally:
        # A failed rollback leaves this directory for recovery instead of deleting it.
        import sys
        if sys.exc_info()[0] is None:
            remove_generated(work, layout['work'])
    receipt['installed'] = True
    receipt['installation_receipt'] = str(layout['logs'] / 'current-build-installation.json')
    write_json(current / 'build.json', receipt)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outputs', type=Path, default=Path(__file__).resolve().parents[1] / 'outputs')
    args = parser.parse_args()
    layout = paths(args.outputs)
    config = json.loads((layout['cache'] / 'build-inputs.json').read_text())
    with working_environment(layout):
        result = install(layout, config['target'])
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}, indent=2))


if __name__ == '__main__': main()
