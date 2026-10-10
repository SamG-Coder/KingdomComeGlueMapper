"""Source-linked Theresa registration and first-arrival side quest.

The original date graphs are audited separately. Registering the visit does
not silently enable unconverted KCD1 romance behavior in the KCD2 runtime.
"""
import copy
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import guid_value
from campaign_sources import RetailSourceReader
from character_person import resolve_person, register_person, actor_resources, unique
from region_travel_visit import attach, ensure, edge
from upgrade_map import read, xml

NAMESPACE = 'gluemapper_theresa'
ROLE = 'GMTRAVEL_THERESA'
DIALOG = 'theresa_visit'
SPEC = dict(quest='gmtravel_visit_theresa', progress_type='GMTravelTheresaVisitProgress',
    file='visit_theresa', objective='visit_theresa', dialogue=DIALOG, marker='theresa',
    gate='spoke_to_theresa', prefix='gmtravel_theresa_visit', title='Visit Theresa',
    objective_text='Talk to Theresa.', active_text='Visit Theresa at the Rattay mill.',
    done_text='I visited Theresa at the Rattay mill.', quest_type='Side')


def resolve(source):
    """Find the original actor through the source romance controller's link."""
    with zipfile.ZipFile(Path(source) / 'Data/Levels/rataje/level.pak') as archive:
        mission = read(archive, 'objects_mission0.xml')
    entities = list(ET.fromstring(mission).iter('Entity'))
    by_id = {e.get('EntityId'): e for e in entities}
    controller = unique((e for e in entities if e.get('Name') == 'q_romanceWithTheresa'), 'Theresa romance controller')
    actor_link = unique((e for e in controller.findall('EntityLinks/Link')
                        if e.get('Name') == "QuestActor[('tereza')]"), 'Theresa actor link')
    person = resolve_person(source, by_id[actor_link.get('TargetId')].get('Name'))
    places = {}
    for link in controller.findall('EntityLinks/Link'):
        match = re.fullmatch(r"QuestPlace\[\('([^']+)'\)\]", link.get('Name', ''))
        if match:
            entity = by_id[link.get('TargetId')]
            places[match[1]] = dict(entity.attrib)
    with RetailSourceReader(source) as reader:
        sources = reader(source, {
            'repeatable_outings': ('Scripts.pak', 'Libs/AI/quests/q_romanceWithTheresa.xml'),
            'first_courtship': ('Scripts.pak', 'Libs/AI/quests/q_millerDate.xml'),
        })
    audit = dict(controller=controller.get('Name'), actor=person['name'],
        mission_sha256=hashlib.sha256(mission).hexdigest(), places=places,
        scripts={name: dict(entry=value['entry'], provenance=value['candidates'],
            trees=[e.get('name') for e in ET.fromstring(value['data']).findall('BehaviorTree')])
            for name, value in sources.items()},
        date_runtime_enabled=False,
        pending=['date behavior translation', 'native pathing/scene handoff',
                 'original dialogue and voice dependencies', 'repeat and save/load retail tests'])
    return person, audit


def resources(target, source, person, character, existing):
    """Merge Storm rules without dropping the innkeeper or return driver."""
    person = dict(person, instance=copy.deepcopy(person['instance']))
    name = person['instance'].find('StaticData/NameStringId')
    original_key = name.text
    name.text = NAMESPACE + '_name'
    with zipfile.ZipFile(Path(source) / 'Localization/English_xml.pak') as archive:
        names = ET.fromstring(read(archive, 'text_ui_soul.xml'))
    row = unique((r for r in names.findall('Row') if r.findtext('Cell') == original_key), 'Theresa localized name')
    row = copy.deepcopy(row); row.find('Cell').text = name.text
    table = ET.Element('Table'); table.append(row)
    files = actor_resources(target, person, character, NAMESPACE, role_name=ROLE,
        native_soul_table='Libs/Tables/rpg/soul__ttac.xml', native_soul_name='ttac_procek',
        native_role_name='PREVOZNIK_TROSECKO', resources=existing)
    return files, xml(table)


