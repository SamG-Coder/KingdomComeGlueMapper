"""Dump native/imported NPC registrations without changing the game or saves.

This compares packaged records, not live AI state. Storm rules are retained as
rules; they are not represented as contexts or roles proven active at runtime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import guid_value
from character_person import native_xml, resolve_person
from upgrade_map import read


def record(element):
    if element is None:
        return None
    return dict(tag=element.tag, attributes=dict(element.attrib),
                text=(element.text or '').strip(), children=[record(e) for e in element])


def compare(left, right, path=''):
    """Retain every differing field, including absent attributes/children."""
    if isinstance(left, dict) and isinstance(right, dict):
        result = []
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                result.append(dict(path=path+'/'+key, left=left.get(key), right=right.get(key),
                                   left_present=key in left, right_present=key in right))
            else:
                result.extend(compare(left[key], right[key], path+'/'+key))
        return result
    return [] if left == right else [dict(path=path, left=left, right=right)]


def audit(kcd1, kcd2, mod, output):
    output.mkdir(parents=True, exist_ok=False)
    evidence = {}
    archive_names = {}

    def resource(pak, path):
        with zipfile.ZipFile(pak) as archive:
            if pak not in archive_names:
                archive_names[pak] = {n.replace('\\','/').lower():n for n in archive.namelist()}
            blob = read(archive, archive_names[pak][path.replace('\\','/').lower()])
        evidence[str(pak)+'::'+path] = hashlib.sha256(blob).hexdigest()
        return blob

    data = kcd2/'Data'
    tables = {}
    with zipfile.ZipFile(data/'Tables.pak') as archive:
        for name in archive.namelist():
            normal = name.replace('\\', '/').lower()
            if normal.startswith('libs/tables/') and normal.endswith('.xml'):
                if any(part in normal for part in ('/ai/', '/rpg/soul', '/rpg/social_class', '/rpg/factiontree', '/skald/')):
                    blob = read(archive, name)
                    tables[normal] = native_xml(blob)
                    evidence[str(data/'Tables.pak')+'::'+name] = hashlib.sha256(blob).hexdigest()
    native_souls = {e.get('soul_name'): e for root in tables.values() for e in root.iter('soul')}
    mod_resources = {}
    with zipfile.ZipFile(mod/'Data/gluemappertravel.pak') as archive:
        for name in archive.namelist():
            if name.lower().startswith(('libs/tables/', 'libs/storm/', 'libs/gluemapper/personimport/')):
                mod_resources[name] = read(archive, name)
                evidence[str(mod/'Data/gluemappertravel.pak')+'::'+name] = hashlib.sha256(mod_resources[name]).hexdigest()
    mod_tables = [native_xml(b) for n,b in mod_resources.items() if n.lower().startswith('libs/tables/') and n.endswith('.xml')]
    mod_souls = {e.get('soul_name'): e for root in mod_tables for e in root.iter('soul')}
    roots = list(tables.values())

    def rows(tag, attribute, value):
        return [record(e) for root in roots for e in root.iter(tag) if e.get(attribute) == value]

    def faction_chain(name, extra):
        index = {}
        def walk(node, chain):
            if node.tag == 'Faction':
                chain = chain + [dict(node.attrib)]
                index[node.get('Name')] = chain
            for child in node:
                walk(child, chain)
        for root in roots + extra:
            tree = root.find('FactionTree')
            if tree is not None: walk(tree, [])
        return index.get(name, [])

    worlds = {}
    for key, pak in [('native', data/'Levels/trosecko/level.pak'),
                     ('imported', mod/'Data/Levels/kcd1_travel/level.pak')]:
        worlds[key] = [ET.fromstring(resource(pak, path)) for path in
                       ('objects_mission0.xml', 'whdata_0', 'tables/ai/scheduler.xml', 'waitinglinks.xml')]
        mission = worlds[key][0]
        seen = {e.get('EntityGuid') for e in mission}
        with zipfile.ZipFile(pak) as archive:
            for path in archive.namelist():
                if path.replace('\\','/').lower().startswith('layers/') and path.lower().endswith('.xml'):
                    blob=read(archive, path)
                    evidence[str(pak)+'::'+path]=hashlib.sha256(blob).hexdigest()
                    for entity in ET.fromstring(blob).iter('Entity'):
                        if entity.get('EntityGuid') not in seen:
                            mission.append(entity);seen.add(entity.get('EntityGuid'))

    def snapshot(name, kind):
        mission, wh, schedule, waiting = worlds[kind]
        entity = next(e for e in mission if e.get('Name') == name)
        by_id = {e.get('EntityId'): e for e in mission}
        by_guid = {str(guid_value(e.get('EntityGuid'))): e for e in mission if e.get('EntityGuid')}
        soul = (mod_souls if kind == 'imported' else native_souls)[name]
        scheduler = {r.get('EntityGuid'): r for r in schedule.find('Schedulers')}
        start = str(guid_value(entity.get('EntityGuid')))
        pending, attached, compiled = [start], {}, {}
        # Follow scheduler targets/delegates transitively; direct entity links
        # get full target records without recursively importing the whole world.
        while pending:
            guid = pending.pop()
            if guid in compiled: continue
            row = scheduler.get(guid)
            compiled[guid] = record(row)
            attached[guid] = record(by_guid.get(guid))
            if row is not None:
                for link in row.iter('S_ActivityLink'):
                    pending += [link.get(k) for k in ('TargetGuid', 'PositioningDelegate')
                                if link.get(k) not in (None, '0') and link.get(k) not in compiled]
        direct = []
        for link in entity.findall('EntityLinks/Link'):
            target = by_id.get(link.get('TargetId'))
            direct.append(dict(link=record(link), target=record(target)))
        brain = soul.get('brain_id')
        components = rows('brain2subbrain', 'brain_id', brain)
        component_ids = {r['attributes']['subbrain_id'] for r in components}
        related = {}
        for path, root in tables.items():
            if '/ai/' not in path: continue
            matches = [record(e) for e in root.iter() if e.get('subbrain_id') in component_ids
                       and e.get('brain_id', brain) == brain]
            if matches: related[path] = matches
        land = next(e for e in mission if e.get('Name') == 'sa_land')
        hosts = []
        for link in land.findall('EntityLinks/Link'):
            if link.get('Name') in ('mrkev', 'redkev'):
                hosts.append(dict(link=record(link), target=record(by_id.get(link.get('TargetId')))))
        character_name = soul.get('skald_character_name')
        characters = rows('skald_character','skald_character_name',character_name) if character_name else []
        voice_ids = {c['attributes'].get('voice_id') for c in characters}
        return dict(name=name, world=kind, entity=record(entity), soul=record(soul),
                    instance=[record(s) for s in wh.findall('./SoulList/Souls/Soul') if s.findtext('Name') == name],
                    archetype=rows('soul_archetype','soul_archetype_id',soul.get('soul_archetype_id')),
                    social_class=rows('social_class','social_class_id',soul.get('social_class_id')),
                    brain=rows('brain','brain_id',brain), brain_components=related,
                    skald_character=characters,
                    character_roles=rows('skald_character2role','skald_character_name',character_name) if character_name else [],
                    voice=[record(e) for root in roots for e in root.iter('voice') if e.get('voice_id') in voice_ids],
                    voice_group=rows('voice_group','name',soul.get('voice_group_name')) if soul.get('voice_group_name') else [],
                    faction_ancestry=faction_chain(soul.get('factionName'),mod_tables if kind == 'imported' else []),
                    direct_links=direct, scheduler_closure=compiled, scheduler_entities=attached,
                    world_interrupt_hosts=hosts, runtime_state_verified=False)

    selected = [('imported','gmtravel_return_driver'),('imported','rato_innkeeper1'),
                ('imported','rat_refugee_tereza'),('native','tsla_nomad'),
                ('native','ttac_procek'),('native','ttac_woman_1')]
    snapshots = {name:snapshot(name,kind) for kind,name in selected}
    for name, snap in snapshots.items():
        (output/(name+'.json')).write_text(json.dumps(snap,indent=2),encoding='utf-8')
    for name in ('rato_innkeeper1','rat_refugee_tereza'):
        person=resolve_person(kcd1,name)
        (output/(name+'-kcd1.json')).write_text(json.dumps(person['ai'],indent=2),encoding='utf-8')
    pairs = [('gmtravel_return_driver','rato_innkeeper1'),('gmtravel_return_driver','rat_refugee_tereza'),
             ('ttac_procek','rato_innkeeper1'),('ttac_woman_1','rat_refugee_tereza'),
             ('tsla_nomad','gmtravel_return_driver')]
    diffs = {a+'--'+b:compare(snapshots[a],snapshots[b]) for a,b in pairs}
    (output/'differences.json').write_text(json.dumps(diffs,indent=2),encoding='utf-8')
    for name,blob in mod_resources.items():
        if name.lower().startswith(('libs/storm/', 'libs/gluemapper/personimport/')):
            dest=output/'packaged-rules'/name
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(blob)
    # Preserve the actual native class defaults and Storm source definitions.
    # Explicit XML property omissions must not be treated as disabled defaults.
    for pak, paths in [
        ('Scripts.pak', ['Scripts/Entities/AI/NPC.lua', 'Scripts/Entities/AI/NPC_Female.lua',
                         'Scripts/Entities/actor/BasicActor.lua']),
        ('IPL_GameData.pak', ['Libs/Storm/storm.xml', 'Libs/Storm/common/base.xml',
                             'Libs/Storm/common/baseZones.xml', 'Libs/Storm/common/baseArchetypes.xml'])]:
        for path in paths:
            dest=output/'native-definitions'/path
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(resource(data/pak,path))
    stem='libs/storm/'
    storm=native_xml(resource(data/'IPL_GameData.pak','Libs/Storm/storm.xml'))
    rule_files={}
    for task in storm.findall('tasks/task'):
        if task.get('name') not in ('roles','contexts','abilities'): continue
        for source in task.findall('source'):
            path=stem+source.get('path').replace('\\','/')
            blob=resource(data/'IPL_GameData.pak',path)
            dest=output/'native-rules'/path
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(blob)
            rule_files[path]=dict(task=task.get('name'),rules=record(native_xml(blob)))
    (output/'native-storm-rules.json').write_text(json.dumps(rule_files,indent=2),encoding='utf-8')
    (output/'provenance.json').write_text(json.dumps(dict(resources=evidence,
        scope='Packaged registration only; active runtime contexts, roles, behaviors and save overrides are not inferred.'),indent=2),encoding='utf-8')
    print('Wrote six native/imported snapshots, two original source snapshots and five field comparisons to',output)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('kcd1','kcd2','mod','output'): parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();audit(args.kcd1,args.kcd2,args.mod,args.output)
