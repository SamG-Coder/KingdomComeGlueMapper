"""Install a validated whole-world HLOD test without replacing gameplay data."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from rebuild_geometry_hlod import extract
from upgrade_map import read


def digest(data):return hashlib.sha256(data).hexdigest()


def closed():
    result=subprocess.run(['powershell','-NoProfile','-Command',
        'if (Get-Process KingdomCome,Editor -ErrorAction SilentlyContinue) { exit 1 }'],capture_output=True)
    if result.returncode:raise RuntimeError('Close the game/editor before installing the world replacement')


def install(staged,targets):
    staged=Path(staged);report=json.loads((staged/'replacement.json').read_text())
    if not report['ready_for_playtest']:raise ValueError('Package has not passed validation')
    closed();changes={}
    for filename,names in (('level.pak',('terrain/hlods.xml','terrain/hlods.dat')),
                           ('terrain.pak',('terrain/terrain.dat',))):
        with zipfile.ZipFile(staged/filename) as z:changes[filename]={n:read(z,n) for n in names}
    with zipfile.ZipFile(staged/'previous-hlod-entries.zip') as z:
        previous={n:read(z,n) for n in ('terrain/hlods.xml','terrain/hlods.dat','terrain/terrain.dat')}
    expected=Counter(r for _,rs,_ in extract(ET.fromstring(previous['terrain/hlods.xml']),previous['terrain/hlods.dat']) for r in rs)
    pending=[];backups={};installed=[]
    for target in map(Path,targets):
        level=target/'Data/Levels/kcd1_travel'
        with zipfile.ZipFile(level/'terrain.pak') as z:
            if read(z,'terrain/terrain.dat')!=previous['terrain/terrain.dat']:raise ValueError('Target terrain/resources differ: '+str(target))
        with zipfile.ZipFile(level/'level.pak') as z:
            old_xml=read(z,'terrain/hlods.xml');old_dat=read(z,'terrain/hlods.dat')
        actual=Counter(r for _,rs,_ in extract(ET.fromstring(old_xml),old_dat) for r in rs)
        if actual!=expected:raise ValueError('Target detailed world differs: '+str(target))
        key=digest(old_xml+old_dat);backup=staged/(key+'.prior-hlod.zip')
        if not backup.exists():
            with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as z:
                z.writestr('terrain/hlods.xml',old_xml);z.writestr('terrain/hlods.dat',old_dat)
        backups[str(target)]=str(backup)
        for filename,replacements in changes.items():
            path=level/filename;temp=path.with_suffix('.native-hlod-pending')
            before=digest(path.read_bytes())
            with zipfile.ZipFile(path) as src,zipfile.ZipFile(temp,'w',zipfile.ZIP_STORED) as dest:
                for entry in src.infolist():
                    key=entry.filename.replace('\\','/').lower()
                    dest.writestr(entry,replacements[key] if key in replacements else read(src,entry.filename))
            with zipfile.ZipFile(temp) as z:
                if z.testzip():raise ValueError('Staged archive CRC failed')
            pending.append((path,temp,before))
    for name,sha in report['files'].items():
        if digest((staged/name).read_bytes())!=sha:raise ValueError('Validated package changed: '+name)
    closed()
    for path,temp,before in pending:
        if digest(path.read_bytes())!=before:raise ValueError('Target changed while preparing replacement')
    # Proxy resources first. Hard links on D: share the validated package bytes;
    # installing to both runtimes does not create two extra multi-GB copies.
    for target in map(Path,targets):
        for shard in sorted(staged.glob('kingdomcomegluemapper_native_hlod_*.pak')):
            destination=target/'Data'/shard.name
            if destination.exists():raise ValueError('Existing native HLOD shard needs explicit migration: '+str(destination))
            os.link(shard,destination)
    for path,temp,before in pending:
        temp.replace(path);installed.append({'path':str(path),'sha256':digest(path.read_bytes())})
    receipt={'status':'installed_for_playtest','runtime_verified':False,'targets':installed,'rollback_hlods':backups,
             'rollback_terrain':str(staged/'previous-hlod-entries.zip'),'replacement':report}
    (staged/'installation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--staged',type=Path,required=True)
    p.add_argument('--target',type=Path,action='append',required=True)
    a=p.parse_args();print(json.dumps(install(a.staged,a.target),indent=2))
