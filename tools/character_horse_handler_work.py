"""Source-linked stable hay work; other stable phases remain explicit pending work."""
import copy
import hashlib
import math
import re
import uuid
import xml.etree.ElementTree as E
import zipfile
from pathlib import Path
from campaign_entity_links import append_links, guid_value, native_guid
from character_person import unique, resolve_link_targets, world_entity
from character_station_work import unquote
from character_job_items import register_item, bind_reservation
from upgrade_map import read, xml

SOURCE = ('final/sa_stable.xml', 'horseHandler_work')
CART = '47195f56-b033-08b1-41e9-7fc78aaca8ab'
FORK = '4d45f922-7974-4852-8465-79fa2f67e0c2'
TEMPLATE = str(uuid.uuid5(uuid.NAMESPACE_URL, 'kingdomcomegluemapper/stable/hay-unloading/v1'))
FRAGMENTS = ('HayUnloadWagonIn', 'HayUnloadWagon', 'HayUnloadWagonOut')
PENDING = ['water refill', 'grain feeding', 'horse assignment and care', 'hay phase-item visual']


def work_contact(anchor, animation):
    """Compose the exported animation start pose with its source alignment frame.

    An aligned fragment's origin is not the actor's feet. The native action
    table stores translation followed by quaternion XYZW; level XML uses WXYZ.
    Never substitute a tool-storage position for this animation transform.
    """
    from character_fence_work import placement
    values = tuple(map(float, animation['init_align0'].split(';')))
    if len(values) != 7 or not all(map(math.isfinite, values)):
        raise ValueError('Invalid native stable animation start transform')
    x, y, z, qx, qy, qz, qw = values
    if abs(sum(v*v for v in (qw,qx,qy,qz)) - 1) > 1e-4:
        raise ValueError('Invalid native stable animation rotation')
    helper = E.Element('Helper', pos=','.join(map(str,(x,y,z))),
                       rot=','.join(map(str,(qw,qx,qy,qz))))
    return placement(E.Element('Entity', anchor), helper)


def supports(entry):
    b=entry['behavior']
    return (b['tree_file'].replace(chr(92),'/').lower(),b['tree_name'])==SOURCE


def source_contract(blob):
    root=E.fromstring(blob).find("BehaviorTree[@name='horseHandler_work']/Root")
    if root is None: raise ValueError('Missing source stable work')
    expressions=[unquote(e.get('expressions','')) for e in root.iter('Expression')]
    if not any("$behaviors[1] = 'unloadHay'" in e for e in expressions):
        raise ValueError('Source stable does not select hay unloading')
    searches=[s for s in root.iter('GraphSearch') if any(unquote(f.get('Child',''))=='activityTarget' for f in s.findall('HasBehaviorFilter'))]
    if len(searches)!=1 or unquote(searches[0].get('Origin',''))!='__area.id' or unquote(searches[0].get('depth',''))!='1':
        raise ValueError('Unsupported stable resource search')


def resolve(entry, archive, person, source, reader, target):
    if not person or not reader or target is None: raise ValueError('Stable work needs source and target evidence')
    # The retained native fragments have only a male animation contract.
    if person['actor'].get('EntityClass')!='NPC': raise ValueError('Female stable hay animation is not yet supported')
    assets=reader(source,{'stable':('Scripts.pak','Libs/AI/'+SOURCE[0]),'helpers':('GameData.pak','Libs/SmartObjects.xml')})
    source_contract(assets['stable']['data'])
    area=E.Element('Entity',entry['area']);links=E.SubElement(area,'EntityLinks')
    for l in entry['target_links']:E.SubElement(links,'Link',l)
    entities=resolve_link_targets(archive,area,list(E.fromstring(read(archive,'objects_mission0.xml'))),{})
    byid={e.get('EntityId'):e for e in entities}
    carts=[byid[l.get('TargetId')] for l in links if byid[l.get('TargetId')].find('Properties') is not None and byid[l.get('TargetId')].find('Properties').get('guidSmartObjectType','').lower()==CART]
    cart=unique(carts,'authored stable hay cart')
    helper=unique([h for h in E.fromstring(assets['helpers']['data']).iter('Helper') if h.get('className')=='hayUnload' and h.get('name')=='unloadPoint'],'hay contact helper')
    from character_fence_work import placement
    pose=placement(world_entity(archive,cart,entities),helper)
    entities=resolve_link_targets(archive,cart,entities,{})
    byid={e.get('EntityId'):e for e in entities}
    slots=[byid[l.get('TargetId')] for l in cart.findall('EntityLinks/Link')]
    fork=unique([e for e in slots if e.get('EntityClass')=='ItemSlot' and e.find('Properties').get('guidItemClassId','').lower()==FORK],'source stable pitchfork')
    animations=native_fragments(target)
    arrival=work_contact(pose,animations[FRAGMENTS[0]])
    return dict(entry,arrival=arrival,horse_handler_work=dict(cart=dict(cart.attrib),
        alignment=pose, fork=E.tostring(fork,encoding='unicode')))


