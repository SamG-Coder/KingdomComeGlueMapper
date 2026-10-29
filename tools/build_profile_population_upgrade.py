"""Stage all people and stores from explicitly selected static source profiles.

Uses the same actor, job and merchant adapters for any source profile. Existing
quest actors/services are retained. Full unsupported work programs stay in the
report; they are never represented as successfully converted idle actions.
"""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as E
import zipfile

from campaign_sources import RetailSourceReader
from campaign_dependency_adapters import SourceTables
from profile_population_upgrade import discover, validate_people, validate_services, retain_work
from upgrade_map import read, digest


def clone_missing(source, destination):
    """Copy-on-replace inputs. All subsequent PAK writers atomically replace."""
    import shutil
    for path in source.rglob('*'):
        if not path.is_file(): continue
        if path.is_symlink(): raise ValueError('Symlink in population baseline')
        target = destination/path.relative_to(source)
        if target.exists(): continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try: os.link(path, target)
        except OSError: shutil.copyfile(path, target)


def registered_shops(package):
    result = set()
    for path in (package/'Data').glob('*.pak'):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.lower().startswith('libs/tables/shop/shop__') and name.lower().endswith('.xml'):
                    result.update(row.get('shop_id') for row in E.fromstring(read(archive,name)).iter('ShopData'))
    return result


def refresh_receipt(package, report):
    """Refresh ownership hashes without modifying the hard-linked baseline."""
    from concurrent.futures import ThreadPoolExecutor
    from setup_campaign import RECEIPT, verify_package
    # Check ownership/layout before accepting existing receipt fields. Changed
    # and new files are expected here, so validate against the final inventory.
    path=package/RECEIPT
    receipt=json.loads(path.read_text(encoding='utf-8'))
    paths=sorted(p for p in package.rglob('*') if p.is_file() and p!=path)
    if any(p.is_symlink() or not p.resolve().is_relative_to(package.resolve()) for p in paths):
        raise ValueError('Invalid population package path')
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipt['files']={p.relative_to(package).as_posix(): sha for p,sha in zip(paths,pool.map(digest,paths))}
    receipt['profile_population']=dict(version=report['version'],profiles=report['profiles'],
        people=report['people'],runtime_verified=False)
    temporary=path.with_suffix('.json.partial')
    temporary.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    os.replace(temporary,path)
    verify_package(package,hashes=False)


