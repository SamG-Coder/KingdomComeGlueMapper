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
from cooking_pots import rotate
from entity_visuals import quaternion_product as qmul
from upgrade_map import read, xml
from region_travel_dialogue import patch as patch_driver_dialogue, merge_localization, HOST, DIALOGUE
from region_travel_ground import Heightfield, wheel_bottom, fit_cart
from region_travel_road import road_spawn_points
import region_travel_driver as driver
from region_travel_ui import import_showroom
from region_travel_companions import import_horse_scheduler, import_horse_scheduler_table, travel_player_resources
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
    # Use a registered native regional loading screen. LoadingScreen selects
    # presentation; SwitchLevel's LevelChanger readiness observer owns the wait.
    # Never call New Game or substitute a timer for that native completion gate.
    native_route = one((r for r in rows if r.get('Name') == 'trosecko'), 'native Trosky route')
    screen = native_route.get('LoadingScreen')
    if not screen or not screen.isdecimal():
        raise ValueError('Native Trosky route has no valid loading screen')
    output = ET.Element(root.tag, root.attrib)
    result = ET.SubElement(output, rows.tag, rows.attrib)
    for route, target, marker in [('to_kcd1', LEVEL_ID, 'gmtravel_arrival_kcd1'),
                                  ('to_trosky', 2, 'gmtravel_arrival_trosky')]:
        name = MOD + '_' + route
        if any(r.get('Name') == name for r in rows):
            raise ValueError('LevelSwitch name collision')
        ET.SubElement(result, 'LevelSwitchData', Name=name, TargetLevelId=str(target),
                      TargetLocationEntity=marker, LoadingScreen=screen)
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


