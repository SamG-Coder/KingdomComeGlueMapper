"""Install/remove a reversible render-stream test without changing source PAKs.

Explicit test only: it must not be promoted to setup defaults before the
reported cross-instance LOD transition is verified in game.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile
from native_mesh_streaming import validate_pair


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plan(stage, targets):
    stage = Path(stage).resolve()
    report = json.loads((stage/'staging.json').read_text())
    if report.get('schema') != 2:
        raise ValueError('Regenerate the test with current physics dependency validation')
    if report['errors'] or not report['converted']:
        raise ValueError('Incomplete conversion')
    for target in targets:
        target = Path(target).resolve()
        if not (target/'mod.manifest').is_file() or not (target/'Data').is_dir():
            raise ValueError('Not an installed mod/package: '+str(target))
        for entry in report['sources']:
            source = (target/'Data'/entry['name']).resolve()
            if source.parent != target/'Data' or not source.is_file() or digest(source) != entry['sha256']:
                raise ValueError('Test source package changed: '+str(source))
    operations = []
    for entry in report['archives']:
        source = Path(entry['path']).resolve()
        if source.parent != stage or digest(source) != entry['sha256']:
            raise ValueError('Staged archive provenance mismatch')
        with zipfile.ZipFile(source) as archive:
            if archive.testzip():
                raise ValueError('Invalid staged archive CRC')
            names = set(archive.namelist())
            if len(names) != entry['pairs'] * 2:
                raise ValueError('Unexpected staged archive members')
            for name in names:
                if name.endswith('.cgf'):
                    if name+'m' not in names:
                        raise ValueError('Missing stream payload: '+name)
                    validate_pair(archive.read(name), archive.read(name+'m'))
                elif not name.endswith('.cgfm') or name[:-1] not in names:
                    raise ValueError('Unexpected test member: '+name)
        for target in targets:
            target = Path(target).resolve()
            if not (target/'mod.manifest').is_file() or not (target/'Data').is_dir():
                raise ValueError('Not an installed mod/package: '+str(target))
            destination = target/'Data'/('zz_gm_'+source.name)
            if destination.exists() and not os.path.samefile(source, destination):
                raise ValueError('Conflicting test archive: '+str(destination))
            operations.append((source, destination))
    return operations


def install(stage, targets):
    operations = plan(stage, targets)  # all checks before any mutation
    created = []
    try:
        for source, destination in operations:
            if not destination.exists():
                os.link(source, destination)
                created.append(destination)
    except Exception:
        for destination in created:
            destination.unlink()
        raise
    receipt = {'status':'installed_for_test', 'runtime_verified':False,
               'links':[{'source':str(s),'destination':str(d)} for s,d in operations]}
    (Path(stage)/'installation.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


def rollback(stage):
    path = Path(stage)/'installation.json'
    receipt = json.loads(path.read_text())
    for entry in receipt['links']:
        source, destination = Path(entry['source']), Path(entry['destination'])
        if destination.exists() and not os.path.samefile(source, destination):
            raise ValueError('Test archive replaced externally: '+str(destination))
    for entry in receipt['links']:
        Path(entry['destination']).unlink(missing_ok=True)
    receipt['status'] = 'rolled_back'
    path.write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--target', type=Path, action='append', default=[])
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    processes = subprocess.check_output(['tasklist','/FO','CSV','/NH'], text=True)
    if any('"'+name+'"' in processes.lower() for name in ('kingdomcome.exe','editor.exe')):
        raise SystemExit('Close the game/editor before changing mounted archives')
    if not args.rollback and not args.target:
        parser.error('At least one --target is required')
    result = rollback(args.stage) if args.rollback else install(args.stage,args.target)
    print(json.dumps(result, indent=2))
