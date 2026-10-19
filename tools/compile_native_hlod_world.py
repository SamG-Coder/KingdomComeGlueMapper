"""Compile the completed placement-only scene with the official KCD2 editor."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from upgrade_map import read


def compile_world(editor_root,level_name,evidence):
    editor_root=Path(editor_root).resolve();level=editor_root/'Data/Levels'/level_name
    if level.resolve()!=level.absolute() or 'mods' in [s.lower() for s in level.resolve().parts]:
        raise ValueError('Use the dedicated authored scene, not an installed mod')
    status=json.loads((level/'native-proxy-bake.json').read_text())
    if not status['complete'] or status['failures']:raise ValueError('Whole-world geometry is incomplete')
    tree=level/'hlodtree.xml';before=hashlib.sha256(tree.read_bytes()).hexdigest()
    for node in ET.parse(tree).iter():
        if node.tag in ('ClusterLod','VegetationLod') and node.get('Geometry') and not (editor_root/'Data'/node.get('Geometry')).is_file():
            raise ValueError('Missing generated geometry')
    running=subprocess.run(['powershell','-NoProfile','-Command',
        'if (Get-Process KingdomCome,Editor -ErrorAction SilentlyContinue) { exit 1 }'],capture_output=True)
    if running.returncode:raise RuntimeError('Game/editor is already running')
    args=[str(editor_root/'Bin/Win64ReleaseSteamLTO_DLL/Editor.exe'),'-layerExportProfile','game',
          '-export',str(level/(level_name+'.cry')),'-unattended','-ignoreAssert','-TraceNoServer','-noSourceControl']
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    started=time.time_ns();result=subprocess.run(args,cwd=editor_root,startupinfo=startup)
    evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    log=(editor_root/'editor.log').read_bytes();(evidence/'whole-world-native-export.log').write_bytes(log)
    if result.returncode:raise RuntimeError(f'Native whole-world export failed: {result.returncode}')
    if hashlib.sha256(tree.read_bytes()).hexdigest()!=before:raise ValueError('Native export changed the authored hierarchy')
    if b'Exiting editor with code 0 (OK)' not in log:raise ValueError('Native export did not finish cleanly')
    errors=[line.decode(errors='replace') for line in log.splitlines() if b'CGF Loading Failed' in line and level_name.encode() in line]
    if errors:raise ValueError('Generated proxy rejected by native loader: '+errors[0])
    for filename in ('level.pak','terrain.pak'):
        p=level/filename
        if not p.exists() or p.stat().st_mtime_ns<started:raise ValueError('Missing fresh native export: '+filename)
    with zipfile.ZipFile(level/'level.pak') as z:compiled=ET.fromstring(read(z,'terrain/hlods.xml'))
    report={'command':args,'exit_code':result.returncode,'groups':len(list(compiled.iter('HLod'))),
            'native_hash':compiled.get('Hash'),'proxy_errors':errors,'runtime_verified':False}
    (evidence/'whole-world-native-export.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--editor-root',type=Path,required=True)
    p.add_argument('--level',required=True);p.add_argument('--evidence',type=Path,required=True)
    a=p.parse_args();print(json.dumps(compile_world(a.editor_root,a.level,a.evidence),indent=2))