def build(source, target, world, output, *, modid=MOD, test_person=None,
          test_bandit_person=None, bandit_template='tpod_bandit_3'):
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
    package=output/modid;data=package/'Data';data.mkdir(parents=True)
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
    character_directories=[character_directory,theresa_directory]
    import miller
    import miller_world
    from character_person import actor_resources as person_resources
    print('Importing Peshek, lockpick shop and work/sleep schedule',flush=True)
    miller_person=miller.resolve(source)
    miller_files,miller_shop=miller.resources(miller_person,target)
    dialogue_files.update(miller_files)
    miller_directory=output/'miller-character'
    miller_character=build_appearance(source,target,miller_person,miller.NAMESPACE,miller_directory)
    character_directories.append(miller_directory)
    dialogue_files=person_resources(target,miller_person,miller_character,miller.NAMESPACE,role_name=miller.ROLE,
        native_soul_table='Libs/Tables/rpg/soul__ttac.xml',native_soul_name='ttac_procek',
        native_role_name='PREVOZNIK_TROSECKO',resources=dialogue_files)
    with zipfile.ZipFile(source/'Localization/English_xml.pak') as source_strings:
        names=ET.fromstring(read(source_strings,'text_ui_soul.xml'))
    selected=ET.Element('Table')
    key=miller_person['instance'].findtext('StaticData/NameStringId')
    selected.append(one((row for row in names.findall('Row') if row.findtext('Cell')==key),'miller UI name'))
    theresa_names=merge_localization(theresa_names,xml(selected))
    clone_report=None
    bandit_report=None
    if test_person:
        from character_person import resolve_person
        from character_native_clone import clone_world, clone_resources
        clone_namespace='gmtravel_test_'+test_person
        print('Importing native coachman clone appearance:',test_person,flush=True)
        clone_person=resolve_person(source,test_person,include_ai=False)
        clone_directory=output/'test-character'
        clone_character=build_appearance(source,target,clone_person,clone_namespace,clone_directory)
        character_directories.append(clone_directory)
    if test_bandit_person:
        from character_person import resolve_person
        import character_bandit_clone as bandit_clone
        bandit_namespace='gmtravel_bandit_'+test_bandit_person
        print('Importing native bandit comparison:',test_bandit_person,bandit_template,flush=True)
        bandit_source=resolve_person(source,test_bandit_person,include_ai=False)
        bandit_native=bandit_clone.resolve_template(target,bandit_template)
        bandit_directory=output/'bandit-character'
        bandit_character=build_appearance(source,target,bandit_source,bandit_namespace,bandit_directory)
        character_directories.append(bandit_directory)
        dialogue_files,bandit_names=bandit_clone.resources(dialogue_files,source,bandit_source,
            bandit_character,bandit_native,bandit_namespace)
        theresa_names=merge_localization(theresa_names,bandit_names)
    travel_level,map_files,map_report=map_resources(source,level_registration(target,LEVEL,LEVEL_ID),LEVEL)
    dialogue_files.update(map_files)
    trade_strings=ET.Element('Table');row=ET.SubElement(trade_strings,'Row')
    for value in ('ui_gmtravel_rattay_trade','(Trade)','(Trade)'):ET.SubElement(row,'Cell').text=value
    row=ET.SubElement(trade_strings,'Row')
    for value in ('ui_gm_miller_trade','(Trade)','(Trade)'):ET.SubElement(row,'Cell').text=value
    dialogue_strings=merge_localization(dialogue_strings,xml(trade_strings))
    graphs={};stations={};world_item_strings={}
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
                if test_person:
                    actor=ET.fromstring(replacements['objects_mission0.xml']).find(f"Entity[@Name='{driver.NAME}']")
                    x,y,_=map(float,actor.get('Pos').split(','))
                    position=(x-3.,y-2.,ground(x-3.,y-2.)+.02)
                    replacements,clone_report=clone_world(replacements,driver.NAME,clone_namespace,position)
                    merged,clone_names=clone_resources({**dialogue_files,**graphs},source,clone_person,
                        clone_character,clone_namespace,template_name=driver.NAME,template_role=driver.ROLE,
                        template_dialogue=driver.GRAPH,target_graph=graph)
                    graphs={k:merged.pop(k) for k in graphs}
                    dialogue_files=merged
                    theresa_names=merge_localization(theresa_names,clone_names)
                    clone_report.update(source_person=test_person,character=clone_character)
                if test_bandit_person:
                    from character_bandit_camp import resolve_camp, register as register_bandit_camp
                    camp=resolve_camp(source,'q_epilogue_camp')
                    x,y,_=camp['position']
                    position=(x,y,ground(x,y)+.02)
                    replacements,bandit_report=bandit_clone.register_world(replacements,source,target,
                        bandit_native,bandit_namespace,position)
                    replacements,dialogue_files,camp_report=register_bandit_camp(
                        replacements,dialogue_files,target,bandit_namespace,camp,LEVEL_ID,bandit_native)
                    bandit_report['camp']=camp_report
                    bandit_report.update(source_person=test_bandit_person,character=bandit_character)
            if side=='kcd1':
                from world_runtime_contracts import apply as apply_world_contracts
                with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as native_contracts:
                    replacements,contract_report=apply_world_contracts(replacements,native_contracts)
                entry_report['runtime_contracts']=contract_report
                from world_gameplay_basis import apply as apply_gameplay_basis
                with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as native_contracts, \
                     zipfile.ZipFile(target/'Data/Scripts.pak') as native_scripts:
                    replacements,graphs,basis_report=apply_gameplay_basis(
                        replacements,graphs,graph,LEVEL,native_contracts,native_scripts,guid)
                entry_report['gameplay_basis']=basis_report
                from cooking_pots import convert as convert_cooking_pots
                with zipfile.ZipFile(source/'Data/Levels/rataje/level.pak') as source_pots, \
                     zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as native_pots:
                    replacements,pots_report=convert_cooking_pots(replacements,source_pots,native_pots,guid)
                entry_report['cooking_pots']=pots_report
                from player_chests import build as build_player_chests
                replacements,chest_assets,chest_report=build_player_chests(replacements,source,target,guid)
                dialogue_files.update(chest_assets)
                entry_report['player_chests']=chest_report
                from chairs import convert as convert_chairs
                with zipfile.ZipFile(source/'Data/Levels/rataje/level.pak') as source_chairs, \
                     zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as native_chairs:
                    replacements,chairs_report=convert_chairs(replacements,source_chairs,native_chairs,guid)
                entry_report['chairs']=chairs_report
                from benches import convert as convert_benches
                with zipfile.ZipFile(source/'Data/Levels/rataje/level.pak') as source_benches, \
                     zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as native_benches, \
                     zipfile.ZipFile(source/'Data/GameData.pak') as source_game:
                    helper_name=next(n for n in source_game.namelist() if n.lower()=='libs/smartobjects.xml')
                    replacements,bench_report=convert_benches(replacements,source_benches,native_benches,read(source_game,helper_name),guid)
                entry_report['benches']=bench_report
                from room_rental import build as build_room_rental
                replacements,rental_resources,rental_report=build_room_rental(
                    replacements,{**dialogue_files,**graphs},source,target,guid)
                graphs={name:rental_resources.pop(name) for name in graphs}
                dialogue_files=rental_resources
                entry_report['room_rental']=rental_report
                from washing_troughs import convert as convert_washing_troughs
                with zipfile.ZipFile(source/'Data/Levels/rataje/level.pak') as wash_source, \
                     zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as wash_native, \
                     zipfile.ZipFile(source/'Data/GameData.pak') as wash_source_game, \
                     zipfile.ZipFile(target/'Data/IPL_GameData.pak') as wash_native_game:
                    replacements,wash_report=convert_washing_troughs(
                        replacements,wash_source,wash_native,wash_source_game,wash_native_game,guid)
                entry_report['washing_troughs']=wash_report
                from world_doors import build as build_world_doors
                replacements,door_resources,door_strings,door_report=build_world_doors(replacements,source,target)
                dialogue_files.update(door_resources)
                entry_report['doors']=door_report
                from ladders import build as build_ladders
                replacements,ladder_report=build_ladders(replacements,source,target,guid)
                entry_report['ladders']=ladder_report
                replacements,miller_resources,miller_report=miller_world.register(
                    replacements,{**dialogue_files,**graphs},miller_person,miller_shop,source,target,guid)
                graphs={name:miller_resources.pop(name) for name in graphs}
                dialogue_files=miller_resources
                entry_report['miller']=dict(world=miller_report,shop=miller_shop,character=miller_character)
                from world_beds import build as build_world_beds
                replacements,beds_report=build_world_beds(replacements,source,target,guid)
                entry_report['beds']=beds_report
                import world_items
                replacements,item_resources,world_item_strings,item_assets,item_report=world_items.build(
                    replacements,dialogue_files,source,target,output/'world-item-conversion')
                dialogue_files=item_resources
                world_items.pack_assets(data/'gluemapper_world_items.pak',item_assets)
                entry_report['world_items']=item_report
                import world_lighting
                from lighting_cubemaps import pack_assets as pack_lighting
                replacements,lighting_assets,lighting_report=world_lighting.build(
                    replacements,source,target,LEVEL,guid,output/'lighting-conversion')
                pack_lighting(data,lighting_assets)
                import world_voxels
                lighting_report['voxels']=world_voxels.build(source,dest)
                replacements['mission_mission0.xml']=world_voxels.configure_environment(replacements['mission_mission0.xml'])
                from interior_exposure import convert as convert_exposure
                replacements['mission_mission0.xml'], lighting_report['exposure'] = convert_exposure(
                    replacements['mission_mission0.xml'])
                lighting_report['indirect_lighting']='Native basic GI with converted KCD1 opacity voxels and environment probes'
                entry_report['lighting']=lighting_report
                import wildlife
                wildlife_files = {n: replacements[n] if n in replacements else read(z, names[n])
                                  for n in wildlife.LEVEL_FILES}
                wildlife_files, wildlife_report = wildlife.convert_level(wildlife_files, source, target, ground)
                replacements.update(wildlife_files)
                entry_report['wildlife'] = wildlife_report
                from world_environment import build as build_environment
                replacements['objects_mission0.xml'], environment_report = build_environment(
                    replacements['objects_mission0.xml'], source, target, guid)
                entry_report['environment'] = environment_report
                import world_music
                music_level = {n: replacements[n] if n in replacements else read(z,names[n])
                               for n in ('objects_mission0.xml','triggerareas.fubar')}
                music_changes, music_files, music_report = world_music.resources(
                    music_level,source,target,guid)
                replacements.update(music_changes)
                with zipfile.ZipFile(data/'gluemapper_music.pak','w',zipfile.ZIP_DEFLATED,compresslevel=1) as music_pak:
                    for name,blob in sorted(music_files.items()): music_pak.writestr(name,blob)
                entry_report['music'] = music_report
                from area_geometry_contract import normalize
                area_missions = [replacements['objects_mission0.xml']]
                layer_names = {n.lower() for n in z.namelist() if n.lower().startswith('layers/') and n.lower().endswith('.xml')}
                layer_names.update(n for n in replacements if n.startswith('layers/') and n.endswith('.xml'))
                area_missions.extend(replacements[n] if n in replacements else read(z,names[n]) for n in sorted(layer_names))
                replacements['triggerareas.fubar'], removed = normalize(replacements['triggerareas.fubar'], area_missions)
                entry_report['removed_duplicate_smart_geometry'] = removed
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
                if (f.is_file() and f.suffix.lower()=='.pak' and f.name.lower() not in ('level.pak','recast.pak')
                        and not f.name.lower().startswith(('svo', 'ipl_svo'))):shutil.copy2(f,dest/f.name)
    dialogue_files.update(travel_player_resources(native_player))
    common_files = {
        'Libs/Tables/LevelSwitch__'+MOD+'.xml': table,
        'Libs/Tables/level__'+MOD+'.xml': travel_level,
        **graphs, **dialogue_files,
    }
    for path in sorted(p for directory in character_directories for p in directory.rglob('*')):
        if path.is_file():
            owner = next(directory for directory in character_directories if path.is_relative_to(directory))
            name = path.relative_to(owner).as_posix()
            if name.startswith('Localization/'):
                continue  # Localized source item names belong in the language PAK.
            if name in common_files:
                raise ValueError('Duplicate packaged resource: ' + name)
            common_files[name] = path.read_bytes()
    common_files = managed_patches(common_files, modid)
    with zipfile.ZipFile(data/(MOD+'.pak'),'x',zipfile.ZIP_STORED) as z:
        for n,b in common_files.items():z.writestr(n,b)
        for level in ('trosecko',LEVEL):
            with zipfile.ZipFile(data/'Levels'/level/'level.pak') as levelpak:
                for n in levelpak.namelist():
                    if n.lower().startswith('tables/'):
                        z.writestr(f'Mods/{modid}/Data/Levels/{level}/{n}',read(levelpak,n))
    manifest=ET.Element('kcd_mod');info=ET.SubElement(manifest,'info')
    for k,v in {'name':'KCD1 Region Travel','modid':modid,'author':'SamG-Coder','version':'0.2.0-alpha.3',
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
            texts['text_ui_items.xml']=merge_localization(read(native_strings,'text_ui_items.xml'),door_strings)
            for character_dir in character_directories:
                item_strings = character_dir/'Localization/English/text_ui_items.xml'
                if item_strings.is_file():
                    base = texts.get('text_ui_items.xml', read(native_strings, 'text_ui_items.xml'))
                    texts['text_ui_items.xml'] = merge_localization(base, item_strings.read_bytes())
            for name,blob in location_strings.items():
                base=texts[name] if name in texts else read(native_strings,name)
                texts[name]=merge_missing_strings(base,blob)
        for name,blob in world_item_strings.items():
            texts[name]=merge_localization(texts.get(name,b'<Table/>'),blob)
        for name,blob in texts.items(): z.writestr(name,blob)
    report={'schema':1,'mod':modid,'level':LEVEL,'level_id':LEVEL_ID,'departure':evidence,'stations':stations,
        'person_world':person_world_report,'test_person':clone_report,'test_bandit':bandit_report,
        'theresa':dict(world=theresa_world,character=theresa_character,date_source_audit=theresa_audit),
        'requires_converted_asset_mod':'kingdomcomegluemapper','runtime_verified':False,'round_trip_verified':False,
        'map_ui':dict(map_report,registration=location_report),'rattay_inn':dict(world=merchant_world_report,shop=shop_report,
            character=character_report,source=merchant['provenance']),
        'driver_dialogue':{'option':'Travel to the Rattay region','original_destination_preserved':True,'new_voice_lines':False,'fare':0,'runtime_verified':False},
        'preservation_tests_pending':['player identity','inventory and equipped clothing','horse and saddle inventory','KDC2 quests','save/load in both maps'],
        'files':{f.relative_to(package).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in package.rglob('*') if f.is_file()}}
    (output/'travel-build.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kcd1',type=Path,required=True);p.add_argument('--kcd2',type=Path,required=True)
    p.add_argument('--world',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--modid',default=MOD)
    p.add_argument('--test-person',help='Optional KCD1 soul name; clone the return coachman with this appearance')
    p.add_argument('--test-bandit-person',help='Optional KCD1 appearance on a native bandit combat template')
    p.add_argument('--bandit-template',default='tpod_bandit_3',help='Native KCD2 soul name for the isolated bandit test')
    a=p.parse_args();r=build(a.kcd1,a.kcd2,a.world,a.output,modid=a.modid,test_person=a.test_person,
                           test_bandit_person=a.test_bandit_person,bandit_template=a.bandit_template)
    print(json.dumps({k:v for k,v in r.items() if k!='files'},indent=2))
