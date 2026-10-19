"""Resumable whole-world proxy bake from a KCD2-authored HLOD tree.

Only writes the dedicated editor scene's generated proxy paths. A successful
bake is not installation or runtime validation. Failed dependencies stop package
promotion and are recorded with the exact native group and source model.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import time
import struct
import xml.etree.ElementTree as ET
import zipfile
from functools import lru_cache
from collections import Counter
import numpy as np
from bake_native_hlod_proxy import transform, select_mesh
from compiled_terrain import parse
from editor_archive import decode
from geometry_opacity import Assets
from hlod_proxy_mesh import compose
from merge_hlod_geometry import merge
from clothing_regions import read_chunks
from upgrade_map import read
from world_surface_coverage import HeightField, transforms

VERSION=1


def run(tree,assets_folder,output_root,terrain_pak,limit=None,only=None):
    tree=Path(tree);output_root=Path(output_root).resolve()
    root=ET.parse(tree).getroot()
    nodes=[n for n in root.iter() if n.tag in ('ClusterLod','VegetationLod') and n.get('Geometry')]
    if only:
        nodes=[n for n in nodes if n.get('Name')==only]
        if len(nodes)!=1:raise ValueError('Expected one native proxy by name')
    status_path=tree.parent/('native-proxy-probe.json' if only else 'native-proxy-bake.json')
    # Small cells first; every parent is built from the original descendants,
    # never from already reduced child geometry.
    nodes.sort(key=lambda n:((output_root/n.get('Geometry')).exists(),-n.get('Name').count('_'),n.get('Name')))
    assets=Assets(assets_folder)
    engine=output_root.parent/'Engine/Engine.pak'
    if engine.exists():
        with zipfile.ZipFile(engine) as archive:
            for name in archive.namelist():assets.entries.setdefault(name.replace('\\','/').lower(),(engine,name))
    @lru_cache(maxsize=1024)
    def material(path):return assets.get(path)
    @lru_cache(maxsize=256)
    def model(path,lod,override):
        # External LOD files sometimes reference slots absent from the placed
        # material. Keep the highest compatible source representation instead
        # of manufacturing a material or silently assigning the wrong slot.
        for requested in range(lod,-1,-1):
            blob=select_mesh(assets,path,requested);chunks=read_chunks(blob);lookup={c.id:c for c in chunks}
            valid=True;render_count=0
            for node in chunks:
                if node.kind!=0x100b or node.data[:64].startswith((b'$physics',b'$lod')):continue
                mid,_,_,matid=struct.unpack_from('<4i',node.data,64)
                mesh=lookup.get(mid)
                if mesh is None or mesh.kind!=0x1000:continue
                render_count+=1
                mat=lookup.get(matid)
                if mat is None:continue # composer provides the precise error
                path_mtl=(override or mat.data[:128].split(b'\0')[0].decode()).removesuffix('.mtl')+'.mtl'
                sub=ET.fromstring(material(path_mtl)).find('SubMaterials')
                if sub is None:continue
                ns,sid=struct.unpack_from('<2I',mesh.data,16);subset=lookup[sid]
                if any(not 0<=struct.unpack_from('<i',subset.data,32+i*36)[0]<len(sub) for i in range(ns)):valid=False
            if valid and render_count:return blob
            if requested==0 and render_count==0:return None
        raise ValueError('No material-compatible mesh representation: '+path)
    veg,blocks=decode((tree.parent/'leveldata/VegetationMap.dat').read_bytes())
    dtype=np.dtype([('pos','<f4',3),('scale','<f4'),('group','<i4'),('brightness','u1'),('angles','<f4',3)])
    rows=np.frombuffer(blocks['VegetationInstancesArray'],dtype=dtype)
    definitions=list(veg.find('Objects'))
    with zipfile.ZipFile(terrain_pak) as z:height=HeightField(parse(read(z,'terrain/terrain.dat')))
    order=np.argsort(rows['pos'][:,0],kind='stable');ordered_x=rows['pos'][order,0]
    matrices=np.empty((len(rows),3,4),dtype='<f4')
    for index in np.unique(rows['group']):
        ids=np.flatnonzero(rows['group']==index);group=definitions[index];instances=rows[ids]
        alignment=float(group.get('AlignToTerrainCoefficient')) if group.get('AlignToTerrain')=='1' else 0
        angles=instances['angles'][:,[1,2,0]]*(255/(2*np.pi))
        matrices[ids]=transforms(instances['pos'],instances['scale'],angles,alignment,
                                height.normal(instances['pos']) if alignment else None)[:,:3,:]
    del height,blocks
    # Input fingerprint includes the producer and every asset archive metadata;
    # generated receipts additionally hash both output files before reuse.
    inputs={'version':VERSION,'tree':hashlib.sha256(tree.read_bytes()).hexdigest(),
            'vegetation':hashlib.sha256((tree.parent/'leveldata/VegetationMap.dat').read_bytes()).hexdigest(),
            'terrain':hashlib.sha256(Path(terrain_pak).read_bytes()).hexdigest(),
            'code':[hashlib.sha256((Path(__file__).parent/n).read_bytes()).hexdigest() for n in
                    ('bake_native_hlod_world.py','hlod_proxy_mesh.py','bake_native_hlod_proxy.py','merge_hlod_geometry.py')],
            'archives':[(p.name,p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(Path(assets_folder).glob('*.pak'))]}
    fingerprint=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
    failures=[];built=0;reused=0;started=time.monotonic()
    try:
        for number,node in enumerate(nodes,1):
            geometry=PurePosixPath(node.get('Geometry').replace('\\','/'))
            if geometry.is_absolute() or '..' in geometry.parts or geometry.parts[:2]!=('levels',tree.parent.name):
                raise ValueError('Proxy escapes the dedicated editor scene')
            output=output_root/str(geometry);receipt=output.with_suffix('.json');mtl=output.with_suffix('.mtl')
            if receipt.exists() and output.exists() and mtl.exists():
                saved=json.loads(receipt.read_text())
                if saved.get('fingerprint')==fingerprint and all(hashlib.sha256(p.read_bytes()).hexdigest()==saved.get(k)
                        for p,k in ((output,'geometry_sha256'),(mtl,'material_sha256'))):
                    reused+=1;continue
            try:
                vegetation=node.tag=='VegetationLod'
                lod=3 if not any(c.tag.endswith('Lod') for c in node) else 6
                count=0;nonrendering=Counter()
                def placements():
                    nonlocal count
                    if vegetation:
                        low=np.array(list(map(float,node.get('BoxMin').split(','))))
                        high=np.array(list(map(float,node.get('BoxMax').split(','))))
                        ids=order[np.searchsorted(ordered_x,low[0]):np.searchsorted(ordered_x,high[0])]
                        ids=ids[(rows['pos'][ids,1]>=low[1])&(rows['pos'][ids,1]<high[1])]
                        for index in np.unique(rows['group'][ids]):
                            group=definitions[index];selected=ids[rows['group'][ids]==index]
                            blob=model(group.get('FileName'),lod,group.get('Material') or None)
                            if blob is None:
                                count+=len(selected);nonrendering[group.get('FileName')]+=len(selected);continue
                            for ident in selected:
                                count+=1;yield blob,matrices[ident],group.get('Material') or None
                    else:
                        for child in node.iter('Brush'):
                            count+=1;blob=model(child.get('Geometry'),lod,child.get('Material'))
                            if blob is None:nonrendering[child.get('Geometry')]+=1;continue
                            yield blob,transform(child),child.get('Material')
                blob,report=compose(placements(),list(map(float,node.get('Pos','0,0,0').split(','))),material,str(geometry.with_suffix('.mtl')),material_mode='per_node')
                blob,merged=merge(blob);report.update(merged)
                xml=report.pop('material_xml').encode('utf-8')
                output.parent.mkdir(parents=True,exist_ok=True)
                for path,payload in ((output,blob),(mtl,xml)):
                    pending=path.with_suffix(path.suffix+'.pending');pending.write_bytes(payload);pending.replace(path)
                report.update(fingerprint=fingerprint,cluster=node.get('Name'),placements=count,
                              nonrendering_placements=dict(nonrendering),
                              bytes=len(blob),mesh_lod_limit=lod,geometry_sha256=hashlib.sha256(blob).hexdigest(),
                              material_sha256=hashlib.sha256(xml).hexdigest(),package_ready=False)
                receipt.write_text(json.dumps(report,indent=2)+'\n')
                built+=1
                print(f'{number}/{len(nodes)} {node.get("Name")}: {count} placements, {len(blob)} bytes',flush=True)
            except (ValueError,KeyError,FileNotFoundError) as e:
                failures.append({'cluster':node.get('Name'),'error':str(e)})
                print(f'FAILED {node.get("Name")}: {e}',flush=True)
            status={'built':built,'reused':reused,'total':len(nodes),'failures':failures,
                    'elapsed_seconds':time.monotonic()-started,'complete':number==len(nodes) and not failures,
                    'package_ready':False,'fingerprint':fingerprint}
            status_path.write_text(json.dumps(status,indent=2)+'\n')
            if limit and built+len(failures)>=limit:break
        status={'built':built,'reused':reused,'total':len(nodes),'failures':failures,
                'elapsed_seconds':time.monotonic()-started,'complete':not only and built+reused==len(nodes) and not failures,
                'package_ready':False,'fingerprint':fingerprint}
        status_path.write_text(json.dumps(status,indent=2)+'\n')
    finally:assets.close()
    return status


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('tree','assets','output-root','terrain-pak'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--limit',type=int)
    p.add_argument('--only')
    a=p.parse_args();print(json.dumps(run(a.tree,a.assets,a.output_root,a.terrain_pak,a.limit,a.only),indent=2))