def upgrade_work(source, target, package, source_level, level, people, new_people, merchants, tables, reader,
                 notify=lambda message: None):
    from character_schedule import plan
    from character_schedule_world import resolve_arrivals, bind_arrivals, arrival_resources, baseline_schedule_names
    from character_schedule_jobs import bind_jobs, population_work_supports, job_resources
    from world_item_definitions import native_items, native_item_registry
    from container_items import item_patches
    from setup_package_upgrade import rewrite
    lp='Data/Levels/'+level+'/level.pak';travel='Data/gluemappertravel.pak'
    with zipfile.ZipFile(package/lp) as archive:
        files={n:read(archive,n) for n in ('objects_mission0.xml','tables/ai/scheduler.xml','waitinglinks.xml','ubernav.tmm')}
    items=native_item_registry(native_items(target,item_patches(package)))
    converted=[];pending=[]
    # New actors and previously imported pending people use the same path.
    # Never reset an existing quest, service or successfully converted job.
    eligible=baseline_schedule_names(files,set(people)-set(merchants))
    missing=set(new_people)-set(merchants)-eligible
    if missing:
        raise ValueError('New population actors lack an owned idle schedule: '+', '.join(sorted(missing)))
    with zipfile.ZipFile(source/'Data/Levels'/source_level/'level.pak') as archive:
        # Older packages incorrectly selected the female housework branch for
        # every actor. Repair only importer-owned route jobs, preserving the
        # already compiled schedule and unrelated quest/service activities.
        from character_sweeping_routes import TEMPLATE as sweeping_template
        from character_work_capabilities import repair_housework_bindings
        mission = E.fromstring(files['objects_mission0.xml'])
        owned_jobs = {e.get('Name') for e in mission if e.find('Properties') is not None
                      and e.find('Properties').get('guidSmartObjectType') == sweeping_template}
        for name, person in people.items():
            actor = next((e for e in mission if e.get('Name') == name), None)
            if actor is None or actor.get('EntityClass') != 'NPC' or not any(
                    job.startswith('gm_schedule_' + name + '_') for job in owned_jobs):
                continue
            notify('Checking existing work: ' + name)
            schedule = plan(person, tables)
            housework = [e for e in schedule['resolved'] if
                (e['behavior']['tree_file'].replace('\\', '/').lower(), e['behavior']['tree_name'])
                == ('final/sa_home.xml', 'housework')]
            if not housework:
                continue
            entries = resolve_arrivals(dict(schedule, resolved=housework), archive, person, source, reader)
            files, repaired = repair_housework_bindings(files, {name: entries}, archive)
            pending.extend(repaired)
        for name in sorted(eligible):
            notify('Converting source schedule: ' + name)
            person=people[name];schedule=plan(person,tables)
            if schedule['pending'] or not schedule['resolved']:
                pending.append(dict(person=name, reason='Source schedule is unresolved', schedule=schedule));continue
            kinds={(e['behavior']['tree_file'].lower(),e['behavior']['tree_name']) for e in schedule['resolved']}
            if not all(population_work_supports(e) for e in schedule['resolved']):
                pending.append(dict(person=name, reason='Complete native work adapter is pending', schedule=schedule));continue
            # Guards without forced workplaces need their source garrison
            # allocator; do not invent a post or send them to the area center.
            if ('final/sa_garrison.xml','Guard') in kinds and not any(
                    l['label']=='garrison_forcedWorkplace' for l in person['ai']['links']):
                pending.append(dict(person=name, reason='Source garrison assignment is pending', schedule=schedule));continue
            entries=resolve_arrivals(schedule,archive,person,source,reader,target)
            working,_=bind_arrivals(files,{name:entries},target)
            working,jobs=bind_jobs(working,{name:entries},archive,target,items,convert_routes=True)
            files=working;converted.extend(jobs)
        # Earlier recipes already converted city walking. Extend only those
        # verified owned pools; do not reset actors or their quest activities.
        from character_city_work import supports as city_supports, extend_existing
        owned_city_hubs = {e.get('Name') for e in E.fromstring(files['objects_mission0.xml'])
            if e.get('EntityClass') == 'SchedulerHub'}
        for name in sorted(set(people)-eligible-set(merchants)):
            if not any(n.startswith('gm_schedule_'+name+'_') for n in owned_city_hubs):
                continue
            notify('Extending city work: ' + name)
            person = people[name]
            schedule = plan(person, tables)
            city = [e for e in schedule['resolved'] if city_supports(e)]
            if schedule['pending'] or not city:
                continue
            entries = resolve_arrivals(dict(schedule, resolved=city), archive, person, source, reader, target)
            files, extended = extend_existing(files, {name: entries}, target)
            converted.extend(extended)
        # Bounded native housework phases replace only owned pending arrivals.
        # Each adapter validates the source gender branch and linked workplace.
        # The same path handles clean builds and older packages.
        from character_fence_work import upgrade as upgrade_fences, supports as fence_supports
        from character_schedule_world import ARRIVAL_TEMPLATE
        mission = E.fromstring(files['objects_mission0.xml'])
        arrival_names = {e.get('Name') for e in mission if e.find('Properties') is not None
                         and (e.find('Properties').get('guidSmartObjectType') == ARRIVAL_TEMPLATE
                              or e.find('Properties').get('guidSmartObjectType') == sweeping_template
                              or e.get('EntityClass') == 'SchedulerHub')}
        housework_schedules = {}
        for name in sorted(set(people)-set(merchants)):
            actor = next((e for e in mission if e.get('Name') == name), None)
            if actor is None or actor.get('EntityClass') not in ('NPC', 'NPC_Female') or not any(
                    n.startswith('gm_schedule_'+name+'_') for n in arrival_names):
                continue
            notify('Resolving household schedule: ' + name)
            person = people[name]; schedule = plan(person, tables)
            work = [e for e in schedule['resolved'] if fence_supports(e)]
            if not schedule['pending'] and work:
                housework_schedules[name] = resolve_arrivals(dict(schedule, resolved=work), archive, person, source, reader, target)
        if housework_schedules:
            notify('Binding fence work')
            files, repaired = upgrade_fences(files, housework_schedules, archive, target)
            from character_home_rest import upgrade as upgrade_rest
            notify('Binding household rest places')
            files, resting = upgrade_rest(files, housework_schedules, archive, target)
            repaired.extend(resting)
            from character_chopping_work import upgrade as upgrade_chopping
            notify('Binding chopping workplaces')
            files, chopping = upgrade_chopping(files, housework_schedules, archive, target, items)
            repaired.extend(chopping)
            from character_household_work import upgrade as upgrade_household
            notify('Binding household workplaces')
            files, household = upgrade_household(files, housework_schedules, archive, target, source, reader, tables)
            repaired.extend(household)
            from character_water_work import upgrade as upgrade_water
            notify('Binding water workplaces')
            files, water = upgrade_water(files, housework_schedules, archive, target, source, reader, tables, items)
            repaired.extend(water)
            changed = {r['person'] for r in repaired if not r.get('extension_only')}
            converted = [r for r in converted if not (r['person'] in changed and r.get('job') == 'source_housework_pending')]
            pending = [r for r in pending if not (r['person'] in changed and r.get('correction') == 'source housework branch excludes sweeping')]
            converted.extend(repaired)
        # Source sleeps take precedence; prologue all-day work routines receive
        # deterministic job-based sleep windows. Native beds remain shared.
        from character_sleep_work import upgrade as upgrade_sleep
        notify('Assigning source beds and sleeping schedules')
        files, sleeping = upgrade_sleep(files, people, archive, target, tables, reader, source, notify=notify)
        converted.extend(sleeping)
        from character_pub_cleaning import extend_people as extend_pub_cleaning
        notify('Binding pub cleaning and rest activities')
        files, cleaning = extend_pub_cleaning(files, people, tables, archive, target, source, reader)
        converted.extend(cleaning)
    import seating_animations
    notify('Installing sitting animations and writing schedule archives')
    with zipfile.ZipFile(package/lp) as archive:
        for name in (seating_animations.HELPERS, seating_animations.ANIMATIONS):
            files[name] = read(archive, name)
    with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as native:
        files, _ = seating_animations.install(files, native)
    rewrite(package,lp,{k:v for k,v in files.items() if k!='ubernav.tmm'})
    modid=E.parse(package/'mod.manifest').findtext('info/modid')
    resources=job_resources(source,target,files,{})
    resources[f'Mods/{modid}/Data/Levels/{level}/tables/ai/scheduler.xml']=files['tables/ai/scheduler.xml']
    for name in (seating_animations.HELPERS, seating_animations.ANIMATIONS):
        resources[f'Mods/{modid}/Data/Levels/{level}/'+name] = files[name]
    rewrite(package,travel,resources)
    return dict(converted=converted,pending=pending,complete=not pending and not any(r.get('pending') for r in converted))


