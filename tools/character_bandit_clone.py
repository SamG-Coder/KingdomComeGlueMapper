"""Optional native-combat comparison actor with a source person's appearance.

Native RPG/equipment records are retained. A destination-local schedulerWait
replaces the template's camp schedule explicitly; no source-map jobs are run.
"""
import copy
import xml.etree.ElementTree as ET
import zipfile

from character_native_clone import identity
from character_person import native_xml, unique
from character_person_world import register_person_world
from campaign_entity_links import native_guid
from upgrade_map import read, xml
import uuid


def resolve_template(target, name, level='trosecko'):
    with zipfile.ZipFile(target/'Data/Tables.pak') as archive:
        matches=[]
        for path in archive.namelist():
            if path.lower().startswith('libs/tables/rpg/soul') and path.lower().endswith('.xml'):
                blob=read(archive,path)
                if name.encode() not in blob: continue
                matches.extend(copy.deepcopy(e) for e in native_xml(blob).iter('soul') if e.get('soul_name')==name)
        soul=unique(matches,'native combat soul')
    with zipfile.ZipFile(target/'Data/Levels'/level/'level.pak') as archive:
        matches=[]
        for path in archive.namelist():
            if path=='objects_mission0.xml' or path.startswith('layers/') and path.endswith('.xml'):
                blob=read(archive,path)
                if name.encode() in blob:
                    matches.extend(copy.deepcopy(e) for e in ET.fromstring(blob).iter('Entity') if e.get('Name')==name)
        actor=unique(matches,'native combat actor')
    # Exact-name rules must follow the new identity, otherwise template-specific
    # inventory and abilities silently disappear. Generic class/faction rules
    # continue to match the preserved native soul fields.
    rules={}
    with zipfile.ZipFile(target/'Data/IPL_GameData.pak') as archive:
        for path in archive.namelist():
            if not path.startswith('Libs/Storm/') or not path.endswith('.xml'):continue
            blob=read(archive,path)
            if name.encode() not in blob:continue
            for rule in native_xml(blob).findall('rules/rule'):
                selectors=rule.find('selectors')
                if selectors is not None and any(s.tag=='hasName' and s.get('name')==name for s in selectors):
                    if len(selectors)!=1:raise ValueError('Native clone needs compound selector translation: '+path)
                    task=path.split('/')[2]
                    rules.setdefault(task,[]).append(copy.deepcopy(rule))
    if not any(r.find('operations/setInventory') is not None for r in rules.get('equipment',[])):
        raise ValueError('Native combat template has no resolved equipment rule')
    return dict(name=name,soul=soul,actor=actor,rules=rules,level=level)


def resources(files, source, person, character, template, namespace):
    result=dict(files)
    soul=copy.deepcopy(template['soul'])
    soul.set('soul_name',namespace);soul.set('soul_id',identity(namespace))
    soul.set('soul_archetype_id',character['archetype'])
    root=ET.Element('database',name='barbora')
    ET.SubElement(root,'souls',version='2').append(soul)
    path='Libs/Tables/rpg/soul__'+namespace+'.xml'
    if path in result:raise ValueError('Combat clone already registered')
    result[path]=xml(root)
    storm=ET.fromstring(result['Libs/Storm/storm.xml'])
    rule_groups=copy.deepcopy(template['rules'])
    for task,operations in {
        'appearance':[('set'+k.title(),dict(name=v)) for k,v in character['appearance'].items()],
        'names':[('setUiName',dict(name=namespace+'_name'))],
    }.items():
        rule=ET.Element('rule',name=namespace+'_'+task)
        ET.SubElement(ET.SubElement(rule,'selectors'),'hasName',name=namespace)
        ops=ET.SubElement(rule,'operations')
        for tag,attrs in operations:ET.SubElement(ops,tag,attrs)
        rule_groups[task]=[rule]
    for task,rules in rule_groups.items():
        definition=storm.find(f"tasks/task[@name='{task}']")
        if definition is None:raise ValueError('Missing native Storm task '+task)
        path=task+'/'+namespace+'.xml'
        ET.SubElement(definition,'source',path=path)
        root=ET.Element('storm');container=ET.SubElement(root,'rules')
        for i,rule in enumerate(rules):
            rule.set('name',namespace+'_'+task+'_'+str(i))
            rule.find('selectors/hasName').set('name',namespace)
            container.append(rule)
        result['Libs/Storm/'+path]=xml(root)
    result['Libs/Storm/storm.xml']=xml(storm)
    with zipfile.ZipFile(source/'Localization/English_xml.pak') as archive:
        names=ET.fromstring(read(archive,'text_ui_soul.xml'))
    key=person['instance'].findtext('StaticData/NameStringId')
    row=copy.deepcopy(unique((r for r in names.findall('Row') if r.findtext('Cell')==key),'source name'))
    row.find('Cell').text=namespace+'_name';names=ET.Element('Table');names.append(row)
    return result,xml(names)


def register_world(files, source, target, template, namespace, position):
    files=dict(files)
    mission=ET.fromstring(files['objects_mission0.xml']);wh=ET.fromstring(files['whdata_0'])
    if any(e.get('Name')==namespace for e in mission):raise ValueError('Duplicate combat test actor')
    actor=copy.deepcopy(template['actor'])
    original_links=[dict(l.attrib) for l in actor.findall('EntityLinks/Link')]
    links=actor.find('EntityLinks')
    if links is not None:actor.remove(links)
    for key in ('Layer','EditorLayer'):actor.attrib.pop(key,None)
    guid=native_guid(int.from_bytes(uuid.UUID(identity(namespace)).bytes[:8],'little'))
    actor.attrib.update(Name=namespace,EntityGuid=guid,
        EntityId=str(max(int(e.get('EntityId','0')) for e in mission)+1),
        Pos=','.join(map(str,position)))
    mission.append(actor)
    soul=ET.SubElement(wh.find('SoulList/Souls'),'Soul',version='8')
    for key,value in dict(Name=namespace,Guid=identity(namespace),SharedSoulGuid=identity(namespace),EntityGuid=guid).items():
        ET.SubElement(soul,key).text=value
    files.update({'objects_mission0.xml':xml(mission),'whdata_0':xml(wh)})
    # An explicit isolated test mode, not an assertion that camp work/sleep or
    # the source quest graph was transferred. Uses a shipped interruptible idle.
    files,report=register_person_world(files,[dict(name=namespace,ai=dict(links=[],schedule=[]))],source,target)
    return files,dict(name=namespace,template=template['name'],soul_id=identity(namespace),
        position=list(position),native_soul=dict(template['soul'].attrib),
        activity_mode='isolated native schedulerWait',source_camp_links_replaced=original_links,
        scheduler=report,native_equipment_preserved=True,never_flee_override=False,runtime_verified=False)
