"""Build a source merchant service on an explicitly staged source actor.

The caller selects the actor, namespace and level. Stock, selling location,
clock, voice identity and physical storage come from source relationships.
No objective completion or item-grant scripts are injected.
"""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as E
import zipfile
from upgrade_map import read, xml


def bind_storage(files, merchant, source, namespace, model, *, authored=None, registered_keys=(), suffix='stash', inventory_contract=None, mission=None):
    """Pass a parsed mission to edit it in place; the caller then serializes it."""
    from merchant_stash import source_stash
    from ordinary_containers import properties
    from campaign_entity_links import append_links
    from region_travel import guid
    if authored is None: authored = source_stash(source,merchant['shop_entity'])
    original = authored.find('Properties')
    inventory = original.find('Database').get('guidInventoryDBId','')
    if inventory_contract is None and inventory not in ('','0','00000000-0000-0000-0000-000000000000'):
        raise ValueError('Merchant stash has an independent inventory requiring conversion')
    record = dict(model=original.get('object_Model',''),kind='service',
        source_properties=E.tostring(original,encoding='unicode'),
        lock=dict(original.find('Lock').attrib),phase=dict(original.find('Phase').attrib) if original.find('Phase') is not None else {})
    props = properties(record,model,inventory_contract or dict(preset='',restock_days=0,read_only=False,registered_keys=registered_keys))
    minimum = original.find('Database').get('iMinimalShopItemPrice')
    if minimum is not None: props.find('Database').set('iMinimalShopItemPrice',minimum)
    owned = mission is None
    if owned: mission = E.fromstring(files['objects_mission0.xml'])
    shop = next(e for e in mission if e.get('Name')==namespace+'_shop')
    npc = next(e for e in mission if e.get('Name')==merchant['name'])
    name = namespace+'_'+suffix
    if any(e.get('Name')==name for e in mission): raise ValueError('Merchant stash already registered')
    # Replace an existing static conversion of this precise source entity.
    for e in list(mission):
        if e.get('Name')=='glue_item_'+authored.get('EntityId'): mission.remove(e)
    existing = [e for e in mission if e.get('Name') == 'gm_container_'+authored.get('EntityId')]
    if len(existing) > 1: raise ValueError('Duplicate converted merchant storage')
    if existing:
        stash = existing[0]
        if stash.get('EntityClass') != 'Stash' or stash.find('Properties').get('object_Model') != model:
            raise ValueError('Existing merchant storage has a different resource contract')
        # World container conversion already owns its collision, lock, keys and
        # inventory. Add service links without creating a second chest in place.
    else:
        stash = E.SubElement(mission,'Entity',Name=name,EntityClass='Stash',EntityGuid=guid(name),
            EntityId=str(max(int(e.get('EntityId','0')) for e in mission)+1),CastShadowMinSpec='1')
        for key in ('Pos','Rotate','Scale'):
            if authored.get(key) is not None:stash.set(key,authored.get(key))
        stash.append(props)
    edges = [(shop,stash,'shopStash'),(npc,stash,'owner')]
    for a,b,label in edges:
        links=a.find('EntityLinks')
        if links is None:links=E.SubElement(a,'EntityLinks')
        E.SubElement(links,'Link',Name=label,TargetId=b.get('EntityId'),TargetGuid=b.get('EntityGuid'))
    result=dict(files)
    if owned: result['objects_mission0.xml']=xml(mission)
    result['waitinglinks.xml']=append_links(files['waitinglinks.xml'],[(a.get('EntityGuid'),b.get('EntityGuid'),label) for a,b,label in edges])
    return result,dict(source_entity=authored.get('Name'),source_id=authored.get('EntityId'),model=model)


