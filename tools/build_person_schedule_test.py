"""Stage source-driven schedule arrival conversion for registered actors."""
import argparse
import json
from pathlib import Path
import zipfile
from campaign_sources import RetailSourceReader
from campaign_dependency_adapters import SourceTables
from character_person import resolve_person
from character_schedule import plan
from character_schedule_world import resolve_arrivals, bind_arrivals, arrival_resources
from build_horse_trader_test import rewrite
from upgrade_map import read
from character_schedule_jobs import bind_jobs, job_resources
from world_item_definitions import native_items, native_item_registry
import seating_animations
from character_person_activity import validate_scheduler_targets
import xml.etree.ElementTree as ET


def build(a):
    if a.output.exists(): raise FileExistsError('Choose a fresh staging directory')
    schedules = {}
    with RetailSourceReader(a.source) as reader, zipfile.ZipFile(a.source / 'Data/Levels' / a.source_level / 'level.pak') as archive:
        tables = SourceTables(a.source, reader)
        for name in a.person:
            person = resolve_person(a.source, name, a.source_level)
            schedule = plan(person, tables)
            if schedule['pending']: raise ValueError('Unresolved source schedule: ' + name)
            schedules[name] = resolve_arrivals(schedule, archive, person, a.source, reader, a.target)
    with zipfile.ZipFile(a.level_pak) as archive:
        files = {n:read(archive,n) for n in ('objects_mission0.xml','tables/ai/scheduler.xml','waitinglinks.xml',
                    seating_animations.HELPERS,seating_animations.ANIMATIONS)}
        if getattr(a,'routes',False):files['ubernav.tmm']=read(archive,'ubernav.tmm')
    resources=arrival_resources()
    if getattr(a,'routes',False):
        if not a.jobs:raise ValueError('--routes requires --jobs')
    if a.jobs_only:
        if not a.jobs:raise ValueError('--jobs-only requires --jobs')
        report=[]
    else:
        files, report = bind_arrivals(files, schedules, a.target)
    if a.jobs:
        from character_horse_handler_work import refresh_existing
        files, stable_refreshes = refresh_existing(files, a.target)
        with zipfile.ZipFile(a.travel_pak) as pak:
            items=[read(pak,n) for n in pak.namelist() if n.lower().startswith('libs/tables/item/item__') and n.endswith('.xml')]
        registered=native_item_registry(native_items(a.target,items))
        with zipfile.ZipFile(a.source/'Data/Levels'/a.source_level/'level.pak') as archive:
            files,jobs=bind_jobs(files,schedules,archive,a.target,registered,getattr(a,'routes',False))
        resources.update(job_resources(a.source,a.target,files,schedules))
        report=dict(arrivals=report,jobs=jobs,stable_approach_refreshes=stable_refreshes)
        # Match setup's additive sleep policy after replacing an owned idle
        # routine. Otherwise a test package can reintroduce all-night work.
        from character_sleep_work import restrict_work_to_waking_hours
        from upgrade_map import xml
        scheduler = ET.fromstring(files['tables/ai/scheduler.xml'])
        report['work_clock_repairs'] = restrict_work_to_waking_hours(
            ET.fromstring(files['objects_mission0.xml']), scheduler, actors=set(schedules))
        files['tables/ai/scheduler.xml'] = xml(scheduler)
        with zipfile.ZipFile(a.target/'Data/Levels/trosecko/level.pak') as native:
            files,animation_report=seating_animations.install(files,native)
        report['animations']=animation_report
    validate_scheduler_targets(ET.fromstring(files['tables/ai/scheduler.xml']))
    resources[f'Mods/{a.mod_id}/Data/Levels/{a.level}/tables/ai/scheduler.xml'] = files['tables/ai/scheduler.xml']
    for name in (seating_animations.HELPERS,seating_animations.ANIMATIONS):
        resources[f'Mods/{a.mod_id}/Data/Levels/{a.level}/'+name]=files[name]
    a.output.mkdir(parents=True)
    rewrite(a.level_pak, a.output / 'level.pak', files)
    rewrite(a.travel_pak, a.output / 'gluemappertravel.pak', resources)
    (a.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','target','level-pak','travel-pak','output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--person', action='append', required=True)
    parser.add_argument('--source-level', default='rataje')
    parser.add_argument('--level', default='kcd1_travel')
    parser.add_argument('--mod-id', default='kingdomcomegluemapper')
    parser.add_argument('--jobs', action='store_true', help='Activate supported native work phases and their reserved tools')
    parser.add_argument('--routes', action='store_true', help='Connect the source sweeping paths through native movement and item lifetimes')
    parser.add_argument('--jobs-only', action='store_true', help='Upgrade existing imported arrival schedules without resetting other routines')
    build(parser.parse_args())