def native_fragments(target):
    """Require executable action rows as well as retained Mannequin fragments."""
    with zipfile.ZipFile(Path(target)/'Data/Animations.pak') as z:
        db=E.fromstring(read(z,'Animations/Mannequin/ADB/kcd_male_database.adb'))
    with zipfile.ZipFile(Path(target)/'Data/Tables.pak') as z:
        actions=E.fromstring(read(z,'Libs/Tables/action/actor_anim_action.xml'))
    result = {}
    for name, tags in zip(FRAGMENTS, ('', 'left+up', '')):
        nodes=list(db.iter(name))
        if len(nodes)!=1:
            raise ValueError('Missing native stable animation: '+name)
        fragments=[f for f in nodes[0].findall('Fragment')
                   if not f.get('Tags') and f.get('FragTags','') == tags
                   and f.find('AnimLayer/Animation') is not None]
        fragment=unique(fragments,'native stable animation variant: '+name)
        row=unique((a for a in actions.iter('actor_anim_action')
                    if a.get('mn_fragment_id') == name
                    and a.get('mn_tags','') == tags
                    and a.get('mn_fragment_guid','').lower() == fragment.get('GUID','').lower()
                    and a.get('player') == 'false' and a.get('mounted') == 'false'),
                   'native stable action: '+name)
        if row.get('isfullbody') != 'true' or row.get('isOneShot') != ('false' if tags else 'true'):
            raise ValueError('Native stable animation lifecycle changed: '+name)
        if row.get('init_align0') != row.get('init_align1'):
            raise ValueError('Native stable alignment requires variant selection: '+name)
        work_contact(dict(Pos='0,0,0',Rotate='1,0,0,0'),row.attrib)
        result[name]=dict(row.attrib)
    if len({row.get('actor_class_hash') for row in result.values()}) != 1:
        raise ValueError('Native stable fragments use different actor controllers')
    return result


