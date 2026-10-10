"""Upgrade existing scenery packages; preserve actors, travel, terrain and saves."""
import argparse
from collections import Counter
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from building_brushes import convert_brush, resource_table
from compiled_terrain import parse
from current_build import paths, working_environment, publish, write_json
from game_paths import GameLibrary
from retail_pak import PakSet
from scenery_materials import repair_materials
from scenery_streaming import source_groups, read_near, compile_hierarchy, records, check_hierarchy
from static_assets import asset_pack, _asset_library
from travel_world_package import prepare_world_for_travel
from upgrade_map import read, xml

REVISION = 1
ASSET_RECEIPT = 'hlods/kcd1/streaming.json'


def rewrite(path, changes):
    temporary=path.with_suffix('.streaming.tmp')
    if temporary.exists():raise FileExistsError(temporary)
    # Large terrain archives have >65k tiny grass tiles but no ZIP64 members.
    with zipfile.ZipFile(path) as src,zipfile.ZipFile(temporary,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as dst:
        for info in src.infolist():dst.writestr(info,changes.get(info.filename,src.read(info.filename)))
    temporary.replace(path)


def native_proxy_flags(target):
    with zipfile.ZipFile(Path(target)/'Data/Levels/trosecko/level.pak') as z:
        doc=ET.fromstring(read(z,'terrain/hlods.xml'));data=read(z,'terrain/hlods.dat')
    flags=Counter()
    for node in doc.iter('HLod'):
        payload=list(records(data,int(node.get('DataOffset')),int(node.get('DataSize'))))
        for child in node:
            i=int(child.get('ProxyIndex','-1'))
            if i>=0:
                record=payload[i][1]
                if struct.unpack_from('<I',record)[0]!=1 or struct.unpack_from('<H',record,94)[0]!=0:
                    raise ValueError('Native proxy ABI changed')
                flags[struct.unpack_from('<Q',record,32)[0]]+=1
    value,count=flags.most_common(1)[0]
    if count/sum(flags.values())<.95:raise ValueError('Native HLOD proxy flags are ambiguous')
    return value


def retarget_material_names(blob,old,new):
    result=bytearray(blob)
    if result[:4]!=b'CrCh':raise ValueError('Invalid proxy CGF')
    count,table=struct.unpack_from('<II',result,8)
    for i in range(count):
        kind,version,ident,size,off=struct.unpack_from('<HHIII',result,table+16*i)
        if kind!=0x1014:continue
        value=bytes(result[off:off+128]).split(b'\0',1)[0]
        if value.startswith(old.encode()):
            value=new.encode()+value[len(old):]
            if len(value)>=128:raise ValueError('Retargeted material name too long')
            result[off:off+128]=value.ljust(128,b'\0')
    return bytes(result)


def retarget_if_upgraded(level):
    """Travel can copy a converted world under a different engine level name."""
    level=Path(level);assets=level/'hlod.pak'
    if not assets.exists():return None
    with zipfile.ZipFile(assets) as z:
        if ASSET_RECEIPT not in z.namelist():return None
        receipt=json.loads(z.read(ASSET_RECEIPT))
        if receipt.get('revision')!=REVISION:raise ValueError('Unknown scenery upgrade revision')
        old='levels/'+receipt['level']+'/';new='levels/'+level.name+'/'
        changes={}
        if old!=new:
            for name in z.namelist():
                if name.endswith('.cgf'):changes[name]=retarget_material_names(z.read(name),old,new)
                elif name.endswith('.mtl'):changes[name]=z.read(name).replace(old.encode(),new.encode())
    if old!=new:
        with zipfile.ZipFile(level/'terrain.pak') as z:t=parse(z.read('terrain/terrain.dat'))
        mesh=[n.replace(old,new) for n in t.tables['meshes']['paths']]
        material=[n.replace(old,new) for n in t.tables['materials']['paths']]
        data=bytearray(t.data[:t.tables['meshes']['offset']-4]+resource_table(mesh)+resource_table(material)+t.data[t.nodes[0]['offset']:])
        struct.pack_into('<I',data,4,len(data));parse(bytes(data))
        rewrite(level/'terrain.pak',{'terrain/terrain.dat':data})
        receipt['level']=level.name;changes[ASSET_RECEIPT]=json.dumps(receipt,sort_keys=True).encode()
        rewrite(assets,changes)
    with zipfile.ZipFile(level/'level.pak') as z:
        check_hierarchy(z.read('terrain/hlods.dat'),ET.fromstring(z.read('terrain/hlods.xml')))
    write_json(level/'scenery-streaming.json',receipt)
    return receipt


def validate_level(level):
    """Check the exported hierarchy and every new mesh/material/texture link."""
    level=Path(level);prefix=f'levels/{level.name}/'
    with zipfile.ZipFile(level/'level.pak') as z:
        counts=check_hierarchy(z.read('terrain/hlods.dat'),ET.fromstring(z.read('terrain/hlods.xml')))
    with zipfile.ZipFile(level/'terrain.pak') as z:t=parse(z.read('terrain/terrain.dat'))
    with zipfile.ZipFile(level/'hlod.pak') as z:
        names=set(z.namelist());mesh_count=0;material_count=0;texture_count=0
        def require(name,extension=''):
            if not name.startswith(prefix):raise ValueError('Far asset references another world: '+name)
            local=name[len(prefix):]+extension
            if local not in names:raise ValueError('Missing level-local HLOD dependency: '+local)
        for name in t.tables['meshes']['paths']:
            if '/hlods/kcd1/' in name:require(name)
        for name in t.tables['materials']['paths']:
            if '/hlods/kcd1/' in name:require(name,'.mtl')
        for name in sorted(names):
            if name.endswith('.cgf'):
                mesh_count+=1;blob=z.read(name);count,table=struct.unpack_from('<II',blob,8)
                for i in range(count):
                    kind,version,ident,size,off=struct.unpack_from('<HHIII',blob,table+16*i)
                    if kind==0x1014:
                        value=blob[off:off+128].split(b'\0',1)[0].decode()
                        if '/' in value:require(value,'.mtl')
            elif name.endswith('.mtl'):
                material_count+=1
                for texture in ET.fromstring(z.read(name)).iter('Texture'):
                    value=texture.get('File','')
                    if value and not value.startswith('$') and value not in ('nearest_cubemap','nearest_cubemap.dds'):
                        require(value);texture_count+=1
        bad=z.testzip()
        if bad:raise ValueError('Invalid HLOD CRC: '+bad)
    return dict(**counts,meshes=mesh_count,materials=material_count,texture_references=texture_count)


def upgrade_level(level, source, definitions, stack, library, library_cache, flags, work):
    level=Path(level);receipt_path=level/'scenery-streaming.json'
    previous=retarget_if_upgraded(level)
    if previous is not None:return previous
    if receipt_path.exists():raise ValueError('Scenery receipt has no matching asset package')
    with zipfile.ZipFile(level/'level.pak') as z:
        near=read_near(z.read('terrain/hlods.dat'),ET.fromstring(z.read('terrain/hlods.xml')))
    with zipfile.ZipFile(level/'terrain.pak') as z: terrain=parse(z.read('terrain/terrain.dat'))
    assets_dir=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='hlod-',dir=work)))
    prefix=f'levels/{level.name}/hlods/kcd1/'
    index,emitted,material,mesh_bytes=asset_pack(stack,library,prefix,assets_dir,library_cache=library_cache)
    meshes=list(terrain.tables['meshes']['paths']);materials=list(terrain.tables['materials']['paths'])
    far={};mesh_ids={}
    active=[g for g in definitions if g['active']]
    for number,group in enumerate(active):
        original=group['path']
        if original not in mesh_ids:
            target=prefix+'mesh'+str(len(mesh_ids))+'.cgf'
            emitted[target]=mesh_bytes(original)
            mesh_ids[original]=len(meshes);meshes.append(target)
        source_material=struct.unpack_from('<i',source.data,group['offset']+92)[0]
        mat=-1
        if source_material>=0:
            name=material(source.tables['materials']['paths'][source_material])
            if name not in materials:materials.append(name)
            mat=materials.index(name)
        record=bytearray(convert_brush(source.data,group['offset'],mesh_ids[original],mat))
        # Use the installed game's exported proxy record convention, never
        # KCD1's obsolete packed uberlod id at the end of the brush transform.
        struct.pack_into('<Q',record,32,flags);struct.pack_into('<H',record,94,0)
        far[group['id']]=bytes(record)
        if (number+1)%1000==0:print(f'{level.name}: converted {number+1}/{len(active)} source far objects',flush=True)
    data,doc,stats=compile_hierarchy(near,definitions,far)
    start=terrain.tables['meshes']['offset']-4
    converted=bytearray(terrain.data[:start]+resource_table(meshes)+resource_table(materials)+terrain.data[terrain.nodes[0]['offset']:])
    struct.pack_into('<I',converted,4,len(converted))
    verified=parse(bytes(converted))
    if verified.data[verified.nodes[0]['offset']:]!=terrain.data[terrain.nodes[0]['offset']:]:
        raise ValueError('Terrain heights, surfaces or merged grass sectors changed')
    archive=level/'hlod.pak'
    if archive.exists():raise FileExistsError('Refusing to overwrite existing HLOD assets: '+str(archive))
    receipt=dict(revision=REVISION,level=level.name,**stats,validation=check_hierarchy(data,doc),
        proxy_asset_files=len(emitted),proxy_meshes=len(mesh_ids),native_proxy_flags=hex(flags),
        terrain_geometry_preserved=True,near_placements_preserved=True,runtime_verified=False)
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=False) as z:
        for name,payload in emitted.items():
            if not name.startswith(f'levels/{level.name}/'):raise ValueError('HLOD asset escaped level namespace')
            z.write(payload,name.removeprefix(f'levels/{level.name}/'))
        z.writestr(ASSET_RECEIPT,json.dumps(receipt,sort_keys=True))
    if archive.stat().st_size>1024**3:raise ValueError('HLOD package exceeds retail ZIP32 budget')
    rewrite(level/'terrain.pak',{'terrain/terrain.dat':converted})
    rewrite(level/'level.pak',{'terrain/hlods.dat':data,'terrain/hlods.xml':xml(doc)})
    write_json(receipt_path,receipt)
    print(f'{level.name}: {stats["proxy_count"]} source near/far links; all {stats["near_records"]} near placements retained',flush=True)
    return receipt


