"""KCD1 interior probes, reflex lights and clipping volumes on KCD2 entities."""
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import itertools
import math
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from chairs import inventory
from cooking_pots import rotate, vector
from entity_visuals import world_transform
from lighting_cubemaps import Cubemaps, SOURCE_TIMES, TARGET_TIMES, stamp, time_bracket, verify_native_contract
from upgrade_map import read, xml
from lighting_clips import associate_probe_clips

PREFIX = 'gm_lighting_'


def defaults(script):
    """Read literal Lua Properties only, without executing installed scripts."""
    text = re.sub(r'--[^\n]*','',script.decode('utf-8-sig'))
    start = re.search(r'\bProperties\s*=\s*\{',text)
    if not start: raise ValueError('Lighting script lacks Properties')
    text = text[start.end()-1:]
    tokens = re.findall(r'"[^"\n]*"|[A-Za-z_]\w*|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|[{},=;]',text)
    pos=0
    def table():
        nonlocal pos
        if tokens[pos]!='{': raise ValueError('Invalid literal property table')
        pos+=1;result={}
        while tokens[pos]!='}':
            key=tokens[pos];pos+=1
            if tokens[pos]!='=':raise ValueError('Non-literal lighting default')
            pos+=1
            if tokens[pos]=='{':value=table()
            else:
                value=tokens[pos];pos+=1
                if value.startswith('"'):value=value[1:-1]
                elif value in ('true','false'):value='1' if value=='true' else '0'
                elif not re.fullmatch(r'-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?',value):
                    raise ValueError('Non-literal lighting default: '+value)
            result[key]=value
            if tokens[pos] in (',',';'):pos+=1
        pos+=1;return result
    def node(name, values):
        result=ET.Element(name)
        for key,value in values.items():
            if isinstance(value,dict):
                if set(value)=={'x','y','z'}:result.set(key,','.join(value[k] for k in ('x','y','z')))
                else:result.append(node(key,value))
            else:result.set(key,value)
        return result
    return node('Properties',table())


def overlay(destination, source, supported_only=False):
    if source is None:return
    for name,value in source.attrib.items():
        if not supported_only or name in destination.attrib:destination.set(name,value)
    for child in source:
        existing=destination.find(child.tag)
        if existing is not None:overlay(existing,child,supported_only)
        elif not supported_only:destination.append(copy.deepcopy(child))


def bounds(properties, rotation, scale, kind):
    if kind=='EnvironmentLight':
        half=[float(properties.get('BoxSize'+a))*abs(scale[i])/2 for i,a in enumerate('XYZ')]
        corners=[rotate(rotation,p) for p in itertools.product(*[(-v,v) for v in half])]
        extent=[max(abs(p[i]) for p in corners) for i in range(3)]
    else:
        radius=float(properties.get('Radius'))*max(map(abs,scale))
        extent=[radius]*3
    if not all(math.isfinite(x) and x>0 for x in extent):raise ValueError('Invalid light volume')
    return vector([-x for x in extent]),vector(extent)


def reflex_times(properties):
    source=properties.find('ToDData')
    if source is None:raise ValueError('Missing baked reflex-light samples')
    values=[]
    for i in range(len(SOURCE_TIMES)):
        sample=source.find(f'T{i:02d}')
        color=tuple(map(float,sample.get('clrColor').split(',')))
        multiplier=float(sample.get('fMult'))
        values.append(tuple(v*multiplier for v in color))
    for i,t in enumerate(TARGET_TIMES):
        a,b,w=time_bracket(SOURCE_TIMES,t)
        # Interpolate radiance, not colour and multiplier independently.
        rgb=[x*(1-w)+y*w for x,y in zip(values[a],values[b])]
        peak=max(rgb)
        source.find(f'T{i:02d}').attrib.update(clrColor=vector([v/peak if peak else 0 for v in rgb]),fMult=format(peak,'.9g'))
    # Native has room for twelve samples but ships eleven actual bake times.
    source.find('T11').attrib.update(source.find('T00').attrib)


def shadow_spec(value):
    # Installed sys_spec_Light.cfg: KCD1's extra PC tier is [7], while
    # KCD2 uses [5] (Experimental). Values 0..4 retain their meaning.
    number=int(value)
    if number not in (0,1,2,3,4,7):raise ValueError('Unsupported KCD1 light shadow tier')
    return '5' if number==7 else str(number)