def resources():
    table=E.Element('database',name='barbora')
    obj=E.SubElement(E.SubElement(table,'SmartEntitys',version='1'),'SmartEntityTemplate',DatabaseId=TEMPLATE,Name='gm_stable_hay',UpdatePriority='false')
    use=E.SubElement(E.SubElement(obj,'BehaviorTemplates'),'SmartBehaviorTemplate',Name='use',InitialState='Enabled',MaxInstances='1')
    E.SubElement(use,'TreeLocation',FileName='gluemapper/stable_hay.xml',TreeName='use')
    trees=E.Element('BehaviorTrees');tree=E.SubElement(trees,'BehaviorTree',name='use',is_function='0')
    variables=E.SubElement(tree,'Variables')
    for name in ('item','approach'):
        E.SubElement(variables,'Variable',name=name,type='_wuid',values='',isPersistent='0',form='single')
    root=E.SubElement(tree,'Root',OneTimeOnly='false',FailState='Recoverable',saveVersion='2')
    hand=E.SubElement(E.SubElement(root,'Behavior',canSkip='1'),'HandContentElement',hand='Right',item='$item',allowAny='false',allowHomeSlotAutoReturn='false',decisionLabel='stable_pitchfork',useDecision='true',enabled='true',saveVersion='2')
    E.SubElement(E.SubElement(hand,'Search',canSkip='1'),'ErrorNode',Message="'Missing stable tool reservation'")
    seq=E.SubElement(E.SubElement(hand,'Exec',canSkip='1'),'Sequence')
    # Resolve the separately registered start pose. Alignment still refers to
    # the source cart frame, as required by the native action's init_align data.
    search=E.SubElement(seq,'GraphSearch',Origin='$__object.id',Borders='',depth='1',
        selection='true',SearchPattern='Depth',EdgePruning="'parent,inventory'",
        AllowedEdges='',SubGraph='',AllowSubtraph='false',includeOrigin='false',
        excludeOrigin='false',failOnEmpty='true',SetOperationChoice='Intersection|And',
        errorOnEmpty='true',shortCircuit='true',skipTraversed='true')
    E.SubElement(search,'LinkTagFilter',tag="'stableAnimationStart'",prune='true',
        negprune='unknown',Parent='',Child='$approach',Data='')
    E.SubElement(seq,'ExactMove',directionType='AlignWithEntity',
        directionSpecification='$approach',animationTriggerDist='0.150000',
        precise='false',changeNPCState='true',fastForwardIncludesMove='false',
        destinationSpecification='$approach',destinationSpecification2='',
        destinationSpecification3='',speed='Walk',additionalParams='',
        pathFindingParams='',staminaPolicy='',pathInfo='')
    def action(parent,name,tags=''):
        return E.SubElement(parent,'AnimationAction',fragment="'"+name+"'",tags=("'"+tags.replace('+',',')+"'") if tags else '',eventReferenceObject='',
            alignObject='$__object.id',alignHelper='-2',context='',keepContext='false')
    action(seq,FRAGMENTS[0])
    parallel=E.SubElement(seq,'Parallel',successMode='Any',failureMode='Any')
    action(parallel,FRAGMENTS[1],'left+up')
    E.SubElement(parallel,'Wait',duration="'30s'",timeType='GameTime',doFail='false',variation='',skipInLOD='false')
    action(seq,FRAGMENTS[2])
    labels=E.Element('database',name='barbora')
    E.SubElement(E.SubElement(labels,'DecisionLabelDatabase',version='1'),
                 'DecisionLabel',Name='stable_pitchfork')
    return {'Libs/Tables/ai/smartEntity/SmartEntity__gm_stable_hay.xml':xml(table),
            'Libs/Tables/ai/DecisionLabelDatabase__gm_stable_hay.xml':xml(labels),
            'AI/gluemapper/stable_hay.xml':xml(trees)}