def disable_ai_trace(travel,target):
    """Remove only the known diagnostic tail and restore its six native trees."""
    from region_travel_ai_trace import resources
    with zipfile.ZipFile(Path(target)/'Data/Scripts.pak') as native:
        traced,lua=resources(native,'kcd1_travel')
        replacements={path:read(native,path) for path in traced}
    legacy_trees,legacy_lua=legacy_diagnostic_variant(traced,lua)
    with PakSet(Path(travel)/'Data') as pack:
        lookup={n.lower():n for n in pack.namelist()};changes={}
        player_name=lookup.get('scripts/entities/actor/player.lua')
        if player_name:
            player=pack.read(player_name)
            if player.endswith(lua):changes[player_name]=player[:-len(lua)]
            elif player.endswith(legacy_lua):changes[player_name]=player[:-len(legacy_lua)]
            elif b'local glueTraceCounts,' in player:raise ValueError('Unknown AI trace tail; preserve gameplay hooks')
        for name,payload in replacements.items():
            actual=lookup.get(name.lower())
            if actual and b'GlueTravelAITrace' in pack.read(actual):
                if pack.read(actual) not in (traced[name],legacy_trees[name]):raise ValueError('AI tree differs from known diagnostic: '+name)
                changes[actual]=payload
        grouped={}
        for name,payload in changes.items():grouped.setdefault(Path(pack.entries[name].filename),{})[name]=payload
    for path,changes in grouped.items():rewrite(path,changes)