def build(source,target,baseline,stage,person,namespace,entry,level, *, restock_policy='source_daily',source_level='rataje'):
    from region_travel_services import resolve_merchant,import_shop_resources
    from campaign_sources import RetailSourceReader
    from campaign_dependency_adapters import SourceTables
    from character_schedule import plan
    from character_schedule_world import resolve_arrivals,bind_arrival_trees
    from character_schedule_jobs import bind_job_trees
    from character_station_work import resources as station_resources
    from campaign_merchant_work import register_trees,add_trade_dialogue
    from dialogue_dubbing import import_source_voices,bind_soul_voice
    from build_actor_dialogues_test import actor_ui_name
    from merchant_stash import source_stashes,inventory_resources
    from world_item_definitions import native_items,native_item_registry
    from setup_package_upgrade import rewrite
    source,target,baseline,stage=map(Path,(source,target,baseline,stage))
    merchant=resolve_merchant(source,person,source_level);merchant['target']=target
    travel='Data/gluemappertravel.pak'; lp='Data/Levels/'+level+'/level.pak'
    with zipfile.ZipFile(stage/travel) as z:resources={n:read(z,n) for n in z.namelist() if n.startswith(('Libs/','Quests/'))}
    with zipfile.ZipFile(stage/lp) as z:files={n:read(z,n) for n in ('objects_mission0.xml','tables/ai/scheduler.xml','waitinglinks.xml')}
    patches=[];assets=set()
    for package in (baseline,stage):
        for p in (package/'Data').glob('*.pak'):
            with zipfile.ZipFile(p) as z:
                assets.update(n.replace('\\','/').lower() for n in z.namelist())
                patches.extend(read(z,n) for n in z.namelist() if n.lower().startswith('libs/tables/item/item__') and n.endswith('.xml'))
    stock_files,stock_strings,stock=import_shop_resources(merchant,source,target,namespace,
        baseline,stage.parent/(namespace+'-stock-items'),extra_item_patches=patches,restock_policy=restock_policy)
    resources.update(stock_files)
    patches.extend(b for n,b in stock_files.items() if n.lower().startswith('libs/tables/item/item__'))
    authored_stashes = source_stashes(source,merchant['shop_entity'],source_level)
    storage_files,storage_strings,contracts,storage_items=inventory_resources(
        source,target,stage,stage.parent/(namespace+'-storage-items'),authored_stashes,extra_item_patches=patches)
    resources.update(storage_files)
    stock_strings.extend(storage_strings)
    patches.extend(b for n,b in storage_files.items() if n.lower().startswith('libs/tables/item/item__'))
    with RetailSourceReader(source) as reader,zipfile.ZipFile(source/'Data/Levels'/source_level/'level.pak') as z:
        tables=SourceTables(source,reader)
        schedule=plan(merchant,tables)
        if schedule['pending']:raise ValueError('Unresolved merchant schedule')
        schedules={person:resolve_arrivals(schedule,z,merchant,source,reader)}
        if any(not (e.get('station_work') or e.get('market_work')) for e in schedules[person]):
            raise ValueError('Merchant service builder requires a converted selling activity')
        # Parse the 30+ MB mission once and serialize it after the last
        # binder, instead of once per binder and storage.
        mission=E.fromstring(files['objects_mission0.xml']);scheduler=E.fromstring(files['tables/ai/scheduler.xml'])
        files=dict(files)
        arrivals=bind_arrival_trees(files,mission,scheduler,schedules,target)
        files,jobs=bind_job_trees(files,mission,scheduler,schedules,z,target,native_item_registry(native_items(target,patches)))
        if any(j.get('pending') for j in jobs):raise ValueError('Merchant work is incomplete')
        for e in schedules[person]:
            if e.get('station_work'): resources.update(station_resources(e['station_work'])[1])
        character=tables.row('rpg/v_soul_character_data','soul_id',merchant['soul']['soul_id'])
        voices=tables.get('text/v_voice_abbreviation')['rows']
        voice=next(v for v in voices if v['voice_id']==character['voice_id'])
        with zipfile.ZipFile(target/'Data/Tables.pak') as native:
            resources,all_voices=import_source_voices(resources,read(native,'Libs/Tables/skald/voice.xml'),voices,{voice['abbreviation']})
        native_voice=next(v for v in E.fromstring(all_voices).iter('voice') if v.get('abbreviation')==voice['abbreviation'])
        # The explicit actor stage owns this role and its Storm assignment.
        role='gm_person_'+merchant['soul']['soul_id'].replace('-','')+'_role'
        roles=[r for n,b in resources.items() if n.startswith('Libs/Tables/rpg/role__') for r in E.fromstring(b).iter('role') if r.get('role_name')==role]
        if len(roles)!=1:raise ValueError('Missing staged merchant role')
        resources,voice_binding=bind_soul_voice(resources,target,namespace,person,native_voice.get('voice_technical_name'),role,actor_ui_name(resources,person))
    files,resources,service=register_trees(files,resources,mission,scheduler,entry,merchant,stock,namespace,role='seller')
    storage=[]
    keys=native_item_registry(native_items(target,patches))
    for authored in authored_stashes:
        model=authored.find('Properties').get('object_Model').replace('\\','/').lower()
        converted='gluebuild/containers/'+hashlib.sha256(model.encode()).hexdigest()[:16]+'/'+Path(model).name
        if converted not in assets:raise ValueError('Missing converted merchant storage geometry: '+converted)
        files,record=bind_storage(files,merchant,source,namespace,converted,authored=authored,
            registered_keys=keys,suffix='stash' if len(authored_stashes)==1 else 'stash_'+authored.get('EntityId'),
            inventory_contract=contracts[authored.find('Properties/Database').get('guidInventoryDBId','')],mission=mission)
        storage.append(record)
    files['objects_mission0.xml']=xml(mission)
    if len(storage)==1: storage=storage[0]
    resources=add_trade_dialogue(resources,entry,person,role,namespace)
    resources.update(stock_files)
    # Setup passes a partial overlay, whose manifest belongs to its baseline.
    modid=E.parse(baseline/'mod.manifest').findtext('info/modid')
    if not modid:raise ValueError('Merchant package requires a destination mod ID')
    resources['Mods/'+modid+'/Data/Levels/'+level+'/tables/ai/scheduler.xml']=files['tables/ai/scheduler.xml']
    report=dict(version=1,person=person,stock=stock,service=service,storage=storage,storage_items=storage_items,jobs=jobs,voice_binding=voice_binding,
        runtime_verified=False,quest_policy='Existing source inventory objective; no scripted grants or completion')
    resources['Libs/GlueMapper/'+namespace+'.json']=json.dumps(report,indent=2).encode()
    rewrite(stage,travel,resources);rewrite(stage,lp,files)
    if stock_strings:
        from build_english_audio_test import merge_audio_strings
        localization = 'Localization/English_xml.pak'
        with zipfile.ZipFile(stage/localization) as archive:
            item_strings = read(archive,'text_ui_items.xml')
        for blob in stock_strings:
            item_strings = merge_audio_strings(item_strings,blob)
        rewrite(stage,localization,{'text_ui_items.xml':item_strings})
    return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('source','target','baseline','stage','person','namespace','entry','level'):p.add_argument('--'+key,required=True)
    a=p.parse_args()
    print(json.dumps(build(**vars(a)),indent=2))