def build(source,target,baseline,output,profiles, *, source_level='rataje',level='kcd1_travel',
          entry='Quests/GlueTravel/entry/kcd1_travel.xml',actor_stage=None,
          update_receipt=True, notify=print, cancelled=lambda: False, progress=None):
    from build_quest_people_test import build as build_actors
    from source_merchant_upgrade import build as build_service
    from setup_package_upgrade import rewrite
    from character_person_activity import validate_scheduler_targets
    source,target,baseline,output=map(Path,(source,target,baseline,output))
    if output.exists():raise FileExistsError('Choose a fresh population stage')
    if any(output.resolve().is_relative_to(p.resolve()) for p in (source,target,baseline)):
        raise ValueError('Population build must be outside game installations and its baseline')
    output.mkdir(parents=True)
    if cancelled(): raise InterruptedError('Cancelled before population conversion')
    previous={}
    with zipfile.ZipFile(baseline/'Data/gluemappertravel.pak') as archive:
        marker='Libs/GlueMapper/profile_population.json'
        if marker in archive.namelist():
            previous=json.loads(read(archive,marker))
            if previous.get('version')!=1 or previous.get('source_level')!=source_level or previous.get('level')!=level:
                raise ValueError('Incompatible previous population conversion')
    with RetailSourceReader(source) as reader:
        tables=SourceTables(source,reader)
        roster,people,shops=discover(source,source_level,profiles,tables)
        if actor_stage is None:
            actor_stage=output/'actors'
            build_actors(SimpleNamespace(source=source,target=target,mod=baseline,output=actor_stage,model=None,
                person=[],source_level=source_level,level=level,source_profile=profiles,
                dialogue_profile=[],dialogue_model=[],project_path=None,quest_path=None,
                notify=notify,cancelled=cancelled,progress=progress,resolved_people=people))
        actor_stage=Path(actor_stage)
        actors=json.loads((actor_stage/'report.json').read_text())
        if actors['pending'] or actors['source_profile_actors'] != roster:
            raise ValueError('Profile actor stage is incomplete or uses a different source selection')
        for path,expected in actors['baseline'].items():
            if digest(baseline/path)!=expected:raise ValueError('Population actor baseline changed: '+path)
        package=output/'package'
        clone_missing(actor_stage/'package',package)
        clone_missing(baseline,package)
        existing=registered_shops(package)
        services=[];preserved=[]
        for index, shop in enumerate(shops):
            if cancelled(): raise InterruptedError('Cancelled before source shop conversion')
            if progress is not None:
                progress('Convert source shops', index, len(shops), shop['shop']['shop_name'])
            identity=str(20000+int(shop['shop']['shop_id']))
            if identity in existing:
                preserved.append(shop);continue
            notify('Converting source shop '+shop['shop']['shop_name'])
            service=build_service(source,target,package,package,shop['person'],shop['namespace'],entry,level,
                source_level=source_level,restock_policy=('source_daily' if shop['shop']['restock_day_mask']=='127' else 'native_merchant'))
            services.append(service);existing.add(identity)
        if shops and progress is not None:
            progress('Convert source shops', len(shops), len(shops), 'Shop conversion complete')
        def work_progress(message):
            if progress is not None:
                progress('Convert source jobs and schedules', 0, 0, message)
            else:
                notify(message)
        work_progress('Resolving source work and schedule bindings')
        work=upgrade_work(source,target,package,source_level,level,people,
            [r['person'] for r in actors['converted']],[r['person'] for r in shops],tables,reader,notify=work_progress)
        work=retain_work(previous.get('work',{}),work,people)
        (output/'work-report.json').write_text(json.dumps(work,indent=2),encoding='utf-8')
    lp='Data/Levels/'+level+'/level.pak'
    if progress is not None:
        progress('Validate source population', 0, 0, 'Checking placements, schedules and shop bindings')
    with zipfile.ZipFile(package/lp) as archive:
        count=validate_people(roster,E.fromstring(read(archive,'objects_mission0.xml')),E.fromstring(read(archive,'whdata_0')))
        validate_scheduler_targets(E.fromstring(read(archive,'tables/ai/scheduler.xml')))
    storage={str(s['stock']['shop_id']) for s in services}
    storage.update(s['shop_id'] for s in previous.get('service_validation',[]) if s['storage'])
    service_validation=validate_services(package,target,shops,level,require_storage=storage)
    report=dict(version=1,source_level=source_level,level=level,profiles=profiles,people=count,
        added=actors['converted'],preserved=actors['existing'],shops_added=services,shops_preserved=preserved,
        work=work,service_validation=service_validation,runtime_verified=False,dialogue_policy='Existing quest dialogue preserved; native trade added for new shops',
        static_profile_policy='Only explicitly selected initial-world profiles; later campaign activation unchanged',
        source_weekday_policy='Daily stock is preserved; weekday masks use the installed KCD2 common_merchant replenishment period',
        baseline=actors['baseline'])
    payload=json.dumps(report,indent=2).encode()
    rewrite(package,'Data/gluemappertravel.pak',{'Libs/GlueMapper/profile_population.json':payload})
    (output/'report.json').write_bytes(payload)
    if update_receipt:
        notify('Verifying population package receipt')
        refresh_receipt(package,report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('source','target','baseline','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--profile',dest='profiles',action='append',required=True)
    p.add_argument('--actor-stage',type=Path)
    p.add_argument('--source-level',default='rataje');p.add_argument('--level',default='kcd1_travel')
    p.add_argument('--entry',default='Quests/GlueTravel/entry/kcd1_travel.xml')
    result=build(**vars(p.parse_args()))
    print(json.dumps(dict(people=result['people'],added=len(result['added']),shops_added=len(result['shops_added']),
                         work_pending=len(result['work']['pending']),runtime_verified=False)))
