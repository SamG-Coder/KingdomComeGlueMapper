"""Assemble an explicit, small actor/work/crime slice over the installed world.

Actor selection is a build input. This tool never replaces the campaign with a
new quest skeleton. It keeps incomplete source work phases in the receipt and
does not mark a bounded playtest as full campaign conversion.
"""
import argparse
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import xml.etree.ElementTree as E
import zipfile
from build_quest_people_test import build as actors_build
from build_person_schedule_test import build as jobs_build
from build_profile_systems import build as systems_build
from build_horse_trader_test import rewrite
from character_person_activity import validate_scheduler_targets
from upgrade_map import read, digest



def verify_baseline(mod, baseline):
    for name, expected in baseline.items():
        if digest(mod/name)!=expected:raise ValueError('Installed baseline changed: '+name)


def audit(package, baseline_mod, people):
    level=package/'Data/Levels/kcd1_travel/level.pak'
    with zipfile.ZipFile(level) as z:
        mission=E.fromstring(read(z,'objects_mission0.xml'))
        scheduler=E.fromstring(read(z,'tables/ai/scheduler.xml'))
        souls=E.fromstring(read(z,'whdata_0'))
    validate_scheduler_targets(scheduler)
    for name in people:
        matches=[e for e in mission if e.get('Name')==name]
        if len(matches)!=1:raise ValueError('Missing or duplicate slice actor: '+name)
        registrations=[s for s in souls.findall('SoulList/Souls/Soul') if s.findtext('EntityGuid')==matches[0].get('EntityGuid')]
        if len(registrations)!=1:raise ValueError('Missing or duplicate soul placement: '+name)
    quest_count=0
    with zipfile.ZipFile(baseline_mod/'Data/gluemappertravel.pak') as old,zipfile.ZipFile(package/'Data/gluemappertravel.pak') as new:
        for path in old.namelist():
            if path.lower().startswith(('quests/','ai/gluemapper/combat')):
                if old.read(path)!=new.read(path):raise ValueError('Slice changed existing quest/combat resource: '+path)
                quest_count+=1
        prefix='Mods/kingdomcomegluemapper/Data/Levels/kcd1_travel/tables/ai/scheduler.xml'
        if E.tostring(E.fromstring(read(new,prefix)))!=E.tostring(scheduler):
            raise ValueError('Travel and level scheduler tables disagree')
    return dict(actor_placements=len(people),existing_quest_resources_preserved=quest_count,
                compiled_scheduler_targets_valid=True,runtime_verified=False)