def dialogue(person):
    root = ET.Element('Database', Name='brambora')
    host = ET.SubElement(ET.SubElement(root, 'Skald'), 'FaderDialog', Name=DIALOG)
    ET.SubElement(ET.SubElement(host, 'Ports'), 'Port', Name='dialog_started', Direction='Out', Type='trigger')
    body = ET.SubElement(host, 'Dialogue', TechnicalStatus='Enabled', Initiator='Player',
        NonSpeakerRoles=ROLE, AllowGreeting='false', AllowFarewell='false')
    selected = ET.SubElement(body, 'SelectedSouls')
    ET.SubElement(selected, 'SelectedSoul', Role='HENRY', Voice='tomMcKay', Type='Wave', Language='ENG')
    ET.SubElement(selected, 'SelectedSoul', Role=ROLE, Soul=person['name'], Type='Wave', Language='ENG')
    entry = ET.SubElement(body, 'Decision', Name='conversation_entry', Priority='General', Autoselect='true')
    start = ET.SubElement(ET.SubElement(entry, 'Sequences'), 'Sequence', Name='visit', EndType='Decision',
        ExitScript="System.LogAlways('GLUE_THERESA event=talk_started')")
    ET.SubElement(ET.SubElement(start, 'Triggers'), 'Port', Name='dialog_started')
    ET.SubElement(ET.SubElement(start, 'Elements'), 'Response', Role='HENRY')
    menu = ET.SubElement(start, 'Decision', Name='topics', Priority='General', Autoselect='false')
    leave = ET.SubElement(ET.SubElement(menu, 'Sequences'), 'Sequence', Name='leave',
        EndType='EndDialogue', GrayOutIfSequencesUsed='Never')
    ET.SubElement(leave, 'UiPrompt', StringName='ui_end_topic', Text='(End dialog)')
    ET.SubElement(ET.SubElement(leave, 'Elements'), 'Response', Role=ROLE)
    return xml(root)


def register_world(files, graphs, graph_path, level, player_soul, person):
    files, graphs = dict(files), dict(graphs)
    mission = ET.fromstring(files['objects_mission0.xml'])
    wh = ET.fromstring(files['whdata_0'])
    entities = list(mission.iter('Entity'))
    next_id = max(int(e.get('EntityId', '0')) for e in entities) + 1
    def add(name, cls, pos, guid, rotation):
        if any(e.get('Name') == name or e.get('EntityGuid') == guid for e in entities):
            raise ValueError('Person entity already exists: ' + name)
        attributes = dict(Name=name, EntityClass=cls, EntityId=str(next_id), EntityGuid=guid,
                          Pos=pos, CastShadowMinSpec='1')
        if rotation: attributes['Rotate'] = rotation
        return ET.SubElement(mission, 'Entity', attributes)
    npc = register_person(add, wh, person)
    # A terminal record registers the actor without inventing a daily schedule
    # or an unsupported legacy behavior. Date activities will attach here.
    scheduler = ET.fromstring(files['tables/ai/scheduler.xml'])
    rows = scheduler.find('Schedulers'); npc_guid = str(guid_value(npc.get('EntityGuid')))
    if any(e.get('EntityGuid') == npc_guid for e in rows):
        raise ValueError('Theresa scheduler identity already registered')
    ET.SubElement(rows, 'C_SmartHub', EntityGuid=npc_guid, IgnoreDeadEnds='false', StupidHub='false')
    project_root = ET.fromstring(graphs[graph_path]); project = project_root.find('Skald/Project')
    ET.SubElement(ensure(project, 'Definitions'), 'Definition', File=DIALOG + '.xml')
    ET.SubElement(project.find('Nodes'), DIALOG, Name=DIALOG)
    graphs[graph_path] = xml(project_root)
    graphs[graph_path.rsplit('/', 1)[0] + '/' + DIALOG + '.xml'] = dialogue(person)
    graphs, strings = attach(graphs, graph_path, level, player_soul, person['soul']['soul_id'], spec=SPEC)
    files.update({'objects_mission0.xml': xml(mission), 'whdata_0': xml(wh),
                  'tables/ai/scheduler.xml': xml(scheduler)})
    report = dict(person=person['name'], soul=person['soul']['soul_id'], entity=npc.get('EntityGuid'),
        position=npc.get('Pos'), quest=SPEC['quest'], quest_type='Side',
        start='first destination arrival; persisted None guard', completion='dialog_started',
        runtime_verified=False, date_runtime_enabled=False)
    return files, graphs, strings, report
