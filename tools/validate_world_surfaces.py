"""Validate every planned tile; optionally adopt unchanged validated base tiles.

Reuse is explicit, for the category-only augmentation and the roundoff encoder
fix. It is not a general authorization to reuse outputs across raster changes.
All mesh/placement/package bytes and previous output hashes are checked.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from voxel_lighting import validate_pair
from world_surface_tiles import code_digest,gpu_configuration,tile_signature
from upgrade_map import digest


def stem(job):return '_'.join(f'{v:04d}' for v in [*(x+128 for x in job['origin']),256])


def validate(scene):
    scene=Path(scene);plan=json.loads((scene/'plan.json').read_text());rows=[];failures=[]
    for job in plan['tiles']:
        name=stem(job)
        try:
            path=scene/'tiles';report=json.loads((path/(name+'.json')).read_text())
            if report['status']!='built' or report['origin']!=job['origin']:raise ValueError('Missing successful tile report')
            if report.get('vegetation_instances',0)!=len(job.get('vegetation',[])):raise ValueError('Vegetation coverage mismatch')
            idx=(path/(name+'.idx')).read_bytes();vox=(path/(name+'.vox')).read_bytes()
            check=validate_pair(idx,vox);sha=hashlib.sha256(idx+vox).hexdigest()
            if sha!=report['sha256']:raise ValueError('Output hash mismatch')
            if check['blocks'] and check['channel_masks']!=[7]:raise ValueError('Missing surface channels')
            rows.append({'tile':name,'sha256':sha,'blocks':check['blocks']})
        except (ValueError,KeyError,OSError) as error:failures.append({'tile':name,'error':str(error)})
    result={'validated':len(rows),'expected':len(plan['tiles']),'failed':failures,'pending_categories':plan['pending_categories'],
            'runtime_verified':False,'tiles':rows}
    (scene/'validated-tiles.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def reuse_base(scene):
    scene=Path(scene);plan=json.loads((scene/'plan.json').read_text());base=Path(plan['reuse_base_scene'])
    old_plan=json.loads((base/'plan.json').read_text())
    for key in ('source_packages','asset_catalog_digest','overrides','pak_root'):
        if plan[key]!=old_plan[key]:raise ValueError('Base scene changed: '+key)
    for path,sha in plan['source_packages'].items():
        if digest(path)!=sha:raise ValueError('Installed source changed')
    if digest(scene/'placements.json')!=digest(base/'placements.json'):raise ValueError('Base placements changed')
    # Validate all cached mesh bytes, including material bindings and UVs.
    for path in (base/'meshes').iterdir():
        if digest(path)!=digest(scene/'meshes'/path.name):raise ValueError('Base mesh changed: '+path.name)
    ledger=json.loads((base/'validated-tiles.json').read_text())
    approved={r['tile']:r['sha256'] for r in ledger['tiles']}
    old_jobs={tuple(j['origin']):j for j in old_plan['tiles']}
    destination=scene/'tiles';destination.mkdir(exist_ok=True)
    version=code_digest();configuration=gpu_configuration();rows=[]
    for job in plan['tiles']:
        if job.get('vegetation') or old_jobs.get(tuple(job['origin']))!=job:continue
        name=stem(job);src=base/'tiles';report=json.loads((src/(name+'.json')).read_text())
        if report['status']!='built' or approved.get(name)!=report['sha256']:raise ValueError('Unvalidated base tile: '+name)
        idx=(src/(name+'.idx')).read_bytes();vox=(src/(name+'.vox')).read_bytes();validate_pair(idx,vox)
        if hashlib.sha256(idx+vox).hexdigest()!=report['sha256']:raise ValueError('Changed base tile: '+name)
        if (destination/(name+'.json')).exists():raise FileExistsError('Existing target tile: '+name)
        for suffix in ('.idx','.vox'):shutil.copy2(src/(name+suffix),destination/(name+suffix))
        report['reuse']={'source':str(src/(name+'.json')),'source_signature':report['signature'],
                        'basis':'unchanged scene inputs; explicit category-only augmentation and roundoff-only encoder compatibility'}
        report['signature']=tile_signature(version,plan['asset_catalog_digest'],plan,job,2,True,'auto',configuration)
        report['gpu_config']=configuration
        (destination/(name+'.json')).write_text(json.dumps(report,indent=2)+'\n')
        rows.append(name)
    result={'reused':len(rows),'rebuild':len(plan['tiles'])-len(rows),'tiles':rows}
    (scene/'reuse-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scene',type=Path,required=True);p.add_argument('--reuse-base',action='store_true')
    a=p.parse_args();r=reuse_base(a.scene) if a.reuse_base else validate(a.scene)
    print(json.dumps({k:v for k,v in r.items() if k!='tiles'},indent=2),flush=True)
    if r.get('failed'):raise SystemExit(1)