def build(a):
    if a.output.exists():raise FileExistsError('Choose a fresh slice output')
    actor_stage=a.actor_stage or a.output/'actors'
    if a.actor_stage:
        receipt=json.loads((actor_stage/'report.json').read_text())
        if receipt.get('consumed_by'):raise ValueError('Actor stage already assembled; create a fresh actor stage')
        verify_baseline(a.mod,receipt['baseline'])
        if set(x['person'] for x in receipt['converted'])!=set(a.person) or receipt['pending']:
            raise ValueError('Cached actor stage does not match the requested slice')
    else:
        actors_build(SimpleNamespace(source=a.source,target=a.target,mod=a.mod,output=actor_stage,
            model=None,person=a.person,source_level=a.source_level,level='kcd1_travel',
            source_profile=[],dialogue_profile=[],dialogue_model=[],quest_path=None,
            project_path='Quests/GlueTravel/GlueTravel_kcd1.xml'))
        receipt=json.loads((actor_stage/'report.json').read_text())
    package=actor_stage/'package'
    # A staged mod root lets every adapter consume the preceding adapter's
    # output, rather than independently rebuilding from the older installation.
    shutil.copyfile(a.mod/'mod.manifest',package/'mod.manifest')
    job_output=a.output/'jobs'
    jobs_build(SimpleNamespace(source=a.source,target=a.target,source_level=a.source_level,
        level_pak=package/'Data/Levels/kcd1_travel/level.pak',travel_pak=package/'Data/gluemappertravel.pak',
        output=job_output,person=a.person,jobs=True,jobs_only=False,routes=True,
        mod_id='kingdomcomegluemapper',level='kcd1_travel'))
    jobs=json.loads((job_output/'report.json').read_text())
    converted_jobs={row['person'] for row in jobs['jobs'] if row.get('job')}
    if converted_jobs!=set(a.person) or any(not row.get('job') for row in jobs['jobs']):
        raise ValueError('Every slice actor must have an executable job adapter before assembly')
    for name,dest in [('level.pak','Data/Levels/kcd1_travel/level.pak'),('gluemappertravel.pak','Data/gluemappertravel.pak')]:
        (job_output/name).replace(package/dest)
    system_output=a.output/'systems'
    systems_build(SimpleNamespace(source=a.source,target=a.target,mod=package,output=system_output,
        source_level=a.source_level,level='kcd1_travel',profile=a.profile,person=a.person,package=False))
    systems=json.loads((system_output/'systems.json').read_text())
    if systems['registered_souls']!=len(a.person) or systems['schedule_errors'] or systems['person_contract_errors']:
        raise ValueError('Actor services did not bind the selected population')
    world={};resources={}
    for p in (system_output/'fragments').rglob('*'):
        if not p.is_file():continue
        path=p.relative_to(system_output/'fragments').as_posix()
        if path.startswith('world/'):world[path[6:]]=p.read_bytes()
        else:resources[path]=p.read_bytes()
    for relative,changes in [('Data/gluemappertravel.pak',resources),('Data/Levels/kcd1_travel/level.pak',world)]:
        path=package/relative;temp=path.with_suffix('.assembling')
        rewrite(path,temp,changes);temp.replace(path)
    from world_character_props import build as character_props_build
    level_package=package/'Data/Levels/kcd1_travel/level.pak'
    with zipfile.ZipFile(level_package) as z: mission=read(z,'objects_mission0.xml')
    mission,character_props=character_props_build(mission,a.source,a.output/'character_props')
    if character_props['converted']:
        props_package=package/'Data/gm_character_props.pak'
        if props_package.exists() or (a.mod/'Data/gm_character_props.pak').exists():
            raise ValueError('Existing character prop package requires an additive merge')
        with zipfile.ZipFile(props_package,'x',zipfile.ZIP_STORED,allowZip64=False) as z:
            for p in (a.output/'character_props/converted').rglob('*'):
                if p.is_file():z.write(p,p.relative_to(a.output/'character_props/converted').as_posix())
        temporary=level_package.with_suffix('.assembling')
        rewrite(level_package,temporary,{'objects_mission0.xml':mission});temporary.replace(level_package)
    validation=audit(package,a.mod,a.person)
    report=dict(package=str(package.resolve()),people=a.person,profiles=a.profile,
        baseline=receipt['baseline'],validation=validation,character_props=character_props,work=jobs,crime=systems['crime'],
        ready_for_bounded_playtest=True,full_source_routines_complete=False,installed=False,
        scope='Source sweeping route phase and fixed guard post; existing opening quests/combat preserved',
        remaining=['Other housework phases','Guard rain/secondary-post selection',
            'Full source quest-driven town schedule/profile lifecycle','Crime storage, punishment and arrest conversations'],
        packages={str(p.relative_to(package)):digest(p) for p in package.rglob('*.pak')})
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/'slice.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    receipt['consumed_by']=str(a.output.resolve())
    (actor_stage/'report.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=report['package'],validation=validation,ready_for_bounded_playtest=True)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','target','mod','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--actor-stage',type=Path,help='Consume an already validated actor stage; replaces only its generated package files')
    p.add_argument('--person',action='append',required=True)
    p.add_argument('--profile',action='append',required=True)
    p.add_argument('--source-level',default='rataje')
    build(p.parse_args())
