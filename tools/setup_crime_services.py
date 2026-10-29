"""Shared fresh-build/resume integration of imported-world native crime services."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as E
import zipfile

from upgrade_map import read

VERSION=8
RECORD='crime-services.json'
TRAVEL='Data/gluemappertravel.pak'


def sha(blob): return hashlib.sha256(blob).hexdigest()


def validate(package, report):
    from campaign_entity_links import guid_value
    from campaign_trigger_areas import read_areas
    package=Path(package)
    if report.get('version') not in range(1, VERSION + 1): raise ValueError('Unsupported crime service receipt')
    with zipfile.ZipFile(package/report['level_pak']) as z:
        mission=E.fromstring(read(z,'objects_mission0.xml'))
        byguid={e.get('EntityGuid'):e for e in mission if e.get('EntityGuid')}
        ids={e.get('EntityId'):e.get('EntityGuid') for e in mission if e.get('EntityId')}
        for g,expected in report['owned_entities'].items():
            e=byguid.get(g)
            if e is None or sha(E.tostring(e))!=expected: raise ValueError('Crime service entity changed '+g)
        waiting=E.fromstring(read(z,'waitinglinks.xml'))
        network={(l.get('SourceId'),l.get('TargetId'),d.text) for l in waiting.iter('WaitingLink') for d in l.findall('LinkDefinition')}
        for a,b,label in report['service_links']:
            matches=[l for l in byguid[a].findall('EntityLinks/Link') if l.get('TargetGuid')==b and l.get('Name')==label]
            if len(matches)!=1 or ids.get(matches[0].get('TargetId'))!=b or (a,b,label) not in network:
                raise ValueError('Missing/dangling crime service link '+label)
        profiles=E.fromstring(read(z,'whdata_1'))
        scene_entities=dict(byguid)
        for p in report['scene_profiles']:
            if len([x for x in profiles.findall('.//GameProfile') if x.findtext('Name')==p['profile']
                    and x.findtext('GameLayers/GameLayer')==p['layer']])!=1: raise ValueError('Missing crime scene profile')
            blob=read(z,'layers/'+p['layer']+'.xml')
            if sha(blob)!=report['scene_files'][p['layer']]: raise ValueError('Crime scene changed')
            for e in E.fromstring(blob):
                if e.get('EntityGuid') in scene_entities: raise ValueError('Duplicate scene identity')
                scene_entities[e.get('EntityGuid')]=e
        from cinematic_scene import guid_key
        guids={guid_key(g) for g in scene_entities};allids={e.get('EntityId') for e in scene_entities.values()}
        for receipt in report['scenes']:
            for old,g in receipt['entity_guid_map'].items():
                if guid_key(g) not in guids: raise ValueError('Unresolved cinematic binding')
        if report['version'] >= 3:
            registrations={s.findtext('SharedSoulGuid') for s in E.fromstring(read(z,'whdata_0')).findall('SoulList/Souls/Soul')}
            for receipt in report['scenes']:
                cast=[a for a in report['cast']['assignments'] if a['scene']==receipt['scene']]
                if not cast or any(a['soul'] not in registrations for a in cast):
                    raise ValueError('Cinematic cast is not registered in the destination')
                scene=E.fromstring(read(z,'layers/'+receipt['layer']+'.xml'))
                bodies={e.get('guidBodyPresetId') for e in scene.findall('Entity/Properties/Body')}
                if any(a['soul'] not in bodies or a['source_soul'] in bodies for a in cast):
                    raise ValueError('Cinematic cast does not match local assignment receipt')
                if report['version'] >= 4:
                    for variant in report['cast']['preserved_variants']:
                        if variant['scene'] != receipt['scene']: continue
                        entity=scene.find("Entity[@Name='"+variant['entity']+"']")
                        if entity is None or entity.find('Properties/Body').get('guidBodyPresetId') != variant['soul']:
                            raise ValueError('Authored cinematic body variant was recast')
                stages={e.get('EntityGuid') for e in mission if e.get('Name')=='gm_crime_native_stage'}
                for node in scene.findall('Entity/Sequence/Nodes/Node'):
                    if node.get('EntityGUID') in stages and node.findall('Track'):
                        raise ValueError('Cinematic can still hide or move the permanent stand')
        if sha(read(z,'terrain/hlods.dat'))!=report['hlods_sha256']: raise ValueError('Stocks replacement geometry changed')
        # Both handler profile names and native sequence names must resolve.
        for g in report['entities']:
            e=byguid[g]
            if e.get('EntityClass')=='IngameCutsceneData':
                props=e.find('Properties')
                p=next((p for p in report['scene_profiles'] if p['profile']==props.get('esGameProfile')),None)
                if p is None: raise ValueError('Unregistered punishment profile binding')
                scenes=E.fromstring(read(z,'layers/'+p['layer']+'.xml'))
                if len(scenes.findall("Entity/Sequence[@Name='"+props.get('esSequenceName')+"']"))!=1:
                    raise ValueError('Missing punishment sequence binding')
    with zipfile.ZipFile(package/TRAVEL) as z:
        for path,expected in report['resources'].items():
            if sha(read(z,path))!=expected: raise ValueError('Crime graph resource changed '+path)
        if report['version'] >= 2:
            from world_crime_graphs import LINK_TYPES, location_link_type
            blob=read(z,LINK_TYPES)
            if location_link_type(blob)!=blob: raise ValueError('Missing punishment location link declaration')
        if report['version'] >= 7:
            from world_crime_factions import BASE, LEGACY_LABEL, authority_selectors
            blob=read(z,BASE)
            witness=report['witness_reporting']
            if authority_selectors(blob,witness['factions'])!=blob: raise ValueError('Missing imported authority selectors')
            if not witness['factions'] or not witness['settlement_information_broadcast']:
                raise ValueError('Missing settlement witness reporting')
            seen=set()
            for path in report['resources']:
                if '/factiontree' not in path.lower(): continue
                for faction in E.fromstring(read(z,path)).iter('Faction'):
                    name=faction.get('Name')
                    if name not in witness['factions']: continue
                    labels=faction.get('Labels','').split()
                    if ('settlement' not in labels or LEGACY_LABEL in labels
                            or faction.get('LocationId')!=witness['factions'][name]):
                        raise ValueError('Missing settlement faction contract '+name)
                    seen.add(name)
            if seen!=witness['factions'].keys(): raise ValueError('Missing settlement faction resource')
    return report


def cast_resources(package, source, target):
    from campaign_sources import RetailSourceReader
    from character_person import native_xml
    from upgrade_map import xml
    with zipfile.ZipFile(Path(package)/TRAVEL) as z:
        local={n:read(z,n) for n in z.namelist() if n.lower().startswith(('libs/tables/', 'libs/storm/'))
               and n.lower().endswith('.xml')}
    with RetailSourceReader(source) as reader:
        local['source/faction.xml']=reader(source,{'faction':('Tables.pak','Libs/Tables/rpg/faction.xml')})['faction']['data']
    with zipfile.ZipFile(Path(target)/'Data/Tables.pak') as z:
        native={n:xml(native_xml(read(z,n))) for n in z.namelist()
                if n.lower().startswith(('libs/tables/rpg/soul', 'libs/tables/rpg/social_class'))
                and n.lower().endswith('.xml')}
    return local,native


def refresh_scene_cast(package, source, target, report):
    """Rebuild owned scene layers only; retain level geometry, IDs and services."""
    from cinematic_scene import SceneIndex,convert,guid_key
    from cinematic_scene_cast import bind_cast
    from region_travel import guid
    local,native=cast_resources(package,source,target)
    from cinematic_scene_ground import combine
    from compiled_terrain import parse
    from world_surface_coverage import HeightField
    with zipfile.ZipFile(Path(package)/Path(report['level_pak']).parent/'terrain.pak') as z:
        destination_height=HeightField(parse(read(z,'terrain/terrain.dat')))
    with zipfile.ZipFile(Path(target)/'Data/Levels/trosecko/terrain.pak') as z:
        native_height=HeightField(parse(read(z,'terrain/terrain.dat')))
    with zipfile.ZipFile(Path(package)/report['level_pak']) as z:
        files={n:read(z,n) for n in ('objects_mission0.xml','triggerareas.fubar','whdata_0')}
        scenes={p['layer']:E.fromstring(read(z,'layers/'+p['layer']+'.xml')) for p in report['scene_profiles']}
    byguid={e.get('EntityGuid'):e for e in E.fromstring(files['objects_mission0.xml'])}
    with zipfile.ZipFile(Path(target)/'Data/Levels/trosecko/level.pak') as z:
        index=SceneIndex.from_archive(z)
        plans=[index.plan(r['scene']) for r in report['scenes']]
        adapters,cast=bind_cast(index,plans,files,local,native,report['selection'])
        adapters,ground=combine(index,plans,adapters,native,report['native_translation'],
                               native_height.height,destination_height.height)
        changed={};receipts=[]
        for old,plan in zip(report['scenes'],plans):
            bindings={key:byguid[g] for key,g in old['entity_guid_map'].items() if g in byguid}
            static={key for key,e in bindings.items() if e.get('Name')=='gm_crime_native_stage'}
            first=min(int(e.get('EntityId')) for e in scenes[old['layer']])
            data,receipt=convert(index,plan,offset=old['offset'],identity=guid,first_id=first,
                layer=old['layer'],source_game='kcd2',bindings=bindings,static_bindings=static,entity_adapter=adapters[plan['name']])
            if receipt['entity_guid_map']!=old['entity_guid_map'] or receipt['entity_id_map']!=old['entity_id_map']:
                raise ValueError('Cinematic cast upgrade changed world identity')
            changed['layers/'+old['layer']+'.xml']=data
            report['scene_files'][old['layer']]=sha(data);receipts.append(receipt)
    report['scenes']=receipts;report['cast']=cast;report['ground_fit']=ground
    return changed


def location_types(package, target):
    """Merge with the package's effective tag registry, never discard other imports."""
    from world_crime_graphs import LINK_TYPES, location_link_type
    candidates=[]
    for path in sorted((Path(package)/'Data').glob('*.pak')):
        with zipfile.ZipFile(path) as z:
            names=[n for n in z.namelist() if n.lower()==LINK_TYPES.lower()]
            if names: candidates.append(read(z,names[0]))
    if len({sha(b) for b in candidates})>1:
        raise ValueError('Conflicting packaged AI link registries')
    if candidates: blob=candidates[0]
    else:
        if target is None: raise ValueError('Native game required for crime link registry')
        with zipfile.ZipFile(Path(target)/'Data/Scripts.pak') as z: blob=read(z,LINK_TYPES)
    return LINK_TYPES,location_link_type(blob)


