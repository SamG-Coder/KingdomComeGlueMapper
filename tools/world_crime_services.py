"""Bind native crime services to an imported, authored district and stocks site."""
import copy
import math
import xml.etree.ElementTree as E

from campaign_entity_links import append_links, entity_link, guid_value
from campaign_trigger_areas import read_areas, write_areas, TriggerArea
from character_crime_districts import contains
from cinematic_scene import SceneIndex, convert, guid_key
from cinematic_scene_profiles import register
from region_travel import guid
from upgrade_map import read, xml

SCENES = ('cin_s9906t_crime__whipping_troskovice', 'cin_s9909t_crime__branding_troskovice')


def vector(entity): return tuple(map(float, entity.get('Pos').split(',')))
def numbers(values): return ','.join(format(v,'.12g') for v in values)


def area_geometry(entity, compiled):
    # Native SmartAreaShape volumes are inline. The package normalizer removes
    # their duplicate fubar entries; TriggerArea volumes remain compiled.
    if entity.find('Area') is not None:
        from character_person_world import source_home_geometry
        probe=copy.deepcopy(entity)
        probe.set('EntityGuid',format(guid_value(entity.get('EntityGuid')),'x'))
        return source_home_geometry(probe)
    return compiled[guid_value(entity.get('EntityGuid'))]


def one(items, description):
    items=list(items)
    if len(items)!=1: raise ValueError(f'Expected one {description}; found {len(items)}')
    return items[0]


def choose_site(files, stock_records, resources):
    """Resolve by imported district polygon, source asset family and live roles."""
    mission=E.fromstring(files['objects_mission0.xml'])
    areas={a.guid:a for a in read_areas(files['triggerareas.fubar'])['areas']}
    districts=[e for e in mission if e.find("Properties[@Label='crimeDistrict']") is not None]
    # Every enabled district must get a deliberate site. Do not silently send
    # another town's offenders to the first available settlement.
    district=one(districts,'imported crime district for this punishment service')
    shape=area_geometry(district,areas)
    site=one((s for s in stock_records if s['path'].lower().endswith('/pillory/pillory.cgf')
              and contains(shape,s['position'])),'authored intact stocks inside the imported district')
    replaced=[s for s in stock_records if s['layer']==site['layer'] and
              s['path'].lower().endswith(('/pillory/pillory.cgf','/pillory/pillory_log.cgf')) and
              all(abs(a-b)<.0003 for a,b in zip(s['transform'],site['transform']))]
    if len(replaced)!=2: raise ValueError('Expected authored stocks and log pair')
    rows={}
    for path,blob in resources.items():
        if '/soul__' in path.lower():
            for s in E.fromstring(blob).iter():
                if s.get('soul_id'):
                    key=s.get('soul_id')
                    if key in rows and rows[key]!=s.attrib: raise ValueError('Conflicting local soul definition')
                    rows[key]=dict(s.attrib)
    # Social-class IDs come from this target's definitions, not source numeric IDs.
    classes={s.get('social_class_id') for path,blob in resources.items() if '/social_class' in path.lower()
             for s in E.fromstring(blob).iter() if s.get('soul_crime_role_id')=='2'}
    if not classes: raise ValueError('Missing native crime-authority social-class definitions')
    registrations={s.findtext('EntityGuid'):s.findtext('SharedSoulGuid') for s in
                   E.fromstring(files['whdata_0']).findall('SoulList/Souls/Soul')}
    guards=[e for e in mission if e.get('EntityClass')=='NPC' and e.get('Pos') and contains(shape,vector(e))
            and rows.get(registrations.get(e.get('EntityGuid')),{}).get('social_class_id') in classes]
    if not guards: raise ValueError('Imported punishment district has no registered local authority')
    locations={s.get('location_id') for path,blob in resources.items() if '/location__' in path.lower()
               for s in E.fromstring(blob).iter('location') if s.get('ui_type')=='Common'}
    location_entities=[e for e in mission if e.find('Properties') is not None and
        e.find('Properties').get('guidLocationId') in locations
        and contains(area_geometry(e,areas),site['position'])]
    location=one(location_entities,'registered settlement location at the stocks').find('Properties').get('guidLocationId')
    from character_factions import LEAF
    local_factions={LEAF+r.get('faction_id') for r in E.fromstring(resources['source/faction.xml']).iter('row')
                    if r.get('location_id')==location}
    guards=[e for e in guards if rows[registrations[e.get('EntityGuid')]]['factionName'] in local_factions]
    if not guards: raise ValueError('No local authority belongs to the source settlement faction')
    faction=one({rows[registrations[e.get('EntityGuid')]]['factionName'] for e in guards},'local settlement authority faction')
    guard=min(guards,key=lambda e:(math.dist(vector(e),site['position']),e.get('EntityGuid')))
    return dict(site=site,replaced=replaced,district=district.get('EntityGuid'),
                guard=guard.get('EntityGuid'),guards=[e.get('EntityGuid') for e in guards],faction=faction,location=location)