def native_probe_areas(properties, native_defaults, linked=False):
    """Use KCD2's normal probe influence path, not KCD1 visibility bypasses.

    Shipped Trosky probes use the native area defaults. In particular, a
    capture offset is a bake input, not an instruction to restrict the runtime
    light to whichever clip contains the entity origin. Preserve the authored
    influence box and bake offset, and only request explicit clipping when an
    actual source link exists.
    """
    options = properties.find('Options')
    native = native_defaults.find('Options')
    before = {key: options.get(key) for key in
              ('bIgnoresVisAreas', 'bAffectsThisAreaOnly', 'bDeferredClipBounds')}
    for key in before:
        options.set(key, native.get(key))
    if linked:
        options.set('bDeferredClipBounds', '1')
    return {'source': before, 'native': {key: options.get(key) for key in before}}


def probe_lighting_environment(blob):
    """Use native baked-probe lighting until this generated world has an SVO bake.

    Copying Trosky's active voxel GI settings without its matching voxel data
    replaces local probe diffuse light with unoccluded ambient illumination.
    This is level-local; never disable GI in the user's global configuration.
    """
    root=ET.fromstring(blob)
    gi=root.find('Environment/Total_Illumination_v2')
    if gi is None:raise ValueError('Missing native level GI configuration')
    gi.set('Active','0')
    return xml(root)


