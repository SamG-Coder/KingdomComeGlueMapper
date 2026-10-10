"""Independent native return-driver soul, dialogue and Storm role."""
import copy
import uuid
import xml.etree.ElementTree as ET
from upgrade_map import xml
from region_travel_policy import LABELS

NAME='gmtravel_return_driver'
SOUL=str(uuid.uuid5(uuid.NAMESPACE_URL,'gluemappertravel/return-driver-soul'))
ROLE='GMTRAVEL_RETURN_DRIVER'
GRAPH='Quests/GlueTravel/return_driver.xml'
STRING='ui_gluemapper_travel_trosky'


def resources(souls, storm, original_dialogue, role_table):
    source=ET.fromstring(souls)
    rows=source.find('souls')
    native=[r for r in rows if r.get('soul_name')=='tsla_nomad']
    if len(native)!=1:raise ValueError('Expected native Trosky driver soul')
    root=ET.Element(source.tag,source.attrib);target=ET.SubElement(root,rows.tag,rows.attrib)
    soul=copy.deepcopy(native[0]);soul.set('soul_id',SOUL);soul.set('soul_name',NAME);target.append(soul)
    # Preserve the original file: Storm loads a named resource, not table deltas.
    roles=ET.fromstring(storm);rules=roles.find('rules')
    if rules is None:raise ValueError('Missing native Storm rules')
    rule=ET.SubElement(rules,'rule',name=NAME)
    ET.SubElement(ET.SubElement(rule,'selectors'),'hasName',name=NAME)
    ET.SubElement(ET.SubElement(rule,'operations'),'addRole',name=ROLE)
    # Storm resolves addRole through the RPG role table. An unregistered name
    # can invalidate this shared rule resource, including the original drivers.
    role_source=ET.fromstring(role_table)
    role_rows=role_source.find('roles')
    matches=[] if role_rows is None else [r for r in role_rows if r.get('role_name')=='PREVOZNIK_TROSECKO']
    if len(matches)!=1:raise ValueError('Expected native coachman role registration')
    role_root=ET.Element(role_source.tag,role_source.attrib)
    role_target=ET.SubElement(role_root,role_rows.tag,role_rows.attrib)
    new_role=copy.deepcopy(matches[0]);new_role.set('role_name',ROLE);role_target.append(new_role)
    # Keep the DOCTYPE, comments and every original rule byte intact.
    at=storm.rfind(b'</rules>')
    if at<0:raise ValueError('Expected native Storm rules closing tag')
    storm_output=storm[:at]+ET.tostring(rule,encoding='utf-8')+b'\n'+storm[at:]
    d=ET.Element('Database',Name='brambora');dialog=ET.SubElement(ET.SubElement(d,'Skald'),'FaderDialog',Name='return_driver')
    ET.SubElement(ET.SubElement(dialog,'Ports'),'Port',Name='travel',Direction='Out',Type='trigger')
    # SelectedSouls is authoring/voice metadata. Menu-only dialogues must still
    # declare their non-speaking NPC participant, as the native Lichtenstejn
    # general-topics dialogue does; otherwise Actor.CanTalk has no NPC to bind.
    body=ET.SubElement(dialog,'Dialogue',TechnicalStatus='Enabled',
                       Initiator='Player',NonSpeakerRoles=ROLE)
    selected=copy.deepcopy(ET.fromstring(original_dialogue).find('./Skald/FaderDialog/Dialogue/SelectedSouls'))
    for actor in selected:
        if actor.get('Role')!='HENRY':actor.set('Role',ROLE);actor.set('Soul',NAME)
    body.append(selected)
    choices=ET.SubElement(ET.SubElement(body,'Decision',Name='destinations',Priority='General'),'Sequences')
    travel=ET.SubElement(choices,'Sequence',Name='travel',EndType='EndDialogue',GrayOutIfSequencesUsed='Never')
    ET.SubElement(travel,'UiPrompt',StringName=STRING,Text=LABELS[STRING])
    ET.SubElement(ET.SubElement(travel,'Triggers'),'Port',Name='travel')
    ET.SubElement(ET.SubElement(travel,'Elements'),'Response',Role='HENRY')
    # The runtime supplies End dialog, as in the native driver's top decision.
    strings=ET.Element('Table');row=ET.SubElement(strings,'Row')
    for v in (STRING,LABELS[STRING],LABELS[STRING]):ET.SubElement(row,'Cell').text=v
    return {'Libs/Tables/rpg/soul__gluemappertravel.xml':xml(root),
            'Libs/Tables/rpg/role__gluemappertravel.xml':xml(role_root),
            'Libs/Storm/roles/world/levelSwitch.xml':storm_output,GRAPH:xml(d)},xml(strings)


def attach_dialogue(graph):
    root=ET.fromstring(graph);project=root.find('./Skald/Project')
    definitions=ET.Element('Definitions');project.insert(0,definitions)
    ET.SubElement(definitions,'Definition',File='return_driver.xml')
    nodes=project.find('Nodes');ET.SubElement(nodes,'return_driver',Name='return_driver')
    wait=ET.SubElement(nodes,'SceneFinishedWaiter',Name='return_dialogue_finished')
    ET.SubElement(wait,'Edge',From='return_driver.travel',To='Enqueue')
    prepare=nodes.find("Function[@Name='prepare_travel']")
    ET.SubElement(prepare,'Edge',From='return_dialogue_finished.OnFinished',To='Exec')
    return xml(root)


def registration(entity_guid):
    soul=ET.Element('Soul',version='8')
    for k,v in [('SharedSoulGuid',SOUL),('Guid',SOUL),('EntityGuid',entity_guid),('Name',NAME)]:
        ET.SubElement(soul,k).text=v
    return soul