def prepare(files, native_archive, selection, height, cast_resources, native_cast_resources, *, native_height):
    files=dict(files);mission=E.fromstring(files['objects_mission0.xml'])
    original=E.fromstring(read(native_archive,'objects_mission0.xml'))
    native={e.get('EntityId'):e for e in original if e.get('EntityId')}
    index=SceneIndex.from_archive(native_archive)
    plans=[index.plan(n) for n in SCENES]
    from cinematic_scene_cast import bind_cast
    adapters,cast_report=bind_cast(index,plans,files,cast_resources,native_cast_resources,selection)
    anchor=one((index.entities[k] for k in plans[0]['entity_guids'] if
        index.entities[k].get('Geometry','').lower()=='objects/manmade/structures/municipal/law_and_order/pillory_b.cgf'),
        'native punishment stage')
    offset=tuple(a-b for a,b in zip(selection['site']['position'],vector(anchor)))
    from cinematic_scene_ground import combine
    adapters,ground_report=combine(index,plans,adapters,native_cast_resources,offset,native_height,height)
    byguid={e.get('EntityGuid'):e for e in mission if e.get('EntityGuid')}
    district,guard=(byguid[selection[k]] for k in ('district','guard'))
    world=one((e for e in mission if e.get('Name')=='open_world'),'local world controller')
    land=one((e for e in mission if e.get('Name')=='sa_land'),'local land')
    native_world=one((e for e in original if e.get('Name')=='open_world'),'native world controller')
    native_land=one((e for e in original if e.get('Name')=='sa_land'),'native land')
    native_area=one((e for e in original if e.find("Properties[@Label='crime_punishmentArea']") is not None),
                    'native reference punishment area')
    native_guard=one(native_area.findall("EntityLinks/Link[@Name='punishment_defaultGuard']"),'native fallback guard')
    bindings={native_world.get('EntityId'):world,native_land.get('EntityId'):land,native_guard.get('TargetId'):guard}
    for name in ('mrkev',):
        native_link=one(native_land.findall("EntityLinks/Link[@Name='"+name+"']"),'native '+name+' binding')
        local_link=one(land.findall("EntityLinks/Link[@Name='"+name+"']"),'local '+name+' binding')
        bindings[native_link.get('TargetId')]=byguid[local_link.get('TargetGuid')]
    roots=[native_area.get('EntityId')]
    world_links=[l for l in native_world.findall('EntityLinks/Link') if 'punishment_' in l.get('Name','') or l.get('Name')=='mrkev']
    land_links=[l for l in native_land.findall('EntityLinks/Link') if l.get('Name') in
                ('punishment_executionCH','punishment_fallbackArea','punishment_fader')]
    roots += [l.get('TargetId') for l in world_links+land_links]
    selected={};pending=list(roots)
    while pending:
        key=pending.pop()
        if key in bindings or key in selected: continue
        if key not in native: raise ValueError('Unresolved native crime service '+key)
        e=native[key]
        if e.get('EntityClass') not in ('TriggerArea','CutsceneHolder','TagPoint','IngameCutsceneData','SkipTimeCutsceneData','FastTravelCutsceneData'):
            raise ValueError('Crime service closure escaped into unrelated world '+str(e.attrib))
        selected[key]=e;pending += [l.get('TargetId') for l in e.findall('EntityLinks/Link')]
    next_id=max(int(e.get('EntityId','0')) for e in mission)+1
    owned=[];links=[]
    def add(template, identity, position=None):
        nonlocal next_id
        e=copy.deepcopy(template)
        for k in ('Layer','EditorLayer','Prefab','ParentId'):e.attrib.pop(k,None)
        for child in list(e):
            if child.tag not in ('Properties','Area'):e.remove(child)
        e.set('Name','gm_'+identity);e.set('EntityId',str(next_id));next_id+=1
        e.set('EntityGuid',guid('crime-services/'+identity))
        if e.get('EntityGuid') in byguid: raise ValueError('Crime service already registered '+identity)
        if position is not None:e.set('Pos',numbers(position))
        elif e.get('Pos'):e.set('Pos',numbers(a+b for a,b in zip(vector(e),offset)))
        mission.append(e);byguid[e.get('EntityGuid')]=e;owned.append(e.get('EntityGuid'));return e
    for key,e in selected.items(): bindings[key]=add(e,e.get('Name'))
    for key,e in selected.items():
        for l in e.findall('EntityLinks/Link'):entity_link(bindings[key],bindings[l.get('TargetId')],l.get('Name'),links)
    area=bindings[native_area.get('EntityId')]
    area.set('Pos',district.get('Pos'))
    shapes=read_areas(files['triggerareas.fubar']);district_shape=area_geometry(district,{a.guid:a for a in shapes['areas']})
    shapes['areas'].append(TriggerArea(guid_value(area.get('EntityGuid')),district_shape.height,district_shape.points))
    for origin,ls in ((world,world_links),(land,land_links)):
        for l in ls:entity_link(origin,bindings[l.get('TargetId')],l.get('Name'),links)
    entity_link(world,land,"asset['land']",links)
    entity_link(area,area,"gm_punishment_location['"+selection['location']+"']",links)
    # A translated return point can land below sloping source ground. Sample
    # the actual destination terrain; in-game navigation is verified separately.
    post=bindings[one(native_area.findall("EntityLinks/Link[@Name='punishment_teleportPoint']"),'native return point').get('TargetId')]
    x,y,_=vector(post);post.set('Pos',numbers((x,y,float(height(x,y))+.15)))
    # Native storage mechanics need an actual persistent Stash, not a dummy
    # SmartObject. Use a shipped chest model with no inherited loot or key.
    chest_template=next(e for e in original if e.get('EntityClass')=='Stash' and e.find('Properties') is not None
                        and 'chest' in e.find('Properties').get('object_Model','').lower())
    x,y,z=selection['site']['position'];storage_pos=(x+4,y+2,float(height(x+4,y+2))+.05)
    chest=add(chest_template,'crime_confiscated_storage',storage_pos)
    chest.set('Rotate','1,0,0,0')
    props=chest.find('Properties');model=props.get('object_Model');props.clear()
    props.attrib.update(object_Model=model,bOwnedByHome='0',esChestContextLabel='empty',
        sPickPlaceAnimTag='slaveChest+pickPlaceChest02',sWH_AI_EntityCategory='Chest')
    E.SubElement(props,'Database',sGeneratedInventory='inventory_empty',nRestockPeriodDays='0',nDestockPeriodDays='0')
    E.SubElement(props,'Lock',bLocked='1',bCanLockPick='1',bLockDifficultyOverride='1',fLockDifficulty='0.8',guidItemClassId='')
    entity_link(district,chest,'crime_stolenItemsStorageChest',links)
    # Permanent native stage/default stocks; cinematic tracks reference these
    # same entities so playback can hide/animate them and restore their state.
    stage=add(anchor,'crime_native_stage')
    persistent={guid_key(anchor.get('EntityGuid')):stage}
    for key in plans[0]['external_entities']:
        e=index.entities[key]
        if '/pillory_default/' in e.get('EditorLayer',''):
            persistent[key]=add(e,'crime_default_'+key)
    prepared=[];scene_receipts=[]
    for plan in plans:
        layer='gm_'+plan['name']
        data,receipt=convert(index,plan,offset=offset,identity=guid,first_id=next_id,layer=layer,source_game='kcd2',
            bindings={k:v for k,v in persistent.items() if k in plan['entity_guids']},entity_adapter=adapters[plan['name']],
            static_bindings={guid_key(anchor.get('EntityGuid'))} & set(plan['entity_guids']))
        next_id+=receipt['emitted_entities']
        prepared.append((data,receipt,one(plan['owner_profiles'],'cinematic owning profile')))
        scene_receipts.append(receipt)
    for e in bindings.values():
        props=e.find('Properties')
        if e.get('EntityClass')=='IngameCutsceneData':
            props.set('esGameProfile','gm_'+props.get('esGameProfile'))
    files['objects_mission0.xml']=xml(mission)
    files['waitinglinks.xml']=append_links(files['waitinglinks.xml'],links)
    files['triggerareas.fubar']=write_areas(shapes['areas'],shapes['export_version'])
    files,profiles=register(files,prepared,E.fromstring(read(native_archive,'whdata_1')),read(native_archive,'triggerareas.fubar'))
    return files,dict(entities=owned,service_links=links,scene_profiles=profiles,scenes=scene_receipts,
        native_translation=offset,selection=selection,return_point=vector(post),storage=chest.get('EntityGuid'),
        native_authority_entry_exit=True,cast=cast_report,ground_fit=ground_report,runtime_verified=False)