def build(files, source, target, level, guid, work):
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    root=ET.fromstring(files['objects_mission0.xml'])
    if any(e.get('Name','').startswith(PREFIX) for e in root.iter('Entity')):
        raise ValueError('Lighting already converted; use a pre-lighting baseline')
    report={'converted':{},'records':[],'unavailable_cubemaps':[], 'missing_clip_geometry':[], 'excluded_layers':{},
            'source_times_minutes':SOURCE_TIMES,'native_times_minutes':TARGET_TIMES,
            'texture_conversion':'Linear HDR time interpolation; prefiltered roughness resampling; 128x128 BC6H',
            'ordinary_lights':'Unchanged; this stage converts environment and reflex lighting.'}
    report['native_contract']=verify_native_contract(target)
    with zipfile.ZipFile(Path(source)/'Data/Levels/rataje/level.pak') as world, \
         zipfile.ZipFile(Path(source)/'Data/Scripts.pak') as old_scripts, \
         zipfile.ZipFile(Path(target)/'Data/Scripts.pak') as scripts:
        script_index={n.lower():n for n in scripts.namelist()}
        old_index={n.lower():n for n in old_scripts.namelist()}
        schemas={};old_defaults={};selected={}
        for kind in ('EnvironmentLight','ReflexLight'):
            suffix='/'+kind.lower()+'.lua'
            schemas[kind]=defaults(read(scripts,next(v for k,v in script_index.items() if k.endswith(suffix))))
            old_defaults[kind]=defaults(read(old_scripts,next(v for k,v in old_index.items() if k.endswith(suffix))))
            entities,selected[kind],deferred=inventory(world,kind)
            report['excluded_layers'][kind]=deferred
        _,clips,_=inventory(world,'ClipVolume')
        selected['ClipVolume']=clips
        selected['AreaBox']=[]
        archive_names={n.lower():n for n in world.namelist()}
        report['clip_candidates']=associate_probe_clips(
            selected['EnvironmentLight'], clips, entities,
            lambda path: read(world, archive_names[path]), old_defaults['EnvironmentLight'])
        included={e.get('EntityId') for group in selected.values() for e in group}
        pending=[e for group in selected.values() for e in group]
        while pending:
            e=pending.pop()
            for link in e.findall('EntityLinks/Link'):
                target_id=link.get('TargetId')
                if target_id not in entities or entities[target_id].get('EntityClass') not in selected:
                    raise ValueError('Unresolved lighting clip link: '+str(link.attrib))
                if target_id not in included:
                    dependency=entities[target_id];selected[dependency.get('EntityClass')].append(dependency)
                    included.add(target_id);pending.append(dependency)
        # Replace the temporary static Trosky world probe. Preserve UI lighting.
        for parent in root.iter():
            for e in list(parent):
                if e.get('EntityClass')=='EnvironmentLight' and e.get('Name')=='level_global_probe':parent.remove(e)
        next_id=max((int(e.get('EntityId','0')) for e in root.iter('Entity')),default=0)+1
        mapped={};resources={};archive_names={n.lower():n for n in world.namelist()}
        # Conversions are content-addressed and verified, so setup can keep
        # them in its workspace (GLUEMAPPER_LIGHTING_CACHE) across builds.
        cubes=Cubemaps(source,Path(os.environ.get('GLUEMAPPER_LIGHTING_CACHE') or work/'cubemaps'))
        # Each job runs texconv (BC6H) and Pillow. Measured on 1,033 Rataje
        # probes (12-core CPU): 4 threads ~515 s, 16 threads ~355 s, identical output.
        default=str(min(16, os.cpu_count() or 4))
        workers=ThreadPoolExecutor(max_workers=int(os.environ.get('GLUEMAPPER_LIGHTING_WORKERS', default)))
        pending_cubes=[]
        try:
            dependencies={}
            for light in selected['ReflexLight']:
                for node,key in (('Properties/Options','file_deferred_clip_geom'),('Properties/Projector','texture_Texture')):
                    path=light.find(node).get(key,'')
                    if path:dependencies[path.replace('\\','/').lower()]=None
            for archive_path in sorted((Path(source)/'Data').glob('*.pak')):
                if not dependencies or all(v is not None for v in dependencies.values()):break
                if not archive_path.name.startswith(('Objects','Buildings','Textures')):continue
                with zipfile.ZipFile(archive_path) as archive:
                    for path in archive.namelist():
                        key=path.replace('\\','/').lower()
                        if key in dependencies:dependencies[key]=read(archive,path)
            for name,blob in dependencies.items():
                if blob is None:raise FileNotFoundError('Lighting dependency: '+name)
                if not name.endswith('.cgf') or blob[:8]!=b'CrChF\x07\0\0':
                    raise ValueError('Unsupported lighting projection dependency: '+name)
                path=work/('dependency_'+str(len(cubes.assets))+'.cgf');path.write_bytes(blob)
                cubes.assets['objects/gluemapper/lighting/'+name]=path
            for kind,group in selected.items():
                count=0
                for original in sorted(group,key=lambda e:int(e.get('EntityId'))):
                    pos,rot,scale=world_transform(original,entities)
                    if pos[0]<0:continue  # KCD1 inventory/showroom, not world lighting.
                    identity=original.get('EntityId');name=PREFIX+identity
                    e=ET.Element('Entity',Name=name,EntityClass=kind,EntityId=str(next_id),
                        EntityGuid=guid('lighting/'+identity),Pos=vector(pos),Rotate=vector(rot),Scale=vector(scale),CastShadowMinSpec='1')
                    next_id+=1
                    record={'source_id':identity,'source_name':original.get('Name'),'class':kind,'name':name,'position':pos}
                    if kind=='AreaBox':
                        e.append(copy.deepcopy(original.find('Area')))
                    elif kind=='ClipVolume':
                        clip=copy.deepcopy(original.find('ClipVolume'))
                        path=clip.get('GeometryFileName').lower().replace('%level%/','')
                        if path not in archive_names:
                            if any(l.get('TargetId')==identity for group in selected.values() for owner in group for l in owner.findall('EntityLinks/Link')):
                                raise FileNotFoundError('Referenced lighting clip geometry: '+path)
                            report['missing_clip_geometry'].append(dict(record,path=path))
                            continue
                        blob=read(world,archive_names[path])
                        if blob[:8]!=b'CrChF\x07\0\0':raise ValueError('Unexpected clip-volume geometry format')
                        path='brush/gm_lighting_'+identity+'.cgf'
                        resources[path]=blob;clip.set('GeometryFileName','%level%/'+path);e.append(clip)
                    else:
                        authored=copy.deepcopy(old_defaults[kind]);overlay(authored,original.find('Properties'))
                        props=copy.deepcopy(schemas[kind]);overlay(props,authored,True)
                        props.set('bSaved_by_game','0')
                        minimum,maximum=bounds(props,rot,scale,kind)
                        global_probe=original.get('Name')=='level_global_probe'
                        e.set('PerInstanceStreamable','0' if global_probe else '1')
                        if not global_probe:e.attrib.update(LocalBoundsMin=minimum,LocalBoundsMax=maximum)
                        if kind=='ReflexLight':
                            reflex_times(props)
                            shadows=props.find('Shadows')
                            shadows.set('nCastShadows',shadow_spec(shadows.get('nCastShadows')))
                            # Source has no authored thick-wall/camera-height model.
                            # Do not let KCD2 defaults clip otherwise valid old lights.
                            props.find('Shape').set('fWallThickness','0')
                            props.find('Options').set('fVerticalClipDistanceDownward','0')
                            props.find('Options').set('fVerticalClipDistanceUpward','0')
                            projector=props.find('Projector')
                            for node,key in ((projector,'texture_Texture'),(props.find('Options'),'file_deferred_clip_geom')):
                                path=node.get(key)
                                if path:node.set(key,'objects/gluemapper/lighting/'+path.replace('\\','/').lower())
                        else:
                            record['area_conversion'] = native_probe_areas(
                                props, schemas[kind], bool(original.findall('EntityLinks/Link')))
                            advanced=props.find('OptionsAdvanced');dynamic=advanced.get('bDynamic')=='1'
                            explicit=advanced.get('texture_deferred_cubemap') or props.find('Options').get('_texture_deferred_cubemap')
                            if dynamic:
                                folder='textures/cubemaps/rataje/'+original.get('Name').lower()
                                spec=[folder+'/'+stamp(t)+'_cm.dds' for t in SOURCE_TIMES]
                            else:
                                spec=[str(Path(explicit.replace('\\','/')).with_suffix('.dds')).replace('\\','/').lower()]
                            diffuse=[p.removesuffix('_cm.dds')+'_cm_diff.dds' for p in spec]
                            missing=[p for p in spec+diffuse if p not in cubes.index]
                            if missing:
                                # Never substitute another room's lighting or leave a
                                # live entity requesting nonexistent baked textures.
                                props.set('bActive','0');advanced.set('bDynamic','0')
                                advanced.set('texture_deferred_cubemap','')
                                record['disabled_missing_bake']=missing
                                report['unavailable_cubemaps'].append(record.copy())
                            else:
                                destination='textures/cubemaps/'+level+'/'+name
                                pending_cubes.append((spec,destination,dynamic))
                                pending_cubes.append((diffuse,destination,dynamic))
                                out=[destination+'/'+stamp(t)+'_cm.dds' for t in (TARGET_TIMES if dynamic else (0,))]
                                advanced.set('texture_deferred_cubemap','' if dynamic else out[0])
                                props.find('Options').set('_texture_deferred_cubemap','')
                                record['cubemaps']=out
                            if count%25==0:print(f'Lighting: converted {count}/{len(group)} probes',flush=True)
                        e.append(props)
                    mapped[identity]=e;report['records'].append(record);count+=1
                report['converted'][kind]=count
            for kind,group in selected.items():
                for original in group:
                    e=mapped.get(original.get('EntityId'))
                    if e is None:continue
                    links=original.find('EntityLinks')
                    if links is not None:
                        container=ET.SubElement(e,'EntityLinks')
                        for link in links:
                            target_entity=mapped[link.get('TargetId')]
                            ET.SubElement(container,'Link',Name=link.get('Name',''),TargetId=target_entity.get('EntityId'),TargetGuid=target_entity.get('EntityGuid'))
            root.extend(mapped.values())
            result=dict(files);result.update(resources);result['objects_mission0.xml']=xml(root)
            result['mission_mission0.xml']=probe_lighting_environment(files['mission_mission0.xml'])
            report['indirect_lighting']='Native baked probes; voxel GI disabled for generated world without an SVO bake'
            pending_cubes=[workers.submit(cubes.convert,*args) for args in pending_cubes]
            for completed,future in enumerate(as_completed(pending_cubes),1):
                future.result()
                if completed%50==0 or completed==len(pending_cubes):
                    print(f'Lighting textures: {completed}/{len(pending_cubes)} schedules ready',flush=True)
            report['texture_entries']=len(cubes.assets)
            report['unique_texture_schedules']=len(cubes.cache)
            return result,dict(cubes.assets),report
        finally:
            workers.shutdown(wait=True,cancel_futures=True)
            cubes.close()