def legacy_diagnostic_variant(traced,lua):
    """Recognize our earlier bounded trace in immutable TravelBase caches.

Only the known diagnostic delta is accepted; gameplay edits in a tree or the
player's trailing source still cause a refusal instead of being overwritten.
"""
    trees={}
    for name,payload in traced.items():
        doc=ET.fromstring(payload)
        for parent in list(doc.iter()):
            for i,child in enumerate(list(parent)):
                if child.tag!='IsLoadedGate':continue
                yes=child.find('Then/ExecuteLua');no=child.find('Else/ExecuteLua')
                if yes is None or no is None or 'GlueTravelAITrace,entity,"human_host:' not in yes.get('code',''):continue
                replacement=ET.Element('ExecuteLua',code=yes.get('code').replace(',true) end',') end'))
                parent.remove(child);parent.insert(i,replacement)
        trees[name]=xml(doc)
    legacy=lua.decode()
    changes=(
        ('local glueTraceCounts,glueTraceTotal,glueHostTotal={},0,0','local glueTraceCounts,glueTraceTotal={},0'),
        ('function GlueTravelAITrace(entity,phase,loaded)','function GlueTravelAITrace(entity,phase)'),
        (" then return end\n    local host=phase:sub(1,11)=='human_host:'\n    if (host and glueHostTotal>=40) or (not host and glueTraceTotal>=400) then return end",
         ' or glueTraceTotal>=400 then return end'),
        ('glueTraceCounts[key]=count\n    if host then glueHostTotal=glueHostTotal+1 else glueTraceTotal=glueTraceTotal+1 end',
         'glueTraceCounts[key]=count;glueTraceTotal=glueTraceTotal+1'),
        (' count=%d engine_loaded=%s',' count=%d'),
        ('p.z,count,tostring(loaded)))','p.z,count))'))
    for before,after in changes:
        if before not in legacy:raise ValueError('Current AI trace changed; update its versioned cleanup')
        legacy=legacy.replace(before,after)
    return trees,legacy.encode()


