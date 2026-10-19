"""Stage a native HLOD replacement while preserving all installed near records.

The native editor owns hierarchy topology, proxy records, bounds and hash.
Only HLOD entries and appended terrain resource-table entries are replaced.
Gameplay, terrain samples, vegetation definitions and collision remain intact.
"""
import argparse
from collections import Counter,defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile
from building_brushes import resource_table
from compiled_terrain import parse
from native_hlod_scene import identity
from rebuild_geometry_hlod import extract
from spatial_hlod import records
from upgrade_map import read


def assemble(current_level,native_level,output):
    current_level,native_level,output=map(Path,(current_level,native_level,output))
    status=json.loads((native_level/'native-proxy-bake.json').read_text())
    if not status['complete'] or status['failures']:raise ValueError('Whole-world proxy bake is incomplete')
    with zipfile.ZipFile(current_level/'level.pak') as z:
        old_xml=read(z,'terrain/hlods.xml');old_data=read(z,'terrain/hlods.dat')
    with zipfile.ZipFile(current_level/'terrain.pak') as z:old_terrain=read(z,'terrain/terrain.dat')
    original=parse(old_terrain)
    with zipfile.ZipFile(native_level/'level.pak') as z:
        native_xml=read(z,'terrain/hlods.xml');native_data=read(z,'terrain/hlods.dat')
    with zipfile.ZipFile(native_level/'terrain.pak') as z:exported=parse(read(z,'terrain/terrain.dat'))
    source=ET.parse(native_level/'hlodtree.xml').getroot();compiled=ET.fromstring(native_xml)
    if source.get('Hash')!=compiled.get('Hash'):raise ValueError('Compiled hierarchy hash does not match authored native tree')
    details=[r for _,rs,_ in extract(ET.fromstring(old_xml),old_data) for r in rs]
    counts=Counter();brushes={};vegetation=defaultdict(list)
    for r in details:
        if len(r)==104:
            brushes[identity(r,counts[r])]=r;counts[r]+=1
        else:
            x,y=struct.unpack_from('<2f',r,44)
            vegetation[(math.floor(x/64),math.floor(y/64))].append(r)
    assigned={};all_assigned=[]
    for node in source.iter():
        if node.tag not in ('LayerLod','ClusterLod','VegetationLod'):continue
        own=[]
        for child in node:
            if child.tag=='Brush':own.append(brushes.pop(child.get('Id').lower().strip('{}')))
        if node.tag=='VegetationLod' and not len(node):
            low=list(map(float,node.get('BoxMin').split(',')));high=list(map(float,node.get('BoxMax').split(',')))
            if any(abs(high[i]-low[i]-64)>.01 for i in (0,1)):raise ValueError('Unexpected native vegetation leaf size')
            own+=vegetation.pop((round(low[0]/64),round(low[1]/64)),[])
        assigned[node.get('Name')]=own;all_assigned.extend(own)
    if brushes or vegetation or Counter(all_assigned)!=Counter(details):raise ValueError('Native hierarchy loses or duplicates near placements')
    paths={n.get('Name'):n.get('Geometry').replace('\\','/').lower() for n in source.iter()
           if n.tag in ('ClusterLod','VegetationLod') and n.get('Geometry')}
    meshes=list(original.tables['meshes']['paths']);materials=list(original.tables['materials']['paths'])
    meshids={p.lower():i for i,p in enumerate(meshes)};matids={p.lower():i for i,p in enumerate(materials)}
    def remap(index,kind):
        if index<0:return index
        name=exported.tables[kind]['paths'][index].replace('\\','/').lower()
        table,ids=(meshes,meshids) if kind=='meshes' else (materials,matids)
        if name not in ids:ids[name]=len(table);table.append(name)
        return ids[name]
    result=bytearray(struct.pack('<I',2));proxy_count=0;native_near_count=0;native_details=[]
    def visit(node):
        nonlocal proxy_count,native_near_count
        offset,size=int(node.get('DataOffset')),int(node.get('DataSize'))
        payload=records(native_data[offset+4:offset+size]);proxy_indices=set()
        own=list(assigned.pop(node.get('Name'),[]))
        for child in node:
            index=int(child.get('ProxyIndex','-1'))
            if index<0:
                if child.get('Name') in paths:raise ValueError('Native compiler omitted a proxy: '+child.get('Name'))
                continue
            if index in proxy_indices or index>=len(payload) or len(payload[index])!=104:raise ValueError('Invalid native proxy linkage')
            proxy_indices.add(index);record=bytearray(payload[index]);mesh=struct.unpack_from('<H',record,40)[0]
            name=exported.tables['meshes']['paths'][mesh].replace('\\','/').lower()
            if name!=paths.get(child.get('Name')):raise ValueError('Native proxy binds the wrong mesh')
            struct.pack_into('<H',record,40,remap(mesh,'meshes'))
            struct.pack_into('<i',record,96,remap(struct.unpack_from('<i',record,96)[0],'materials'))
            child.set('ProxyIndex',str(len(own)));own.append(bytes(record));proxy_count+=1
        native_near_count+=len(payload)-len(proxy_indices)
        native_details.extend(r for i,r in enumerate(payload) if i not in proxy_indices)
        block=b''.join(own);node.set('DataOffset',str(len(result)));node.set('DataSize',str(len(block)+4))
        result.extend(struct.pack('<I',len(block))+block)
        for child in node:visit(child)
    visit(compiled)
    if assigned or proxy_count!=len(paths):raise ValueError('Native compiled hierarchy does not cover the source groups')
    def placement_key(record,terrain):
        kind=struct.unpack_from('<I',record)[0];index=struct.unpack_from('<H',record,40)[0]
        name=terrain.tables['meshes' if kind==1 else 'vegetation']['paths'][index].replace('\\','/').lower()
        offsets=(56,72,88) if kind==1 else (44,48,52)
        return kind,name,tuple(round(struct.unpack_from('<f',record,o)[0],3) for o in offsets)
    expected=Counter(placement_key(r,original) for r in details)
    actual=Counter(placement_key(r,exported) for r in native_details)
    missing=expected-actual;extra=actual-expected
    vegetation_root=source.find('VegetationLod')
    zmin=float(vegetation_root.get('BoxMin').split(',')[2]);zmax=float(vegetation_root.get('BoxMax').split(',')[2])
    # Native vegetation export clips its 3-D grid at BoxMin/BoxMax. Retain
    # original placements outside that vertical interval in their XY leaf,
    # matching the geometry baker, rather than deleting them from the map.
    if extra or any(k[0]!=2 or zmin<=k[2][2]<zmax for k in missing):
        raise ValueError(f'Native compiler changed in-bounds placement membership: {len(missing)} missing, {len(extra)} extra')
    if len(meshes)>65536:raise ValueError('Mesh resource index overflow')
    start=original.tables['meshes']['offset']-4
    end=original.tables['materials']['offset']+len(original.tables['materials']['paths'])*256
    terrain=bytearray(old_terrain[:start]+resource_table(meshes)+resource_table(materials)+old_terrain[end:])
    struct.pack_into('<I',terrain,4,len(terrain));check=parse(bytes(terrain))
    if check.data[check.nodes[0]['offset']:]!=original.data[original.nodes[0]['offset']:]:raise ValueError('Terrain data changed')
    new_xml=ET.tostring(compiled,encoding='utf-8')
    output.mkdir(parents=True,exist_ok=True)
    for archive,changes in (('level.pak',{'terrain/hlods.xml':new_xml,'terrain/hlods.dat':bytes(result)}),
                            ('terrain.pak',{'terrain/terrain.dat':bytes(terrain)})):
        pending=output/(archive+'.pending')
        with zipfile.ZipFile(current_level/archive) as src,zipfile.ZipFile(pending,'w',zipfile.ZIP_STORED) as dest:
            for entry in src.infolist():dest.writestr(entry,changes.get(entry.filename.replace('\\','/').lower(),read(src,entry.filename)))
        with zipfile.ZipFile(pending) as check:
            if check.testzip():raise ValueError('Corrupt staged archive')
        pending.replace(output/archive)
    # Bounded rollback data, not another copy of the world assets.
    rollback=output/'previous-hlod-entries.zip'
    if not rollback.exists():
        with zipfile.ZipFile(rollback,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('terrain/hlods.xml',old_xml);z.writestr('terrain/hlods.dat',old_data);z.writestr('terrain/terrain.dat',old_terrain)
    shard=0;written=0;z=None;archive=None
    try:
        for path in sorted(paths.values()):
            geometry=native_level.parents[1]/path
            receipt=json.loads(geometry.with_suffix('.json').read_text())
            for suffix,key in (('.cgf','geometry_sha256'),('.mtl','material_sha256')):
                file=geometry.with_suffix(suffix);data=file.read_bytes()
                if hashlib.sha256(data).hexdigest()!=receipt[key]:raise ValueError('Proxy changed since bake: '+str(file))
                if z is None or written+len(data)>1024**3:
                    if z is not None:z.close();archive.with_suffix('.pending').replace(archive)
                    archive=output/f'kingdomcomegluemapper_native_hlod_{shard:02}.pak';shard+=1;written=0
                    z=zipfile.ZipFile(archive.with_suffix('.pending'),'w',zipfile.ZIP_STORED,allowZip64=False)
                z.writestr(str(Path(path).with_suffix(suffix)).replace('\\','/'),data)
                written+=len(data)
    finally:
        if z is not None:z.close()
    if archive is not None:archive.with_suffix('.pending').replace(archive)
    report={'near_records_preserved':len(details),'native_near_records':native_near_count,'proxies':proxy_count,
            'retained_native_vertical_exclusions':[{'kind':k[0],'geometry':k[1],'position':k[2],'count':v} for k,v in missing.items()],
            'groups':len(list(compiled.iter('HLod'))),'native_hash':source.get('Hash'),
            'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.pak')},
            'runtime_verified':False,'ready_for_playtest':True}
    (output/'replacement.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('current-level','native-level','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();print(json.dumps(assemble(a.current_level,a.native_level,a.output),indent=2))
