"""Bake one native cluster as a bounded, textured geometry test.

Existing per-model LODs can be selected explicitly. Texture atlasing and
cross-object simplification are not yet performed.
No gameplay archives are modified by this command.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET
import zipfile
import numpy as np
from geometry_opacity import Assets
from hlod_proxy_mesh import compose
from editor_archive import decode


def transform(node):
    w,x,y,z=map(float,node.get('Rotation','1,0,0,0').split(','))
    q=np.array([w,x,y,z])
    if not np.isfinite(q).all() or abs(np.linalg.norm(q)-1)>1e-4:
        raise ValueError('Invalid native rotation')
    r=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
    scale=np.array(list(map(float,node.get('Scale','1,1,1').split(','))))
    return np.column_stack((r*scale,list(map(float,node.get('Pos','0,0,0').split(',')))))


def select_mesh(assets,path,lod):
    if not 0<=lod<=6:raise ValueError('Mesh LOD must be between 0 and 6')
    stem=path.replace('\\','/').lower().removesuffix('.cgf')
    for level in range(lod,0,-1):
        candidate=f'{stem}_lod{level}.cgf'
        if candidate in assets.entries:return assets.get(candidate)
    return assets.get(path)


def vegetation_placements(tree, cluster, assets, terrain_pak, mesh_lod):
    from compiled_terrain import parse
    from upgrade_map import read
    from world_surface_coverage import HeightField, transforms
    root,blocks=decode((Path(tree).parent/'leveldata/VegetationMap.dat').read_bytes())
    data=blocks['VegetationInstancesArray']
    if len(data)%33:raise ValueError('Invalid native vegetation instance block')
    dtype=np.dtype([('pos','<f4',3),('scale','<f4'),('group','<i4'),('brightness','u1'),('angles','<f4',3)])
    rows=np.frombuffer(data,dtype=dtype)
    low=np.array(list(map(float,cluster.get('BoxMin').split(','))))
    high=np.array(list(map(float,cluster.get('BoxMax').split(','))))
    # Half-open XY cells: a boundary instance belongs to exactly one leaf.
    selected=rows[np.all((rows['pos'][:,:2]>=low[:2])&(rows['pos'][:,:2]<high[:2]),axis=1)]
    if not len(selected):raise ValueError('Native vegetation cell has no placements')
    definitions=list(root.find('Objects'))
    with zipfile.ZipFile(terrain_pak) as z:
        height=HeightField(parse(read(z,'terrain/terrain.dat')))
    for index in sorted(set(selected['group'])):
        if not 0<=index<len(definitions):raise ValueError('Vegetation group dependency missing')
        group=definitions[index];instances=selected[selected['group']==index]
        alignment=float(group.get('AlignToTerrainCoefficient')) if group.get('AlignToTerrain')=='1' else 0
        angles=instances['angles'][:,[1,2,0]]*(255/(2*np.pi))
        matrices=transforms(instances['pos'],instances['scale'],angles,alignment,
                            height.normal(instances['pos']) if alignment else None)
        blob=select_mesh(assets,group.get('FileName'),mesh_lod)
        for matrix in matrices:yield blob,matrix[:3,:],group.get('Material') or None


def bake(tree, cluster_name, assets_folder, output_root, terrain_pak=None, mesh_lod=0):
    root=ET.parse(tree).getroot()
    matches=[n for n in root.iter() if n.tag in ('ClusterLod','VegetationLod') and n.get('Name')==cluster_name]
    if len(matches)!=1:raise ValueError('Expected one native cluster by name')
    cluster=matches[0]
    vegetation=cluster.tag=='VegetationLod'
    if vegetation and (len(cluster) or not terrain_pak):
        raise ValueError('Vegetation test requires a leaf cell and terrain package')
    if not vegetation and (not len(cluster) or any(n.tag!='Brush' for n in cluster)):
        raise ValueError('This bounded test requires a leaf brush cluster')
    geometry=PurePosixPath(cluster.get('Geometry','').replace('\\','/'))
    if geometry.is_absolute() or '..' in geometry.parts or geometry.suffix!='.cgf' or geometry.parts[0]!='levels':
        raise ValueError('Invalid native proxy output path')
    material=str(geometry.with_suffix('.mtl'))
    assets=Assets(assets_folder)
    try:
        placements=(vegetation_placements(tree,cluster,assets,terrain_pak,mesh_lod) if vegetation else
                    ((select_mesh(assets,n.get('Geometry'),mesh_lod),transform(n),n.get('Material')) for n in cluster))
        count=0
        def counted():
            nonlocal count
            for item in placements:count+=1;yield item
        blob,report=compose(counted(),list(map(float,cluster.get('Pos','0,0,0').split(','))),assets.get,material)
    finally:assets.close()
    output=Path(output_root).resolve()/str(geometry)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.with_suffix('.mtl').write_text(report.pop('material_xml'),encoding='utf-8')
    output.write_bytes(blob)
    report.update(cluster=cluster_name,placements=count,geometry=str(output),bytes=len(blob),mesh_lod_limit=mesh_lod,package_ready=False)
    output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tree',type=Path,required=True)
    p.add_argument('--cluster',required=True)
    p.add_argument('--assets',type=Path,required=True)
    p.add_argument('--output-root',type=Path,required=True)
    p.add_argument('--terrain-pak',type=Path)
    p.add_argument('--mesh-lod',type=int,default=0)
    a=p.parse_args();print(json.dumps(bake(a.tree,a.cluster,a.assets,a.output_root,a.terrain_pak,a.mesh_lod),indent=2))