def upgrade(mods,config,work,travel_mode=True):
    mods,work=Path(mods),Path(work);work.mkdir(parents=True,exist_ok=True)
    mapping=work/'scenery-library.json'
    write_json(mapping,dict(schema=1,kcd1=config['source'],kcd2=config['target'],build=str(work/'scenery')))
    library=GameLibrary(mapping);result={}
    with ExitStack() as stack:
        source_z=stack.enter_context(zipfile.ZipFile(Path(config['source'])/'Data/Levels/rataje/level.pak'))
        source=parse(read(source_z,'terrain/terrain.dat'))
        layers={int(e.get('Id')):e.get('Name') for e in ET.fromstring(read(source_z,'leveldata.xml')).find('Layers')}
        definitions=source_groups(source,ET.fromstring(read(source_z,'terrain/uberlods.xml')),layers)
        cache={'library':_asset_library(stack,library)}
        index,shaders,_=cache['library']
        world=mods/'kingdomcomegluemapper'
        with PakSet(world/'Data') as pack:
            changes,restored=repair_materials(pack,index,shaders)
            grouped={}
            for name,payload in changes.items():grouped.setdefault(Path(pack.entries[name].filename),{})[name]=payload
        for path,changes in grouped.items():rewrite(path,changes)
        result['materials_restored']=restored
        print(f'Restored {len(restored)} scenery materials from verified source identities',flush=True)
        flags=native_proxy_flags(config['target'])
        for level in (world/'Data/Levels/kcd1_rataje',mods/'gluemappertravel/Data/Levels/kcd1_travel'):
            if level.exists():
                result[level.name]=upgrade_level(level,source,definitions,stack,library,cache,flags,work)
                result[level.name]['dependency_validation']=validate_level(level)
        if travel_mode:prepare_world_for_travel(world,render_diagnostics=False)
        if (mods/'gluemappertravel').exists():disable_ai_trace(mods/'gluemappertravel',config['target'])
        diagnostic_receipt=world/'render-diagnostics.json'
        if diagnostic_receipt.exists():write_json(diagnostic_receipt,dict(enabled=False,reason='Startup capture complete'))
    write_json(work/'scenery-upgrade.json',result)
    return result


def build(layout,config):
    if layout['next'].exists():raise FileExistsError('Inspect the pending build before upgrading scenery')
    shutil.copytree(layout['current'],layout['next'])
    result=upgrade(layout['next']/'Mods',config,layout['work'])
    write_json(layout['logs']/'scenery-upgrade.json',result)
    return publish(layout,config)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outputs',type=Path,default=Path(__file__).resolve().parents[1]/'outputs')
    args=parser.parse_args();layout=paths(args.outputs)
    config=json.loads((layout['cache']/'build-inputs.json').read_text())
    with working_environment(layout):receipt=build(layout,config)
    print(json.dumps(receipt['validation'],indent=2))
