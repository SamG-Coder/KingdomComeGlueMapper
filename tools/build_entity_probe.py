"""Append static visual representations of original furnishings and placed items."""
import argparse
from game_paths import GameLibrary
from collections import Counter
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from entity_visuals import collect_visuals
from static_assets import asset_pack, character_definition
from upgrade_map import xml


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=GameLibrary,default=Path(r'D:\SteamLibrary\steamapps\common'))
    p.add_argument('--base-level',required=True)
    p.add_argument('--level',required=True)
    p.add_argument('--character-visuals', action='store_true', help='Try native AnimChar visuals for simple door/grindstone CDF assemblies')
    args=p.parse_args()
    if not all(n and n.replace('_','').isalnum() for n in (args.level,args.base_level)):p.error('Invalid level')
    root=args.library/'KCD2Mod/Data';base=root/'Levels'/args.base_level;dest=root/'Levels'/args.level
    prefix='glueitems/'+args.level+'/'
    if dest.exists() or (root/prefix).exists():p.error('Choose a new level')
    with ExitStack() as stack:
        source=stack.enter_context(zipfile.ZipFile(args.library/'KingdomComeDeliverance/Data/Levels/rataje/level.pak'))
        archive=stack.enter_context(zipfile.ZipFile(base/'level.pak'))
        doc=ET.fromstring(archive.read('objects_mission0.xml'))
        names={e.get('Name') for e in doc.iter('Entity')}
        visuals,excluded=collect_visuals(source,args.library,args.character_visuals)
        visuals=[v for v in visuals if v['entity'].get('Name') not in names]
        cache=stack.enter_context(tempfile.TemporaryDirectory(prefix='entities-',dir='outputs'))
        index,emitted,material,mesh_bytes=asset_pack(stack,args.library,prefix,cache)
        meshes={};failures={};added=[]
        for v in visuals:
            model=v['model']
            try:
                if model not in meshes:
                    target=prefix+'model'+str(len(meshes))+Path(model).suffix
                    if model.endswith('.cdf'):
                        character_definition(index,emitted,material,mesh_bytes,model,target)
                    else:
                        blob=mesh_bytes(model)
                        lods={lod:mesh_bytes(model[:-4]+f'_lod{lod}.cgf') for lod in range(1,7) if model[:-4]+f'_lod{lod}.cgf' in index}
                        emitted[target]=blob
                        for lod,payload in lods.items():emitted[target[:-4]+f'_lod{lod}.cgf']=payload
                    meshes[model]=target
                if model.endswith('.cdf'):
                    v['entity'].find('Properties').set('object_Model', meshes[model])
                else:
                    v['entity'].set('Geometry',meshes[model])
                if v['material']:v['entity'].set('Material',material(v['material']))
            except (KeyError,ValueError,FileNotFoundError) as error:
                failures[v['source_id']]={'model':model,'reason':str(error)}
                continue
            doc.append(v['entity']);added.append({k:value for k,value in v.items() if k!='entity'})
        print(f'Packaged {len(added)} additional item entities across {len(meshes)} models',flush=True)
        dest.mkdir(parents=True)
        shutil.copy2(base/'terrain.pak',dest/'terrain.pak')
        with zipfile.ZipFile(dest/'level.pak','x',zipfile.ZIP_STORED) as output:
            for name in archive.namelist():
                payload=archive.read(name)
                if name=='objects_mission0.xml':payload=xml(doc)
                elif name=='levelinfo.xml':
                    info=ET.fromstring(payload);info.set('Name','data/levels/'+args.level);payload=xml(info);(dest/name).write_bytes(payload)
                elif name=='leveldata.xml':
                    info=ET.fromstring(payload);info.find('LevelInfo').set('Name',args.level);payload=xml(info)
                output.writestr(name,payload)
        for name,path in emitted.items():
            target=(root/name).resolve()
            if not target.is_relative_to((root/prefix).resolve()):raise ValueError('Asset path escapes namespace')
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as out,path.open('rb') as inp:shutil.copyfileobj(inp,out)
        report=dict(level=args.level,base_level=args.base_level,added=len(added),classes=dict(Counter(v['source_class'] for v in added)),
                    excluded=excluded,failures=failures,records=added,interactive=False)
        (dest/'entities-report.json').write_text(json.dumps(report,indent=2));Path('reports/entities-probe.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k not in ('records','failures')}),flush=True)


if __name__=='__main__':main()
