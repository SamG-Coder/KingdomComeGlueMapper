"""Source-scoped tavern conversation and merchant-work migration."""
import json
import zipfile
import xml.etree.ElementTree as E
from pathlib import Path
from types import SimpleNamespace
from upgrade_map import read

VERSION=1
MARKER='Libs/GlueMapper/source_tavern.json'


def build(source,target,package,work,model_path,person,namespace,entry,level,project):
    from build_actor_dialogues_test import build as build_dialogues
    from build_recipe import apply_overlay
    from region_travel_services import resolve_merchant,import_shop_resources
    from container_items import item_patches
    from setup_package_upgrade import rewrite
    from build_english_audio_test import merge_audio_strings
    from character_schedule import plan
    from character_schedule_world import resolve_arrivals,bind_arrivals,arrival_resources
    from character_schedule_jobs import bind_jobs
    from character_sweeping_routes import resources as route_resources
    from campaign_sources import RetailSourceReader
    from campaign_dependency_adapters import SourceTables
    from world_item_definitions import native_items,native_item_registry
    from campaign_merchant_work import register
    import seating_animations
    source,target,package,work=map(Path,(source,target,package,work))
    travel='Data/gluemappertravel.pak';lp='Data/Levels/'+level+'/level.pak'
    report=None
    with zipfile.ZipFile(package/travel) as z:
        if MARKER in z.namelist():
            report=json.loads(read(z,MARKER))
            if report.get('version')!=VERSION: raise ValueError('Unsupported prior tavern migration')
            resources={n:read(z,n) for n in z.namelist() if n.startswith(('Libs/','Quests/'))}
    if report is not None:
        updated=trade(resources,entry,person,namespace)
        changes={n:b for n,b in updated.items() if resources.get(n)!=b}
        if changes:
            rewrite(package,travel,changes)
            from setup_campaign import digest
            return {travel:digest(package/travel)},report
        return {},report
    work.mkdir(parents=True,exist_ok=True)
    merchant=resolve_merchant(source,person);merchant['target']=target
    dep=work/'dependencies'
    item_files,strings,stock=import_shop_resources(merchant,source,target,namespace,package,dep,reuse_output=dep.exists())
    patches=item_patches(package)
    patches.extend(b for n,b in item_files.items() if n.lower().startswith('libs/tables/item/item__'))
    out=work/'dialogues'
    if (out/'report.json').exists():
        from setup_campaign import digest
        previous=json.loads((out/'report.json').read_text())
        if any(digest(Path(n))!=h for n,h in previous['baseline'].items()):
            raise ValueError('Prepared tavern dialogues have a changed package baseline')
        if any(not (out/'package'/n).is_file() or digest(out/'package'/n)!=h for n,h in previous['files'].items()):
            raise ValueError('Prepared tavern dialogue output is incomplete or changed')
    else:
        build_dialogues(SimpleNamespace(source=source,target=target,mod=package,output=out,
            quest_ir=[Path(model_path)],start_topic=[],namespace=namespace+'_dialog',kind='FaderDialog',
            discover_quests=True,quest_owned_scope=True,actor_soul=[merchant['soul']['soul_id']],
            container=entry.removeprefix('Quests/'),container_name=level,activation=json.loads(Path(model_path).read_text())['quest']+'.started',
            skill_upgrade=['threaten=might'],participant_asset=['Dude=player'],area_asset=[],level_archive=package/lp))
    # Compile before archive replacement; failures leave existing data intact.
    with zipfile.ZipFile(out/'package'/travel) as z:resources={n:read(z,n) for n in z.namelist() if n.startswith(('Libs/','Quests/'))}
    with zipfile.ZipFile(package/lp) as z:
        files={n:read(z,n) for n in ('objects_mission0.xml','tables/ai/scheduler.xml','waitinglinks.xml','ubernav.tmm',seating_animations.HELPERS,seating_animations.ANIMATIONS)}
    with RetailSourceReader(source) as reader,zipfile.ZipFile(source/'Data/Levels/rataje/level.pak') as z:
        schedule=plan(merchant,SourceTables(source,reader))
        if schedule['pending']:raise ValueError('Unresolved merchant schedule')
        schedules={person:resolve_arrivals(schedule,z,merchant)}
        files,arrivals=bind_arrivals(files,schedules,target)
        files,jobs=bind_jobs(files,schedules,z,target,native_item_registry(native_items(target,patches)),True)
    if any(not j.get('job') for j in jobs):raise ValueError('Unsupported source merchant work program')
    files,resources,service=register(files,resources,entry,merchant,stock,namespace)
    resources.update(arrival_resources());routes,_=route_resources(source,target);resources.update(routes)
    with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as z:files,_=seating_animations.install(files,z)
    resources.update(item_files)
    resources=trade(resources,entry,person,namespace)
    for n in ('tables/ai/scheduler.xml',seating_animations.HELPERS,seating_animations.ANIMATIONS):
        resources['Mods/kingdomcomegluemapper/'+lp.rsplit('/',1)[0]+'/'+n]=files[n]
    changed=apply_overlay(package,out/'package',consume=True)
    rewrite(package,travel,resources);rewrite(package,lp,files)
    if strings:
        with zipfile.ZipFile(package/'Localization/English_xml.pak') as z:
            original=read(z,'text_ui_items.xml')
        for b in strings:original=merge_audio_strings(original,b)
        rewrite(package,'Localization/English_xml.pak',{'text_ui_items.xml':original})
    report=dict(version=VERSION,person=person,service=service,stock=stock,arrivals=arrivals,jobs=jobs,runtime_verified=False)
    rewrite(package,travel,{MARKER:json.dumps(report,indent=2).encode()})
    from setup_campaign import digest
    for n in (travel,lp,'Localization/English_xml.pak'):changed[n]=digest(package/n)
    return changed,report


def trade(resources,entry,person,namespace):
    from campaign_merchant_work import add_trade_dialogue
    characters=[c for n,b in resources.items() if n.startswith('Libs/Tables/skald/skald_character__')
                for c in E.fromstring(b).iter('skald_character') if c.get('skald_character_name')=='gm_voice_'+person]
    if len(characters)!=1:raise ValueError('Missing or ambiguous merchant dialogue identity')
    return add_trade_dialogue(resources,entry,person,characters[0].get('master_role_name'),namespace)


def upgrade(package,source,target):
    from setup_gameplay import QUEST_PATH,QUEST,ENTRY,LEVEL,PROJECT
    from quest_import import import_quest
    package=Path(package)
    with zipfile.ZipFile(package/'Data/gluemappertravel.pak') as z:
        if QUEST_PATH not in z.namelist():return {},{}
        present=MARKER in z.namelist()
    if source is None or target is None:raise ValueError('Source games required for source tavern upgrade')
    work=package.parent/'tavern-upgrade'
    work.mkdir(exist_ok=True)
    model_path=work/'quest-ir.json'
    if not present:
        from campaign_sources import RetailSourceReader
        with RetailSourceReader(source) as reader:
            model,_=import_quest(Path(source),QUEST,source_reader=reader)
        model_path.write_text(json.dumps(model),encoding='utf-8')
    return build(source,target,package,work,model_path,'ska_blanka','gm_tavern',ENTRY,LEVEL,PROJECT)
