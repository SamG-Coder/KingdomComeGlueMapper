"""Build an isolated native cart-travel overlay from installed retail content.

Uses native coachman dialogue -> Skald SwitchLevel -> LevelSwitch records.
No New Game, player recreation, inventory reconstruction or console map command.
The converted world's asset shards must remain installed separately.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import uuid
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import native_guid, append_links
from campaign_package import level_registration, level_table_shells
from upgrade_map import read, xml
from region_travel_dialogue import patch as patch_driver_dialogue, merge_localization, HOST, DIALOGUE
from region_travel_ground import Heightfield, wheel_bottom, fit_cart
from region_travel_road import road_spawn_points
import region_travel_driver as driver
from region_travel_ui import import_showroom
from region_travel_companions import import_horse_scheduler, import_horse_scheduler_table, horse_recovery_resources
from region_travel_navigation import package_navigation
from character_person_world import register_person_world
from region_travel_entry import register_entry
from region_travel_map import resources as map_resources
from region_travel_locations import resources as location_resources, merge_missing_strings
from region_travel_services import resolve_merchant, shop_resources
from character_person_appearance import build_appearance
from region_travel_merchant import actor_resources, register_world
from region_travel_visit import attach as attach_visit
from region_travel_tables import managed_patches
import region_travel_theresa as theresa

MOD = 'gluemappertravel'
LEVEL = 'kcd1_travel'
LEVEL_ID = 1001
PREFAB = 'Prefabs/manmade/vehicles/wagon_b_covered_empty.xml'


def guid(name):
    return native_guid(int.from_bytes(uuid.uuid5(uuid.NAMESPACE_URL, MOD + '/' + name).bytes[:8], 'little'))


def one(items, description):
    items = list(items)
    if len(items) != 1:
        raise ValueError(f'Expected one {description}, found {len(items)}')
    return items[0]


def departure(source):
    """Resolve the epilogue camp through the original authored quest link."""
    with zipfile.ZipFile(Path(source) / 'Data/Levels/rataje/level.pak') as z:
        payload = read(z, 'objects_mission0.xml')
    entities = list(ET.fromstring(payload).iter('Entity'))
    quest = one((e for e in entities if e.get('Name') == 'q_epilogue'), 'epilogue controller')
    link = one((e for e in quest.findall('EntityLinks/Link')
                if e.get('Name') == 'QuestPlace[horseParkPtacek]'), 'departure horse marker link')
    marker = one((e for e in entities if e.get('EntityId') == link.get('TargetId')), 'departure marker')
    return copy.deepcopy(marker), {'archive': 'Data/Levels/rataje/level.pak',
        'entry': 'objects_mission0.xml', 'sha256': hashlib.sha256(payload).hexdigest(),
        'quest': quest.get('Name'), 'link': link.get('Name'), 'marker': dict(marker.attrib)}


def native_arrival(archive, name):
    matches = []
    for entry in archive.namelist():
        if entry.lower().endswith('.xml') and (entry.lower().startswith('layers/') or entry.lower() == 'objects_mission0.xml'):
            payload = read(archive, entry)
            if name.encode() not in payload:
                continue
            for entity in ET.fromstring(payload).iter('Entity'):
                if entity.get('Name') == name:
                    matches.append((copy.deepcopy(entity), entry))
    return one(matches, 'native arrival marker ' + name)


def switch_table(native):
    root = ET.fromstring(native)
    rows = root.find('LevelSwitches')
    if rows is None or rows.get('version') != '1':
        raise ValueError('Unsupported native LevelSwitch schema')
    output = ET.Element(root.tag, root.attrib)
    result = ET.SubElement(output, rows.tag, rows.attrib)
    for route, target, marker in [('to_kcd1', LEVEL_ID, 'gmtravel_arrival_kcd1'),
                                  ('to_trosky', 2, 'gmtravel_arrival_trosky')]:
        name = MOD + '_' + route
        if any(r.get('Name') == name for r in rows):
            raise ValueError('LevelSwitch name collision')
        ET.SubElement(result, 'LevelSwitchData', Name=name, TargetLevelId=str(target),
                      TargetLocationEntity=marker)
    return xml(output)


def travel_graph(project, route):
    root = ET.Element('Database', Name='brambora')
    host = ET.SubElement(ET.SubElement(root, 'Skald'), 'Project', Name=project)
    nodes = ET.SubElement(host, 'Nodes')
    ET.SubElement(nodes, 'Function', Name='prepare_travel', MethodName='wh::conceptmodule::PassLongTime', DeclaringType='wh::conceptmodule')
    switch = ET.SubElement(nodes, 'Function', Name='travel', MethodName='wh::game::SwitchLevel', DeclaringType='wh::game')
    ET.SubElement(switch, 'Constant', Name='LevelSwitching', Value=MOD + '_' + route)
    ET.SubElement(switch, 'Edge', From='prepare_travel.OnExec', To='Exec')
    return xml(root)


def qmul(a, b):
    w,x,y,z=a;v,i,j,k=b
    return (w*v-x*i-y*j-z*k,w*i+x*v+y*k-z*j,w*j-x*k+y*v+z*i,w*k+x*j-y*i+z*v)


def rotate(q, v):
    return qmul(qmul(q, (0,*v)), (q[0],-q[1],-q[2],-q[3]))[1:]


def prefab_geometry(prefab):
    """Flatten the native cart hierarchy with its authored wheel transforms."""
    objects = {e.get('Id'):e for e in ET.fromstring(prefab).findall('./Objects/Object')}
    def transform(key, seen=()):
        if key in seen or key not in objects:
            raise ValueError('Invalid cart hierarchy')
        e=objects[key]
        p=tuple(map(float,e.get('Pos','0,0,0').split(',')))
        q=tuple(map(float,e.get('Rotate','1,0,0,0').split(',')))
        if e.get('Parent'):
            pp,pq=transform(e.get('Parent'),seen+(key,))
            p=tuple(a+b for a,b in zip(pp,rotate(pq,p)));q=qmul(pq,q)
        return p,q
    return [(e.get('Name'),e.get('Geometry').replace('\\','/'),*transform(k))
            for k,e in objects.items() if e.get('EntityClass')=='GeomEntity' and e.get('Geometry')]


def station(mission, marker, side, prefab, ground=None, wheel_bounds=None, placement=None):
    root = ET.fromstring(mission)
    project = 'GlueTravel_' + side
    route = 'to_kcd1' if side=='trosky' else 'to_trosky'
    existing = list(root.iter('Entity'))
    if any(e.get('Name','').startswith('gmtravel_') for e in existing):
        raise ValueError('Travel station already present')
    counter=max(1000+len(existing),max((int(e.get('EntityId','0')) for e in existing),default=0)+1)
    origin=tuple(map(float,marker.get('Pos').split(',')))
    rawq=tuple(map(float,marker.get('Rotate','1,0,0,0').split(',')))
    yaw=math.atan2(2*(rawq[0]*rawq[3]+rawq[1]*rawq[2]),1-2*(rawq[2]**2+rawq[3]**2))
    orientation=(math.cos(yaw/2),0,0,math.sin(yaw/2))
    geometry=prefab_geometry(prefab)
    cart_origin=tuple(a+b for a,b in zip(origin,rotate(orientation,(5,0,0))))
    cart_q=orientation
    if ground is not None:
        contacts=[]
        for name,model,p,q in geometry:
            if name.startswith('wheel_'):
                contacts.append(tuple(a+b for a,b in zip(p,rotate(q,(0,0,wheel_bounds[model.lower()])))))
        cart_origin,cart_q,errors=fit_cart(cart_origin,yaw,contacts,ground,qmul,rotate)
        if placement is not None:placement.update(cart_origin=cart_origin,cart_rotation=cart_q,wheel_height_errors=errors)
    def add(name,cls,local=(0,0,0),rotation=(1,0,0,0),**attrs):
        nonlocal counter
        pos=tuple(a+b for a,b in zip(origin,rotate(orientation,local)))
        e=ET.SubElement(root,'Entity',Name=name,EntityClass=cls,EntityId=str(counter),EntityGuid=guid(name),
            Pos=','.join(map(str,pos)),Rotate=','.join(map(str,qmul(orientation,rotation))),**attrs)
        counter+=1
        return e
    arrival=add('gmtravel_arrival_'+side,'TagPoint')
    if ground is not None:
        arrival.set('Pos',f'{origin[0]},{origin[1]},{ground(*origin[:2])+.05}')
    ET.SubElement(arrival,'Properties',bSaved_by_game='0')
    # Cart sits beside the arrival, keeping the player's landing clear.
    for name,model,p,q in geometry:
        e=add('gmtravel_'+side+'_'+name,'GeomEntity',(p[0]+5,p[1],p[2]),q,
              Geometry=model,CastShadowMinSpec='1',ViewDistRatio='150')
        ET.SubElement(e,'Properties',bSaved_by_game='0',bInteractiveCollisionClass='1')
        if ground is not None:
            e.set('Pos',','.join(map(str,(a+b for a,b in zip(cart_origin,rotate(cart_q,p))))))
            e.set('Rotate',','.join(map(str,qmul(cart_q,q))))
    # Travel belongs to the coachman's dialogue. A second E interaction beside
    # the cart steals the mount/use action when the player's horse is nearby.
    if side=='kcd1':
        npc=add(driver.NAME,'NPC',(2.5,-1,0),CastShadowMinSpec='1')
        if ground is not None:
            p=list(map(float,npc.get('Pos').split(',')));p[2]=ground(*p[:2])+.02
            npc.set('Pos',','.join(map(str,p)))
        home=add('gmtravel_return_driver_home','SchedulerHub',(2.5,-1,0))
        home.set('Pos',npc.get('Pos'))
        for name in ('_!home','owner'):
            links=npc.find('EntityLinks')
            if links is None:links=ET.SubElement(npc,'EntityLinks')
            ET.SubElement(links,'Link',Name=name,TargetId=home.get('EntityId'),TargetGuid=home.get('EntityGuid'))
    concept=add(project+'_concept','Concept')
    graph='Quests/GlueTravel/'+project+'.xml'
    ET.SubElement(concept,'Properties',fileConcept=graph,bIncludeModConcepts='0')
    blob=travel_graph(project,route)
    if side=='kcd1':blob=driver.attach_dialogue(blob)
    return xml(root),graph,blob,[]


def preprocess(payload, graph, level):
    root=ET.fromstring(payload) if payload else ET.Element('Root',version='1')
    manager=root.find('ConceptManager')
    if manager is None:manager=ET.SubElement(root,'ConceptManager')
    paths=manager.find('ConceptPaths')
    if paths is None:paths=ET.SubElement(manager,'ConceptPaths')
    ET.SubElement(paths,'Path').text=graph.lower()
    if manager.find('ConceptModules') is None:ET.SubElement(manager,'ConceptModules')
    ai=root.find('AI')
    if ai is None:ai=ET.SubElement(root,'AI')
    path=ai.find('LevelPath')
    if path is None:path=ET.SubElement(ai,'LevelPath')
    path.text='data/levels/'+level
    return xml(root)


def build(source, target, world, output):
    source,target,world,output=map(Path,(source,target,world,output))
    if output.exists():raise FileExistsError('Choose a fresh output directory')
    # First version deliberately requires a static converted world. Never silently
    # remove a campaign's graph/actors or run its new-game initialization on arrival.
    with zipfile.ZipFile(world/'level.pak') as z:
        entities=ET.fromstring(read(z,'objects_mission0.xml'))
        if any(e.get('EntityClass') in ('Concept','QuestObject') for e in entities.iter('Entity')):
            raise ValueError('Use a static converted world, without campaign initialization')
    departure_marker,evidence=departure(source)
    with zipfile.ZipFile(target/'Data/Tables.pak') as z:table=switch_table(read(z,'Libs/Tables/LevelSwitch.xml'))
    with zipfile.ZipFile(target/'Data/IPL_GameData.pak') as z:
        prefab=read(z,PREFAB);storm=read(z,'Libs/Storm/roles/world/levelSwitch.xml')
    with zipfile.ZipFile(target/'Data/Scripts.pak') as z:
        dialogue_files,dialogue_strings=patch_driver_dialogue(read(z,DIALOGUE),read(z,HOST))
        original_dialogue=read(z,DIALOGUE)
        native_player=read(z,'Scripts/Entities/actor/player.lua')
    with zipfile.ZipFile(target/'Data/Tables.pak') as z:
        driver_files,driver_strings=driver.resources(read(z,'Libs/Tables/rpg/soul__tsla.xml'),storm,original_dialogue,read(z,'Libs/Tables/rpg/role.xml'))
    dialogue_files.update(driver_files)
    dialogue_strings=merge_localization(dialogue_strings,driver_strings)
    with zipfile.ZipFile(target/'Localization/English_xml.pak') as z:
        dialogue_strings=merge_localization(read(z,'text_ui_dialog.xml'),dialogue_strings)
    with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as z:
        native_world=ET.fromstring(read(z,'whdata_0'))
        native_mission=read(z,'objects_mission0.xml')
        native_scheduler=read(z,'tables/ai/scheduler.xml')
        player=one((s for s in native_world.findall('./SoulList/Souls/Soul') if s.findtext('Player')=='1'), 'native player registration')
    package=output/MOD;data=package/'Data';data.mkdir(parents=True)
    namespace='gluemapper_rattay'
    print('Importing original Rattay upper-gate innkeeper and stock',flush=True)
    merchant=resolve_merchant(source,'rato_innkeeper1')
    merchant_files,shop_report=shop_resources(merchant,target,namespace)
    character_directory=output/'merchant-character'
    character_report=build_appearance(source,target,merchant,namespace,character_directory)
    merchant_files.update(actor_resources(target,merchant,character_report,namespace))
    print('Importing Theresa and registering the arrival side quest',flush=True)
    theresa_person,theresa_audit=theresa.resolve(source)
    theresa_directory=output/'theresa-character'
    theresa_character=build_appearance(source,target,theresa_person,theresa.NAMESPACE,theresa_directory)
    merchant_files,theresa_names=theresa.resources(target,source,theresa_person,theresa_character,merchant_files)
    dialogue_files.update(merchant_files)
    travel_level,map_files,map_report=map_resources(source,level_registration(target,LEVEL,LEVEL_ID),LEVEL)
    dialogue_files.update(map_files)
    trade_strings=ET.Element('Table');row=ET.SubElement(trade_strings,'Row')
    for value in ('ui_gmtravel_rattay_trade','(Trade)','(Trade)'):ET.SubElement(row,'Cell').text=value
    dialogue_strings=merge_localization(dialogue_strings,xml(trade_strings))
    graphs={};stations={}
    needed={model.lower() for name,model,p,q in prefab_geometry(prefab) if name.startswith('wheel_')}
    wheel_bounds={}
    for path in sorted((target/'Data').glob('*Objects*.pak')):
        with zipfile.ZipFile(path) as assets:
            for n in assets.namelist():
                if n.lower() in needed:wheel_bounds[n.lower()]=wheel_bottom(read(assets,n))
    if needed!=set(wheel_bounds):raise ValueError('Missing cart wheel meshes')
    for side,level,base in [('trosky','trosecko',target/'Data/Levels/trosecko'),('kcd1',LEVEL,world)]:
        print('Packaging travel station:',side,flush=True)
        dest=data/'Levels'/level;dest.mkdir(parents=True)
        with zipfile.ZipFile(base/'level.pak') as z:
            names={n.lower():n for n in z.namelist()}
            marker,entry=native_arrival(z,'startingPoint_trosecko') if side=='trosky' else (departure_marker,'objects_mission0.xml')
            placement={}
            ground=None
            if side=='kcd1':
                with zipfile.ZipFile(base/'terrain.pak') as terrain:
                    ground=Heightfield(read(terrain,'terrain/terrain.dat'))
            mission,graph,blob,links=station(read(z,names['objects_mission0.xml']),marker,side,prefab,ground,wheel_bounds,placement)
            if side=='kcd1':
                station_entities=ET.fromstring(mission)
                def station_position(name):
                    return tuple(map(float,station_entities.find(f"Entity[@Name='{name}']").get('Pos').split(',')))
                road_report=road_spawn_points(ground.terrain,station_position('gmtravel_arrival_kcd1'),
                    [(station_position(driver.NAME),6.),(placement['cart_origin'],4.5)],ground)
                mission,ui_report=import_showroom(mission,native_mission)
                mission,horse_report=import_horse_scheduler(mission,native_mission,marker,ground)
            graphs[graph]=blob
            replacements={'objects_mission0.xml':mission,'whdata_0':preprocess(read(z,names['whdata_0']) if 'whdata_0' in names else None,graph,level),
                'waitinglinks.xml':append_links(read(z,names['waitinglinks.xml']) if 'waitinglinks.xml' in names else None,links)}
            if side=='kcd1':
                navigation_entries,navigation_report=package_navigation(source,target,dest)
                replacements.update(navigation_entries)
                # Register the same native player identity. No equipment or RPG
                # values are assigned; the transfer/save system owns those.
                wh=ET.fromstring(replacements['whdata_0'])
                if wh.find('SoulList') is not None:
                    raise ValueError('Static destination unexpectedly contains soul registrations')
                ET.SubElement(ET.SubElement(wh,'SoulList'),'Souls').append(copy.deepcopy(player))
                wh.find('./SoulList/Souls').append(driver.registration(guid(driver.NAME)))
                ET.SubElement(wh.find('ConceptManager'),'IncludeModGraphs').text='0'
                ET.SubElement(ET.SubElement(wh.find('AI'),'SmartAreaManager',version='2'),'SmartAreas')
                replacements['whdata_0']=xml(wh)
                for n in ('levelinfo.xml','leveldata.xml'):
                    r=ET.fromstring(read(z,names[n]))
                    (r if n=='levelinfo.xml' else r.find('LevelInfo')).set('Name','data/levels/'+level if n=='levelinfo.xml' else level)
                    replacements[n]=xml(r)
                for n,b in level_table_shells(target).items():
                    if n.lower() not in names:replacements[n]=b
                scheduler_path='tables/ai/scheduler.xml'
                scheduler=replacements.get(scheduler_path)
                if scheduler is None:scheduler=read(z,names[scheduler_path])
                replacements[scheduler_path],scheduler_report=import_horse_scheduler_table(scheduler,native_scheduler,mission)
                horse_report.update(scheduler_report)
                # Use a real Level module and native streamed-profile entry,
                # rather than treating this destination as a static map probe.
                for name in ('whdata_1','extractedlayerentityids.xml','triggerareas.fubar','mission_mission0.xml'):
                    if name in names and name not in replacements:
                        replacements[name]=read(z,names[name])
                with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as entry_source, \
                     zipfile.ZipFile(target/'Data/Scripts.pak') as entry_scripts:
                    replacements,entry_graphs,entry_report=register_entry(
                        replacements,blob,graph,entry_source,entry_scripts,LEVEL,LEVEL_ID,guid)
                graphs.update(entry_graphs)
                graphs,visit_strings=attach_visit(graphs,graph,LEVEL,player.findtext('SharedSoulGuid'),merchant['soul']['soul_id'])
                replacements,graphs,merchant_world_report=register_world(
                    replacements,graphs,graph,source,target,merchant,shop_report,guid)
                replacements,graphs,theresa_strings,theresa_world=theresa.register_world(
                    replacements,graphs,graph,LEVEL,player.findtext('SharedSoulGuid'),theresa_person)
                replacements,person_world_report=register_person_world(
                    replacements,[merchant,theresa_person],source,target)
                visit_strings=merge_localization(visit_strings,theresa_strings)
                replacements,location_files,location_strings,location_report=location_resources(
                    source,target,replacements,LEVEL_ID)
                dialogue_files.update(location_files)
            with zipfile.ZipFile(dest/'level.pak','x',zipfile.ZIP_STORED,allowZip64=False) as dst:
                for info in z.infolist():
                    b=replacements.pop(info.filename.lower(),None)
                    dst.writestr(info,read(z,info.filename) if b is None else b)
                for n,b in replacements.items():dst.writestr(n,b)
            with zipfile.ZipFile(dest/'level.pak') as packed:
                (dest/'levelinfo.xml').write_bytes(read(packed,'levelinfo.xml'))
            stations[side]={'source_marker':dict(marker.attrib),'source_entry':entry,'graph':graph,'arrival':'gmtravel_arrival_'+side,'grounding':placement}
            if side=='kcd1':stations[side].update(inventory_ui=ui_report,driver_soul=driver.SOUL,horse_scheduler=horse_report,navigation=navigation_report,entry=entry_report,horse_road=road_report)
        if side=='kcd1':
            for f in base.iterdir():
                if f.is_file() and f.suffix.lower()=='.pak' and f.name.lower() not in ('level.pak','recast.pak'):shutil.copy2(f,dest/f.name)
    dialogue_files.update(horse_recovery_resources(native_player,{LEVEL:stations['kcd1']['horse_road']}))
    common_files = {
        'Libs/Tables/LevelSwitch__'+MOD+'.xml': table,
        'Libs/Tables/level__'+MOD+'.xml': travel_level,
        **graphs, **dialogue_files,
    }
    for path in sorted([*character_directory.rglob('*'), *theresa_directory.rglob('*')]):
        if path.is_file():
            owner = character_directory if path.is_relative_to(character_directory) else theresa_directory
            name = path.relative_to(owner).as_posix()
            if name.startswith('Localization/'):
                continue  # Localized source item names belong in the language PAK.
            if name in common_files:
                raise ValueError('Duplicate packaged resource: ' + name)
            common_files[name] = path.read_bytes()
    common_files = managed_patches(common_files, MOD)
    with zipfile.ZipFile(data/(MOD+'.pak'),'x',zipfile.ZIP_STORED) as z:
        for n,b in common_files.items():z.writestr(n,b)
        for level in ('trosecko',LEVEL):
            with zipfile.ZipFile(data/'Levels'/level/'level.pak') as levelpak:
                for n in levelpak.namelist():
                    if n.lower().startswith('tables/'):
                        z.writestr(f'Mods/{MOD}/Data/Levels/{level}/{n}',read(levelpak,n))
    manifest=ET.Element('kcd_mod');info=ET.SubElement(manifest,'info')
    for k,v in {'name':'KCD1 Region Travel','modid':MOD,'author':'SamG-Coder','version':'0.2.0-alpha.3',
                'description':'Experimental native travel between Trosky and the imported KDC1 map.'}.items():ET.SubElement(info,k).text=v
    (package/'mod.manifest').write_bytes(xml(manifest))
    localization=package/'Localization';localization.mkdir()
    with zipfile.ZipFile(target/'Localization/English_xml.pak') as z:
        quest_strings=merge_localization(read(z,'text_ui_quest.xml'),visit_strings)
    with zipfile.ZipFile(localization/'English_xml.pak','x',zipfile.ZIP_STORED) as z:
        with zipfile.ZipFile(target/'Localization/English_xml.pak') as native_strings:
            soul_strings=merge_localization(read(native_strings,'text_ui_soul.xml'),theresa_names)
        texts={'text_ui_dialog.xml':dialogue_strings,'text_ui_quest.xml':quest_strings,'text_ui_soul.xml':soul_strings}
        with zipfile.ZipFile(target/'Localization/English_xml.pak') as native_strings:
            for character_dir in (character_directory, theresa_directory):
                item_strings = character_dir/'Localization/English/text_ui_items.xml'
                if item_strings.is_file():
                    base = texts.get('text_ui_items.xml', read(native_strings, 'text_ui_items.xml'))
                    texts['text_ui_items.xml'] = merge_localization(base, item_strings.read_bytes())
            for name,blob in location_strings.items():
                base=texts[name] if name in texts else read(native_strings,name)
                texts[name]=merge_missing_strings(base,blob)
        for name,blob in texts.items(): z.writestr(name,blob)
    report={'schema':1,'mod':MOD,'level':LEVEL,'level_id':LEVEL_ID,'departure':evidence,'stations':stations,
        'person_world':person_world_report,
        'theresa':dict(world=theresa_world,character=theresa_character,date_source_audit=theresa_audit),
        'requires_converted_asset_mod':'kingdomcomegluemapper','runtime_verified':False,'round_trip_verified':False,
        'map_ui':dict(map_report,registration=location_report),'rattay_inn':dict(world=merchant_world_report,shop=shop_report,
            character=character_report,source=merchant['provenance']),
        'driver_dialogue':{'option':'Travel to KDC1','original_destination_preserved':True,'new_voice_lines':False,'fare':0,'runtime_verified':False},
        'preservation_tests_pending':['player identity','inventory and equipped clothing','horse and saddle inventory','KDC2 quests','save/load in both maps'],
        'files':{f.relative_to(package).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in package.rglob('*') if f.is_file()}}
    (output/'travel-build.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kcd1',type=Path,required=True);p.add_argument('--kcd2',type=Path,required=True)
    p.add_argument('--world',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=build(a.kcd1,a.kcd2,a.world,a.output)
    print(json.dumps({k:v for k,v in r.items() if k!='files'},indent=2))
