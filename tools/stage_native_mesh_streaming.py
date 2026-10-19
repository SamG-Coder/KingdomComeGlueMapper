"""Build a bounded streaming test overlay without modifying installed archives."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from native_mesh_streaming import split, resident_reason
from upgrade_map import read


def stage(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    receipt = destination/'installation.json'
    if receipt.is_file() and json.loads(receipt.read_text())['status'] == 'installed_for_test':
        raise ValueError('Roll back the mounted test before regenerating it')
    destination.mkdir(parents=True, exist_ok=True)
    report = {'schema':2, 'source':str(source), 'sources':[],
              'converted':0, 'resident':0, 'resident_assets':[], 'errors':[], 'archives':[],
              'runtime_verified':False, 'ready_to_install':False}
    for archive in sorted((source/'Data').glob('kingdomcomegluemapper_world_*.pak')):
        output = destination/('native_stream_'+archive.stem+'.pak')
        pending = output.with_suffix('.pending')
        written = 0
        with zipfile.ZipFile(archive) as src, zipfile.ZipFile(pending, 'w', zipfile.ZIP_STORED) as dst:
            for entry in src.infolist():
                name = entry.filename.replace('\\','/').lower()
                if not name.startswith(('glueveg/','gluebuild/','gluelandscape/')) or not name.endswith('.cgf'):
                    continue
                try:
                    original = read(src, entry.filename)
                    header, payload = split(original)
                except (ValueError, KeyError) as error:
                    report['errors'].append({'asset':name, 'reason':str(error)})
                    continue
                if payload is None:
                    report['resident'] += 1
                    report['resident_assets'].append({'asset':name,
                        'reason':resident_reason(original) or 'no_streamable_render_geometry'})
                    continue
                dst.writestr(name, header); dst.writestr(name+'m', payload)
                written += 1; report['converted'] += 1
        if written:
            with zipfile.ZipFile(pending) as check:
                if check.testzip(): raise ValueError('Staged archive CRC failure')
            pending.replace(output)
            with output.open('rb') as f: sha = hashlib.file_digest(f,'sha256').hexdigest()
            report['archives'].append({'path':str(output), 'sha256':sha, 'pairs':written})
            with archive.open('rb') as f:
                original_sha = hashlib.file_digest(f, 'sha256').hexdigest()
            report['sources'].append({'name':archive.name, 'sha256':original_sha})
        else:
            pending.unlink()
        print(archive.name, report['converted'], 'pairs;', len(report['errors']), 'errors', flush=True)
    (destination/'staging.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = stage(args.source,args.output)
    if result['errors']: raise SystemExit('Conversion incomplete; see staging.json')