def refresh_existing(files, target):
    """Migrate the owned pre-approach graph before publishing its new behavior.

    Existing workers are deliberately excluded from the idle-to-work converter.
    Their behavior is nevertheless refreshed on every population upgrade. Older
    hay graphs therefore need the start point now required by that behavior.
    Only the generated cart/hub identities and the known old origin placement
    are accepted; unrelated resources and all activity clocks remain intact.
    """
    mission = E.fromstring(files['objects_mission0.xml'])
    carts = [e for e in mission if e.find('Properties') is not None
             and e.find('Properties').get('guidSmartObjectType') == TEMPLATE]
    if not carts:
        return dict(files), []
    animation = native_fragments(target)[FRAGMENTS[0]]
    scheduler = E.fromstring(files['tables/ai/scheduler.xml'])
    rows = scheduler.find('Schedulers')
    waiting = E.fromstring(files['waitinglinks.xml'])
    edges, report = [], []
    identity = lambda value: native_guid(int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'little'))

    def entity(guid):
        if not isinstance(guid, str):
            raise ValueError('Stable graph entity has no GUID')
        return unique((e for e in mission if e.get('EntityGuid') == guid), 'stable graph entity: ' + guid)

    def linked(owner, label):
        link = unique((l for l in owner.findall('EntityLinks/Link') if l.get('Name') == label),
                      'stable graph link: ' + label)
        node = entity(link.get('TargetGuid'))
        if node.get('EntityId') != link.get('TargetId'):
            raise ValueError('Stable graph target ID disagrees with its GUID')
        return node

    for cart in carts:
        guid = cart.get('EntityGuid')
        match = re.fullmatch(r'gm_stable_hay_([0-9a-f]{1,16})', cart.get('Name', ''))
        # Source uint64 text is retained in the hash but lowercased in the name.
        source_ids = [] if match is None else [value for value in {match[1], match[1].upper()}
                     if identity('stable-hay/' + value) == guid]
        if (len(source_ids) != 1 or cart.get('EntityClass') != 'SmartObjectHolder'
                or cart.find('Properties').attrib != dict(guidSmartObjectType=TEMPLATE, bSaved_by_game='0')):
            raise ValueError('Cannot refresh foreign stable cart')
        leaf = unique((r for r in rows if r.get('EntityGuid') == str(guid_value(guid))), 'stable cart scheduler')
        if leaf.tag != 'C_SmartHub' or len(leaf):
            raise ValueError('Cannot refresh changed stable cart scheduler')
        labels = [l.get('Name') for l in cart.findall('EntityLinks/Link')]
        reservation = "schedulerReservedItem['use','stable_pitchfork']"
        if labels.count(reservation) != 1 or any(label not in (reservation, 'stableAnimationStart') for label in labels):
            raise ValueError('Cannot refresh changed stable reservation graph')
        point = linked(cart, reservation)
        slot = linked(point, '')
        expected_point = identity('job-reservation/' + guid + '/stable_pitchfork/' + slot.get('EntityGuid'))
        if (point.get('EntityClass') != 'TagPoint' or point.get('EntityGuid') != expected_point
                or len(point.findall('EntityLinks/Link')) != 1 or slot.get('EntityClass') != 'ItemSlot'
                or slot.find('Properties') is None or not slot.find('Properties').get('guidItemClassId')):
            raise ValueError('Cannot refresh changed stable tool reservation')

        consumers = [(r, link) for r in rows for link in r.findall('Links/S_ActivityLink')
                     if link.get('TargetGuid') == str(guid_value(guid))]
        if not consumers:
            raise ValueError('Stable cart has no owned schedule consumer')
        hubs = []
        for row, activity in consumers:
            hub = entity(native_guid(int(row.get('EntityGuid'))))
            actor = unique((e for e in mission if e.get('EntityClass') == 'NPC'
                            and identity('schedule-arrival/' + e.get('EntityGuid', '') + '/' + source_ids[0])
                            == hub.get('EntityGuid')), 'owned stable worker')
            prefix = 'gm_schedule_' + actor.get('Name') + '_'
            parameters = activity.find('Parameters')
            if (hub.get('EntityClass') != 'SchedulerHub' or not hub.get('Name', '').startswith(prefix)
                    or not hub.get('Name')[len(prefix):].isdigit()
                    or hub.find('Properties') is None or hub.find('Properties').attrib != dict(bSaved_by_game='0')
                    or row.tag != 'C_SmartHub' or len(row) != 1 or len(row.findall('Links/S_ActivityLink')) != 1
                    or row.get('Position') != hub.get('Pos') or activity.get('PositioningDelegate') != '0'
                    or parameters is None or any(parameters.get(k) != v for k, v in
                       dict(BehaviorName='use', TimeOfDayStart='0', TimeOfDayEnd='1440').items())
                    or len(hub.findall('EntityLinks/Link')) != 1 or linked(hub, '_,use') is not cart):
                raise ValueError('Cannot refresh changed stable schedule hub')
            incoming = [(r, link) for r in rows for link in r.findall('Links/S_ActivityLink')
                        if link.get('TargetGuid') == row.get('EntityGuid')]
            if not incoming or any(r.get('EntityGuid') != str(guid_value(actor.get('EntityGuid')))
                    or link.find('Parameters') is None or link.find('Parameters').get('BehaviorName') is not None
                    for r, link in incoming):
                raise ValueError('Cannot refresh foreign stable schedule consumer')
            hubs.append((hub, row, actor))

        pose = work_contact(cart.attrib, animation)
        start_guid = identity('stable-start/' + guid)
        starts = [e for e in mission if e.get('EntityGuid') == start_guid]
        if len(starts) > 1:
            raise ValueError('Duplicate stable animation start')
        start = starts[0] if starts else None
        if start is not None and (start.get('EntityClass') != 'TagPoint'
                or start.get('Name') != cart.get('Name') + '_start'
                or start.get('Pos') != pose['Pos'] or start.get('Rotate', '1,0,0,0') != pose['Rotate']
                or start.findall('EntityLinks/Link')):
            raise ValueError('Conflicting stable animation start')
        for hub, row, actor in hubs:
            old_pose = (hub.get('Pos'), hub.get('Rotate', '1,0,0,0'))
            expected = (pose['Pos'], pose['Rotate'])
            if old_pose != expected and (start is not None or old_pose != (cart.get('Pos'), cart.get('Rotate', '1,0,0,0'))):
                raise ValueError('Cannot refresh unknown stable approach placement')
        authored = [l for l in cart.findall('EntityLinks/Link') if l.get('Name') == 'stableAnimationStart']
        if authored and (len(authored) != 1 or start is None
                or authored[0].get('TargetGuid') != start_guid or authored[0].get('TargetId') != start.get('EntityId')):
            raise ValueError('Conflicting stable animation start link')
        saved_edges = [r for r in waiting.findall('.//WaitingLink') if r.get('SourceId') == guid
                       and any(d.text == 'stableAnimationStart' for d in r)]
        if len(saved_edges) > 1 or any(r.get('TargetId') != start_guid for r in saved_edges):
            raise ValueError('Conflicting stable animation start waiting link')
        if start is None:
            start = E.SubElement(mission, 'Entity', Name=cart.get('Name')+'_start', EntityGuid=start_guid,
                EntityId=str(max(int(e.get('EntityId', '0')) for e in mission)+1), EntityClass='TagPoint',
                Pos=pose['Pos'], Rotate=pose['Rotate'])
            E.SubElement(start, 'Properties')
        if not authored:
            E.SubElement(cart.find('EntityLinks'), 'Link', Name='stableAnimationStart',
                         TargetId=start.get('EntityId'), TargetGuid=start_guid)
        edges.append((guid, start_guid, 'stableAnimationStart'))
        for hub, row, actor in hubs:
            changed = hub.get('Pos') != pose['Pos'] or hub.get('Rotate', '1,0,0,0') != pose['Rotate']
            hub.set('Pos', pose['Pos']); hub.set('Rotate', pose['Rotate']); row.set('Position', pose['Pos'])
            report.append(dict(person=actor.get('Name'), job='stable_hay_approach_refresh', extension_only=True,
                cart=guid, approach=start_guid, migrated=not authored or not saved_edges or changed,
                pending=list(PENDING), runtime_verified=False))
    result = dict(files)
    result['objects_mission0.xml'] = xml(mission)
    result['tables/ai/scheduler.xml'] = xml(scheduler)
    result['waitinglinks.xml'] = append_links(files['waitinglinks.xml'], edges)
    return result, report


