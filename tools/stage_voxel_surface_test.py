"""Stage/install/restore a bounded offline-GI test with content-addressed backups."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from upgrade_map import read, digest
from voxel_lighting import validate_pair
from world_voxels import configure_environment



def require_closed_game():
    r=subprocess.run(['powershell','-NoProfile','-Command',
        'if (Get-Process KingdomCome,Editor -ErrorAction SilentlyContinue) { exit 1 }'],capture_output=True)
    if r.returncode:raise RuntimeError('Close the game/editor before changing test packages')


def restore(output):
    require_closed_game()
    output=Path(output);r=json.loads((output/'installation.json').read_text())
    for n,entry in r['files'].items():
        dest=Path(r['level_dir'])/n
        if digest(dest)!=entry['test_sha256']:raise ValueError('Installed file changed since test: '+n)
        if digest(output/'backup'/n)!=entry['original_sha256']:raise ValueError('Backup checksum mismatch: '+n)
    for n in r['files']:shutil.copy2(output/'backup'/n,Path(r['level_dir'])/n)
    r['restored']=True;(output/'installation.json').write_text(json.dumps(r,indent=2)+'\n')


def stage(surface_build,level_dir,output,install=False):
    surface_build,level_dir,output=map(Path,(surface_build,level_dir,output))
    if output.exists():raise FileExistsError('Use a fresh staging folder')
    r=json.loads((surface_build/'report.json').read_text());tile=r['tile']
    if Path(tile).name!=tile or not tile.replace('_','').isdigit():raise ValueError('Invalid tile name')
    idx=(surface_build/(tile+'.idx')).read_bytes();vox=(surface_build/(tile+'.vox')).read_bytes()
    check=validate_pair(idx,vox)
    if check['channel_masks']!=[7] or not check['blocks']:raise ValueError('Missing surface channels')
    replacements={'terrain/svo/'+tile+'.idx':idx,'terrain/svo/'+tile+'.vox':vox}
    owners=[]
    for p in level_dir.glob('svo-part*.pak'):
        with zipfile.ZipFile(p) as z:
            names={n.lower() for n in z.namelist()}
            if names & replacements.keys():
                if not replacements.keys()<=names:raise ValueError('Tile pair split across packages')
                owners.append(p)
    if len(owners)!=1:raise ValueError('Expected one installed owner for the test tile')
    output.mkdir(parents=True);(output/'backup').mkdir();(output/'package').mkdir()
    paths=[level_dir/'level.pak',owners[0]]
    for path in paths:shutil.copy2(path,output/'backup'/path.name)
    with zipfile.ZipFile(paths[0]) as z:
        mission=ET.fromstring(read(z,'mission_mission0.xml'))
    gi=mission.find('Environment/Total_Illumination_v2')
    if gi is None:raise ValueError('Missing mission GI settings')
    previous=dict(gi.attrib)
    mission=ET.fromstring(configure_environment(ET.tostring(mission),surface_channels=True))
    gi=mission.find('Environment/Total_Illumination_v2')
    for path in paths:
        changes={'mission_mission0.xml':ET.tostring(mission)} if path.name=='level.pak' else replacements
        with zipfile.ZipFile(path) as src,zipfile.ZipFile(output/'package'/path.name,'x',zipfile.ZIP_STORED) as dst:
            for entry in src.infolist():dst.writestr(entry.filename,changes.get(entry.filename.lower(),read(src,entry.filename)))
        with zipfile.ZipFile(output/'package'/path.name) as z:
            if z.testzip():raise ValueError('Package CRC failure')
    report={'level_dir':str(level_dir.resolve()),'surface_build':str(surface_build.resolve()),
            'scope':'Experimental house-only voxel tile; surrounding scene GI is intentionally incomplete',
            'runtime_verified':False,'previous_gi':previous,'test_gi':gi.attrib,'files':{},'installed':False}
    for path in paths:report['files'][path.name]={'original_sha256':digest(path),'test_sha256':digest(output/'package'/path.name)}
    (output/'installation.json').write_text(json.dumps(report,indent=2)+'\n')
    if install:
        require_closed_game()
        for path in paths:
            if digest(path)!=report['files'][path.name]['original_sha256']:raise ValueError('Baseline changed during staging')
        for path in paths:shutil.copy2(output/'package'/path.name,path)
        for path in paths:
            if digest(path)!=report['files'][path.name]['test_sha256']:raise ValueError('Installation checksum mismatch')
        report['installed']=True;(output/'installation.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--restore',type=Path)
    for n in ('surface-build','level-dir','output'):p.add_argument('--'+n,type=Path)
    p.add_argument('--install',action='store_true')
    a=p.parse_args()
    if a.restore:restore(a.restore)
    elif all((a.surface_build,a.level_dir,a.output)):print(json.dumps(stage(a.surface_build,a.level_dir,a.output,a.install),indent=2))
    else:p.error('Supply surface build, level directory and output, or restore directory')
