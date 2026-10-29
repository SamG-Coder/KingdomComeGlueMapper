"""Ship a selected original invitation and its source-authored departure.

Combat training remains a separate conversion. This stage does not complete
the lesson, give rewards, or relocate an actor to an invented starting point.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import uuid
import xml.etree.ElementTree as E
import zipfile

from campaign_quest_graph import xml, edge, constant, all_of, any_of, emit_quest
from campaign_quest_start_placement import load, unique
from campaign_entity_links import native_guid, guid_value, append_links
from upgrade_map import read

RECORD = 'quest-invitation.json'
# Explicit shipped slice; semantics, poses, text, actions and movement are imported.
SELECTION = ('q_tutorial_swordOne', 'vanek_walksToTiltyard', '20281')


def graph_digest(node):
    from character_city_benches import signature
    return hashlib.sha256(repr(signature(node)).encode()).hexdigest()


def bind_quest(entry, quest_blob, source, intro_stem, departure_stem):
    """Connect persistent source state to existing parent state and native work."""
    q = E.fromstring(quest_blob).find('Skald/Quest').get('Name')
    host = entry.find('Skald/Level'); nodes = host.find('Nodes')
    if nodes.find(q) is not None: raise ValueError('Invitation quest already registered')
    E.SubElement(host.find('Definitions'), 'Definition', File='../gm_imported_scene/'+q+'.xml')
    child = E.SubElement(nodes,q,Name=q)
    intro = unique(nodes.findall("EnableBehavior[@Name='"+intro_stem+"_enable']"), 'invitation introduction')
    prior = unique(intro.findall("Edge[@To='IsActive']"), 'intro activation')
    old_active = prior.get('From'); intro.remove(prior)
    objective = source['objective']
    departed = any_of(nodes,departure_stem+'_departed',
        [q+'.'+objective+'_Started', q+'.'+objective+'_Completed'])
    pending = E.SubElement(nodes,'Function',Name=departure_stem+'_not_departed',
        MethodName='math::boolean::Not',DeclaringType='math::boolean')
    edge(pending,departed,'Value')
    intro_active = all_of(nodes,departure_stem+'_intro_active',[old_active,pending.get('Name')+'.bool'])
    edge(intro,intro_active,'IsActive')
    from campaign_intro_preparation import rebind
    rebind(entry,intro_stem,old_active,intro_active)
    # Parent activation is the same source condition already compiled for the
    # introduction. A Watch reconciles saved state without a new-game-only hook.
    parent = source['parent_quest']
    ready = all_of(nodes,departure_stem+'_ready',[parent+'.started',parent+'.'+source['parent_objective']+'_Started'])
    watch = E.SubElement(nodes,'Watch',Name=departure_stem+'_watch',TypeT='bool')
    edge(watch,ready,'WatchedState'); edge(watch,parent+'.started','IsActive')
    gate = E.SubElement(nodes,'If',Name=departure_stem+'_start')
    for event in (watch.get('Name')+'.OnChanged',parent+'.source_state_changed'): edge(gate,event,'Exec')
    not_started = E.SubElement(nodes,'Function',Name=departure_stem+'_not_started',
        MethodName='math::boolean::Not',DeclaringType='math::boolean')
    edge(not_started,q+'.started','Value')
    edge(gate,all_of(nodes,departure_stem+'_can_start',[ready,not_started.get('Name')+'.bool']),'Condition')
    order = E.SubElement(nodes,'TriggerSequence',Name=departure_stem+'_initialize')
    edge(order,gate.get('Name')+'.True','Exec')
    edge(child,order.get('Name')+'.A','activate'); edge(child,order.get('Name')+'.B','start')
    enable = E.SubElement(nodes,'EnableBehavior',Name=departure_stem+'_enable',Signature=departure_stem,EventSet='resultNotification')
    constant(enable,'Behavior','departure'); constant(enable,'ForceKick','true')
    for param,asset in [('NPC',intro_stem+'_actor'),('SmartEntity',departure_stem+'_host'),('workPlace',departure_stem+'_place')]:
        E.SubElement(enable,'Asset',Name=param,Alias=asset)
    edge(enable,q+'.'+objective+'_Started','arrivalPending')
    edge(enable,all_of(nodes,departure_stem+'_active',[ready,q+'.started',departed]),'IsActive')
    edge(child,enable.get('Name')+'.Success','complete_'+objective)
    return entry


def validate(package):
    from setup_gameplay import ENTRY
    report=json.loads((package/RECORD).read_text(encoding='utf-8'))
    with zipfile.ZipFile(package/'Data/gluemappertravel.pak') as z:
        for name,digest in report['resources'].items():
            if hashlib.sha256(read(z,name)).hexdigest()!=digest:
                raise ValueError('Invitation resource differs: '+name)
        entry=E.fromstring(read(z,ENTRY))
        if entry.find(".//EnableBehavior[@Name='"+report['stem']+"_enable']") is None:
            raise ValueError('Invitation departure is not scheduled')
        if entry.find('.//Nodes/'+SELECTION[0]) is None:
            raise ValueError('Invitation source quest is not registered')
        for name,digest in report['graph_nodes'].items():
            node=unique(entry.findall(".//Nodes/*[@Name='"+name+"']"),'invitation graph node '+name)
            if graph_digest(node)!=digest:raise ValueError('Invitation binding differs: '+name)
    return report


def upgrade(package, source, target, *, manifest=None):
    from setup_campaign import digest
    if (package/RECORD).exists():
        validate(package)
        return repair_journal(package, source)
    if source is None or target is None: raise ValueError('Both games are required for source invitations')
    from setup_gameplay import PROJECT, ENTRY, LEVEL, quest_model
    from setup_package_upgrade import rewrite
    from campaign_intro_departure import plan, emit
    from campaign_behavior import native_catalog
    from campaign_behavior_schedule import connect, validate_compiled
    from character_person_world import native_wait_activity
    from character_shop_guard_work import native_contract, SOURCE_TEMPLATE
    from lean_alignment import native_placement
    from build_actor_dialogues_test import build as dialogues
    from build_recipe import apply_overlay
    from campaign_combat_work import validate as validate_ai
    from seating_animations import install, HELPERS, ANIMATIONS
    quest, tree, topic = SELECTION
    intro=json.loads((package/'quest-introductions.json').read_text(encoding='utf-8'))
    intro_stem='gm_intro_'+quest+'_'+intro['source_plan']['tree']
    stem='gm_invitation_'+quest
    with tempfile.TemporaryDirectory(prefix='invitation-',dir=package.parent) as temporary:
        work=Path(temporary)
        model=quest_model(source,work/'quest',quest)
        p=plan((work/'quest/behavior.xml').read_bytes(),tree)
        if p['actor_role'] != intro['source_plan']['actor_role']:
            raise ValueError('Invitation departure targets a different source actor')
        p.update(parent_quest=intro['source_plan']['parent_quest'],parent_objective=intro['source_plan']['objective'])
        placement=load(source,'rataje',[(quest,intro['source_plan']['actor_role'],p['place_role'])])[0]
        point=None
        with zipfile.ZipFile(source/'Data/Levels/rataje/level.pak') as z:
            for n in z.namelist():
                if n.lower()=='objects_mission0.xml' or n.lower().startswith('layers/') and n.lower().endswith('.xml'):
                    for e in E.fromstring(read(z,n)).iter('Entity'):
                        if e.get('EntityGuid')==placement['source_place_guid']: point=copy.deepcopy(e)
        props=None if point is None else point.find('Properties')
        if (props is None or props.get('guidSmartObjectType')!=SOURCE_TEMPLATE
                or props.get('soclasses_SmartObjectHelpers')!='LeaningSpot'
                or props.find('Script') is None or props.find('Script').get('Misc')!='leanBack:true|leanLeft:false|leanRight:false'):
            raise ValueError('Departure destination requires another native stance adapter')
        point.attrib.update(placement['placement'])
        point=native_placement(point,source,target,'leaning_back')
        native_props,_=native_contract(target)
        travel='Data/gluemappertravel.pak'; level='Data/Levels/kcd1_travel/level.pak'
        with zipfile.ZipFile(package/travel) as z:
            project_root=E.fromstring(read(z,PROJECT));entry=E.fromstring(read(z,ENTRY))
            parent_blob=read(z,'Quests/GlueTravel/gm_imported_scene/'+p['parent_quest']+'.xml')
        progress=unique({s.get('TypeT') for s in E.fromstring(parent_blob).iter('State')
                        if s.get('TypeT','').endswith('ObjectiveProgress')},'source objective progress type')
        quest_blob,quest_report=emit_quest(model,progress)
        # Later lesson timeout/workers are reported rather than pretending to
        # implement the combat tutorial through the invitation stage.
        files=emit(stem,p,native_catalog(target));validate_ai(files,target)
        files['Quests/GlueTravel/gm_imported_scene/'+quest+'.xml']=quest_blob
        bind_quest(entry,quest_blob,p,intro_stem,stem)
        with zipfile.ZipFile(package/level) as z:
            level_files={n:read(z,n) for n in ('objects_mission0.xml','waitinglinks.xml','tables/ai/scheduler.xml',HELPERS,ANIMATIONS)}
        mission=E.fromstring(level_files['objects_mission0.xml']);scheduler=E.fromstring(level_files['tables/ai/scheduler.xml'])
        project=project_root.find('Skald/Project')
        project_entity=unique((e for e in mission if e.get('Name')==project.get('Name')),'project entity')
        actor=unique((e for e in mission if e.get('EntityGuid')==placement['actor_guid']),'invitation actor')
        additions=[]
        def add(name,cls,props,pose):
            guid=native_guid(uuid.uuid5(uuid.NAMESPACE_URL,name).int & ((1<<64)-1))
            if any(e.get('EntityGuid')==guid for e in mission):raise ValueError('Invitation entity collision')
            entity=E.SubElement(mission,'Entity',Name=name,EntityClass=cls,EntityGuid=guid,
                EntityId=str(max(int(e.get('EntityId','0')) for e in mission)+1),**pose)
            entity.append(copy.deepcopy(props));return entity
        spot=add(stem+'_place','DetailMovementSmartObject',native_props,{k:point.get(k) for k in ('Pos','Rotate')})
        E.SubElement(scheduler.find('Schedulers'),'C_SmartHub',EntityGuid=str(guid_value(spot.get('EntityGuid'))),IgnoreDeadEnds='false',StupidHub='false')
        template=str(uuid.uuid5(uuid.NAMESPACE_URL,stem+'/template'))
        host=add(stem+'_host','SmartObjectHolder',E.Element('Properties',guidSmartObjectType=template,bSaved_by_game='0'),placement['placement'])
        for entity in (spot,host):
            alias=entity.get('Name')
            E.SubElement(project.find('Assets'),'SmartObjectAsset',Name=alias)
            label="asset['"+alias+"']"
            E.SubElement(project_entity.find('EntityLinks'),'Link',Name=label,TargetId=entity.get('EntityId'),TargetGuid=entity.get('EntityGuid'))
            additions.append((project_entity.get('EntityGuid'),entity.get('EntityGuid'),label))
        activity,_=native_wait_activity(target)
        connect(mission,additions,actor,host,'departure',p['priority'],p['speed'],stem,scheduler,activity)
        # Quest availability invalidates either activity immediately at the
        # authored handoff; retaining the sitting request would block departure.
        for link in scheduler.findall('.//S_ActivityLink'):
            params=link.find('Parameters')
            if params is not None and link.get('TargetGuid') in {str(guid_value(host.get('EntityGuid'))),
                    str(guid_value(unique((e for e in mission if e.get('Name')==intro_stem+'_host'),'intro host').get('EntityGuid')))}:
                params.set('HaltOnInvalid','true')
        table=E.Element('database',name='barbora')
        t=E.SubElement(E.SubElement(table,'SmartEntitys',version='1'),'SmartEntityTemplate',DatabaseId=template,Name=stem,UpdatePriority='false')
        b=E.SubElement(E.SubElement(t,'BehaviorTemplates'),'SmartBehaviorTemplate',Name='departure',InitialState='Disabled',MaxInstances='1')
        E.SubElement(b,'TreeLocation',FileName=stem+'.xml',TreeName='departure')
        files['Libs/Tables/ai/smartEntity/SmartEntity__'+stem+'.xml']=xml(table)
        validate_compiled(mission,scheduler,additions)
        level_files.update({'objects_mission0.xml':xml(mission),'tables/ai/scheduler.xml':xml(scheduler),
            'waitinglinks.xml':append_links(level_files['waitinglinks.xml'],additions)})
        with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as z:level_files,_=install(level_files,z)
        for n in ('tables/ai/scheduler.xml',HELPERS,ANIMATIONS):files['Mods/kingdomcomegluemapper/Data/Levels/kcd1_travel/'+n]=level_files[n]
        resources={n:hashlib.sha256(b).hexdigest() for n,b in files.items() if not n.startswith('Mods/')}
        files.update({PROJECT:xml(project_root),ENTRY:xml(entry)})
        rewrite(package,travel,files);rewrite(package,level,level_files)
        if not (package/'mod.manifest').exists():
            if manifest is None:raise ValueError('Invitation requires the package manifest')
            (package/'mod.manifest').write_bytes(manifest.read_bytes())
        quest_model(source,work/'parent',p['parent_quest'])
        dialogues(SimpleNamespace(source=source,target=target,mod=package,output=work/'speech',
            quest_ir=[work/'quest/quest-ir.json',work/'parent/quest-ir.json'],start_topic=[topic],namespace=stem+'_speech',
            kind='FaderDialog',container=ENTRY.removeprefix('Quests/'),container_name=LEVEL,
            activation=p['parent_quest']+'.'+p['parent_objective']+'_Started',skill_upgrade=[],participant_asset=[]))
        apply_overlay(package,work/'speech/package',consume=True)
        with zipfile.ZipFile(package/travel) as z:
            for n in z.namelist():
                if n.startswith('Quests/') and n.endswith('.xml') and '/'+stem+'_speech_' in n:
                    resources[n]=hashlib.sha256(read(z,n)).hexdigest()
            final_entry=E.fromstring(read(z,ENTRY))
        if not any('/'+stem+'_speech_' in n for n in resources):
            raise ValueError('Invitation dialogue was not emitted')
        graph_nodes={n.get('Name'):graph_digest(n) for n in final_entry.findall('.//Nodes/*')
            if n.get('Name','').startswith(stem+'_') or n.get('Name') in (quest,intro_stem+'_enable')}
        report=dict(stem=stem,source=p,placement=placement,resources=resources,quest_conversion=quest_report,
                    graph_nodes=graph_nodes,runtime_verified=False,scope='Source invitation and departure; combat lesson is not enabled')
        (package/RECORD).write_text(json.dumps(report,indent=2),encoding='utf-8')
    changes={n:digest(package/n) for n in (travel,level,RECORD,'Localization/English.pak','Localization/English_xml.pak','Data/gluemapper_dialogue_facials.pak')}
    localized, report = repair_journal(package, source, model=model)
    changes.update(localized)
    return changes,report


def repair_journal(package, source, *, model=None):
    """Migrate missing invitation strings and retain all existing graph bindings."""
    from campaign_quest_text import load as load_text, update_journal, localization_table
    from build_english_audio_test import merge_audio_strings
    from setup_campaign import digest
    from setup_package_upgrade import rewrite
    report = validate(package)
    path = 'Quests/GlueTravel/gm_imported_scene/'+SELECTION[0]+'.xml'
    travel, locale = 'Data/gluemappertravel.pak', 'Localization/English_xml.pak'
    if report.get('journal_localization'):
        with zipfile.ZipFile(package/locale) as archive:
            rows = E.fromstring(read(archive,'text_ui_quest.xml'))
        values = {r.findtext('Cell'):r.findall('Cell')[2].text for r in rows.findall('Row')}
        if any(values.get(r['key']) != r['text'] for r in report['quest_conversion']['journal_strings']):
            raise ValueError('Invitation journal strings are missing or changed')
        return {},report
    if source is None: raise ValueError('KCD1 is required to restore invitation journal text')
    if model is None:
        from quest_import import import_quest
        from campaign_sources import RetailSourceReader
        with RetailSourceReader(source) as reader:
            model, _ = import_quest(Path(source), SELECTION[0], source_reader=reader)
    localized, evidence = load_text(source,[model])
    journal = bool(model['tables']['quest2skald_subchapter']['rows'])
    if evidence['missing'] or journal and not localized[model['quest']].get('name'):
        raise ValueError('Missing source invitation journal text')
    evidence['journal_visible'] = journal
    with zipfile.ZipFile(package/travel) as archive:
        blob, records = update_journal(read(archive,path), model, localized[model['quest']])
    with zipfile.ZipFile(package/locale) as archive:
        strings = merge_audio_strings(read(archive,'text_ui_quest.xml'),localization_table(records))
    rewrite(package,travel,{path:blob})
    rewrite(package,locale,{'text_ui_quest.xml':strings})
    report['resources'][path] = hashlib.sha256(blob).hexdigest()
    report['quest_conversion']['journal_strings'] = records
    report['journal_localization'] = evidence
    pending = (package/RECORD).with_suffix('.json.pending')
    pending.write_text(json.dumps(report,indent=2),encoding='utf-8')
    pending.replace(package/RECORD)
    return {n:digest(package/n) for n in (travel,locale,RECORD)},validate(package)