def bind(mission,scheduler,person,entry,archive,target,registered):
    from character_schedule_world import ARRIVAL_TEMPLATE
    native_fragments(target)
    job=unique([e for e in mission if e.get('Name')=='gm_schedule_'+person+'_'+entry['arrival']['EntityId']],'stable arrival')
    rows=scheduler.find('Schedulers');number=str(guid_value(job.get('EntityGuid')))
    hub=unique([r for r in rows if r.get('EntityGuid')==number],'stable arrival scheduler')
    incoming=[l for r in rows for l in r.findall('Links/S_ActivityLink') if l.get('TargetGuid')==number]
    if job.find('Properties').get('guidSmartObjectType')!=ARRIVAL_TEMPLATE or len(hub) or not incoming or any(l.find('Parameters').get('BehaviorName')!='use' for l in incoming):
        raise ValueError('Cannot replace non-owned stable job')
    source=list(E.fromstring(read(archive,'objects_mission0.xml')))
    slot=E.fromstring(entry['horse_handler_work']['fork'])
    if not any(e.get('EntityId')==slot.get('EntityId') for e in source):source.append(slot)
    source=resolve_link_targets(archive,slot,source,{})
    placed=world_entity(archive,slot,source);source=[placed if e.get('EntityId')==slot.get('EntityId') else e for e in source]
    contract=dict(item_id=FORK,properties=E.Element('Properties'),behavior='use',decision='stable_pitchfork',definition="schedulerReservedItem['use','stable_pitchfork']",direct_links=[])
    tool,report=register_item(mission,placed,{e.get('EntityId'):e for e in source},contract,registered)
    # One source cart has one native exclusive resource shared by all workers.
    guid=native_guid(int.from_bytes(hashlib.sha256(('stable-hay/'+entry['horse_handler_work']['cart']['EntityGuid']).encode()).digest()[:8],'little'))
    matches=[e for e in mission if e.get('EntityGuid')==guid]
    alignment=entry['horse_handler_work']['alignment']
    props=E.Element('Properties',guidSmartObjectType=TEMPLATE,bSaved_by_game='0')
    if matches:
        field=unique(matches,'shared stable cart')
        if (field.get('EntityClass')!='SmartObjectHolder'
                or field.get('Pos')!=alignment['Pos']
                or field.get('Rotate','1,0,0,0')!=alignment.get('Rotate','1,0,0,0')
                or field.find('Properties') is None
                or E.tostring(field.find('Properties'))!=E.tostring(props)):
            raise ValueError('Conflicting stable cart')
        leaf=unique([r for r in rows if r.get('EntityGuid')==str(guid_value(guid))],'shared stable scheduler')
        if len(leaf):raise ValueError('Conflicting stable cart scheduler')
    else:
        field=E.SubElement(mission,'Entity',Name='gm_stable_hay_'+entry['horse_handler_work']['cart']['EntityGuid'].lower(),EntityGuid=guid,EntityId=str(max(int(e.get('EntityId','0')) for e in mission)+1),EntityClass='SmartObjectHolder',Pos=alignment['Pos'],Rotate=alignment.get('Rotate','1,0,0,0'))
        field.append(props)
        E.SubElement(rows,'C_SmartHub',EntityGuid=str(guid_value(guid)),IgnoreDeadEnds='false',StupidHub='false')
    waiting=bind_reservation(mission,field,tool,contract)
    start_guid=native_guid(int.from_bytes(hashlib.sha256(('stable-start/'+guid).encode()).digest()[:8],'little'))
    starts=[e for e in mission if e.get('EntityGuid')==start_guid]
    if starts:
        start=unique(starts,'stable animation start')
        if (start.get('EntityClass')!='TagPoint' or start.get('Pos')!=job.get('Pos')
                or start.get('Rotate')!=job.get('Rotate','1,0,0,0')):
            raise ValueError('Conflicting stable animation start')
    else:
        start=E.SubElement(mission,'Entity',Name=field.get('Name')+'_start',
            EntityGuid=start_guid,EntityId=str(max(int(e.get('EntityId','0')) for e in mission)+1),
            EntityClass='TagPoint',Pos=job.get('Pos'),Rotate=job.get('Rotate','1,0,0,0'))
        E.SubElement(start,'Properties')
        links=field.find('EntityLinks')
        if links is None: links=E.SubElement(field,'EntityLinks')
        E.SubElement(links,'Link',Name='stableAnimationStart',TargetId=start.get('EntityId'),TargetGuid=start_guid)
        waiting.append((guid,start_guid,'stableAnimationStart'))
    job.set('EntityClass','SchedulerHub');job.remove(job.find('Properties'));E.SubElement(job,'Properties',bSaved_by_game='0');hub.set('Position',job.get('Pos'))
    outgoing=E.SubElement(hub,'Links');use=copy.deepcopy(incoming[0]);use.set('TargetGuid',str(guid_value(guid)))
    use.find('Parameters').set('TimeOfDayStart','0');use.find('Parameters').set('TimeOfDayEnd','1440');outgoing.append(use)
    for l in incoming:l.find('Parameters').attrib.pop('BehaviorName')
    links=job.find('EntityLinks')
    if links is None:links=E.SubElement(job,'EntityLinks')
    elif len(links):raise ValueError('Stable arrival already has outgoing links')
    E.SubElement(links,'Link',Name='_,use',TargetId=field.get('EntityId'),TargetGuid=guid)
    waiting.append((job.get('EntityGuid'),guid,'_,use'))
    return job,waiting,dict(person=person,job='stable_hay_unloading',source_behavior=entry['behavior'],cart=entry['horse_handler_work']['cart'],tool=report,pending=PENDING,runtime_verified=False)