def upgrade(package, source, target, notify=lambda *a:None):
    from setup_campaign import RECEIPT,digest
    from setup_package_upgrade import rewrite
    from compiled_terrain import parse
    from audit_world_scenes import stocks
    from world_crime_services import choose_site, prepare
    from world_crime_graphs import prepare as graphs_prepare
    from world_gameplay_basis import validate_dependency_scopes
    from crime_site_geometry import geometry_signature, remove_stocks
    from build_open_test_gates import source_geometry
    from mesh_lod_metadata import normalize
    from setup_static_gate_repair import replacements
    from lod_ratio_metadata import MARKER
    from world_surface_coverage import HeightField
    package=Path(package)
    if (package/RECORD).exists():
        report=json.loads((package/RECORD).read_text(encoding='utf8'))
        validate(package,report)
        if report['version']==VERSION:return {},report
        if source is None or target is None: raise ValueError('Both games are required to upgrade local crime services')
        notify('Connect local crime and punishments',0,2,'Connect settlement witnesses and native authorities')
        scene_files=refresh_scene_cast(package,source,target,report) if report['version']<5 else {}
        name,blob=location_types(package,target)
        from world_crime_factions import package_resources
        resources,witness=package_resources(package,source,target,report['level_pak'])
        resources[name]=blob
        if scene_files: rewrite(package,report['level_pak'],scene_files)
        rewrite(package,TRAVEL,resources)
        report['resources'].update({p:sha(b) for p,b in resources.items()})
        report['witness_reporting']=witness
        report['resources'][name]=sha(blob);report['version']=VERSION
        report['graphs']['local_location_link_type']='string'
        validate(package,report)
        pending=package/(RECORD+'.pending');pending.write_text(json.dumps(report,indent=2),encoding='utf8');pending.replace(package/RECORD)
        notify('Connect local crime and punishments',2,2,'Settlement reporting and authority contracts validated; live testing still required')
        changed=[TRAVEL,RECORD]+([report['level_pak']] if scene_files else [])
        return {p:digest(package/p) for p in changed},report
    if source is None or target is None: raise ValueError('Both games are required for local crime integration')
    source,target=map(Path,(source,target));receipt=json.loads((package/RECEIPT).read_text(encoding='utf8'))
    level=receipt['travel_bridge']['destination'];source_level=receipt['campaign_level'].removeprefix('kcd1_')
    if not all(n.replace('_','').isalnum() for n in (level,source_level)):raise ValueError('Invalid crime level')
    lp='Data/Levels/'+level+'/level.pak'
    notify('Connect local crime and punishments',0,5,'Read imported districts and authored stocks')
    with zipfile.ZipFile(package/lp) as z:
        required=('objects_mission0.xml','waitinglinks.xml','triggerareas.fubar','whdata_0','leveldata.xml','terrain/hlods.dat','terrain/hlods.xml')
        files={n:read(z,n) for n in required}
        for n in ('whdata_1',MARKER):
            if n in z.namelist():files[n]=read(z,n)
    with zipfile.ZipFile(package/'Data/Levels'/level/'terrain.pak') as z: terrain_blob=read(z,'terrain/terrain.dat')
    terrain=parse(terrain_blob)
    entry='Quests/GlueTravel/entry/'+level+'.xml';wrapper=entry.rsplit('/',1)[0]+'/world/open_world.xml'
    with zipfile.ZipFile(package/TRAVEL) as z:
        resources={n:read(z,n) for n in z.namelist() if '/soul__' in n.lower() or '/location__' in n.lower()
                   or n in (entry,wrapper)}
    with zipfile.ZipFile(target/'Data/Tables.pak') as z: resources['Libs/Tables/rpg/social_class.xml']=read(z,'Libs/Tables/rpg/social_class.xml')
    from campaign_sources import RetailSourceReader
    with RetailSourceReader(source) as reader:
        source_factions=reader(source,{'faction':('Tables.pak','Libs/Tables/rpg/faction.xml')})
        resources['source/faction.xml']=source_factions['faction']['data']
    with zipfile.ZipFile(source/'Data/Levels'/source_level/'level.pak') as z: selection=choose_site(files,stocks(z),resources)
    originals=source_geometry(source,{p['path'] for p in selection['replaced']})
    signatures={p:{geometry_signature(b),geometry_signature(normalize(b))} for p,b in originals.items()}
    notify('Connect local crime and punishments',1,5,'Replace verified source stocks and preserve streaming')
    with ExitStack() as stack:
        assets={}
        for path in sorted((package/'Data').glob('*.pak')):
            z=stack.enter_context(zipfile.ZipFile(path))
            for n in z.namelist():
                if n.lower().endswith('.cgf'):assets.setdefault(n.lower(),[]).append((z,n))
        cache={}
        def matches(converted,original):
            if converted not in cache:
                candidates=assets.get(converted.lower(),[])
                if len(candidates)!=1:raise ValueError('Missing/ambiguous converted stocks asset '+converted)
                cache[converted]=geometry_signature(read(*candidates[0]))
            return cache[converted] in signatures[original]
        updated,hierarchy,removed=remove_stocks(files['terrain/hlods.dat'],files['terrain/hlods.xml'],
            selection['replaced'],terrain.tables['meshes']['paths'],matches)
    files.update(replacements(terrain_blob,files['terrain/hlods.dat'],updated,hierarchy,
                             json.loads(files[MARKER]) if MARKER in files else None))
    notify('Connect local crime and punishments',2,5,'Register relocated native scenes and local services')
    local_cast,native_cast=cast_resources(package,source,target)
    with zipfile.ZipFile(target/'Data/Levels/trosecko/terrain.pak') as z:
        native_height=HeightField(parse(read(z,'terrain/terrain.dat')))
    with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as z:
        files,report=prepare(files,z,selection,HeightField(terrain).height,local_cast,native_cast,
                             native_height=native_height.height)
    notify('Connect local crime and punishments',3,5,'Bind native controller, confiscation and local reputation')
    with zipfile.ZipFile(target/'Data/Scripts.pak') as z:
        graphs,graph_report=graphs_prepare(resources[entry],resources[wrapper],entry,wrapper,z,selection['faction'])
    name,blob=location_types(package,target);graphs[name]=blob
    from world_crime_factions import package_resources
    witness_resources,witness=package_resources(package,source,target,lp)
    graphs.update(witness_resources)
    graph_report['local_location_link_type']='string'
    with zipfile.ZipFile(target/'Data/IPL_GameData.pak') as z:
        graph_report['validated_graphs']=validate_dependency_scopes({p:b for p,b in graphs.items() if p.startswith('Quests/')},
            wrapper,read(z,'Libs/concept/definitions.xml'))
    report.update(version=VERSION,status='bound_native_services',level_pak=lp,source_level=source_level,
        graphs=graph_report,witness_reporting=witness,removed_stocks=removed,hlods_sha256=sha(updated),
        resources={p:sha(b) for p,b in graphs.items()},
        scene_files={p['layer']:sha(files['layers/'+p['layer']+'.xml']) for p in report['scene_profiles']})
    mission=E.fromstring(files['objects_mission0.xml'])
    report['owned_entities']={e.get('EntityGuid'):sha(E.tostring(e)) for e in mission if e.get('EntityGuid') in report['entities']}
    notify('Connect local crime and punishments',4,5,'Write and validate private package')
    rewrite(package,lp,files);rewrite(package,TRAVEL,graphs)
    validate(package,report)
    pending=package/(RECORD+'.pending');pending.write_text(json.dumps(report,indent=2),encoding='utf8');pending.replace(package/RECORD)
    notify('Connect local crime and punishments',5,5,'Native service bindings validated; playback requires in-game testing')
    return {n:digest(package/n) for n in (lp,TRAVEL,RECORD)},report
