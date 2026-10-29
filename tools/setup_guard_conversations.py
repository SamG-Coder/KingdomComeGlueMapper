"""Upgrade existing packages through the same source actor/dialogue builders."""
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET
from upgrade_map import read


def source_activation(model, tree_name):
    """Read the selected source dialogue initiator's local objective gate."""
    from campaign_quest_graph import objective_identifier
    tree = model['behavior_documents'][model['behavior_entry']]['trees'][tree_name]['root']
    behavior = next(n for n in tree['children'] if n['op'] == 'Behavior')
    children = behavior['children']
    if len(children) != 1 or children[0]['op'] != 'QuestObjectiveCondition':
        raise ValueError('Unsupported source dialogue activation structure')
    guard = children[0]['attributes']
    if guard['quest'].strip('"') not in ('', '$__object.name', model['quest']):
        raise ValueError('External source dialogue activation')
    if guard['function'].strip('"') != 'IsObjectiveStarted':
        raise ValueError('Unsupported source dialogue activation predicate')
    objective = guard['objective'].strip('"')
    if objective not in {r['objective_name'] for r in model['tables']['quest_objective']['rows']}:
        raise ValueError('Unknown source dialogue activation objective')
    return model['quest'] + '.' + objective_identifier(objective) + '_Started'


def upgrade(package, source, target):
    from setup_gameplay import SCENE_PEOPLE, QUEST, QUEST_PATH, PROJECT, LEVEL, ENTRY, DIALOGUES, dialogues
    from campaign_scene_test import source_scene_positions
    from build_quest_people_test import build as people
    from build_recipe import apply_overlay
    from quest_import import import_quest
    package = Path(package)
    with zipfile.ZipFile(package/'Data/gluemappertravel.pak') as z:
        if QUEST_PATH not in z.namelist():
            return {}, {}
        quest = ET.fromstring(read(z, QUEST_PATH))
        if quest.find("./Skald/Quest/Objectives/Objective[@Name='getCrossguard']") is None:
            return {}, {}
        entry = ET.fromstring(read(z, ENTRY))
        present = {n.get('Name') for n in entry.findall('./Skald/Level/Nodes/*')}
    group = next(g for g in DIALOGUES if g[0] == 'gm_guard_interaction')
    expected = {group[0]+'_'+t for t in group[2]}
    if expected <= present:
        return {}, dict(status='present', runtime_verified=False)
    if expected & present:
        raise ValueError('Partial guard dialogue registration requires repair')
    if source is None or target is None:
        raise ValueError('Source games required for guard conversation upgrade')
    work = package.parent/'guard-conversation-upgrade'
    work.mkdir(exist_ok=True)
    changes = {}
    with zipfile.ZipFile(package/'Data/Levels'/LEVEL/'level.pak') as z:
        mission = ET.fromstring(read(z,'objects_mission0.xml'))
        names = {n.get('Name') for n in mission.iter('Entity')}
    missing = set(SCENE_PEOPLE)-names
    if missing:
        spots = source_scene_positions(Path(source),'rataje',QUEST,'stribrna_skalice')
        out = work/'actors'
        if not (out/'report.json').exists():
            people(SimpleNamespace(source=Path(source),target=Path(target),mod=package,output=out,
                model=None,person=sorted(missing),source_level='rataje',level=LEVEL,
                source_profile=[],dialogue_profile=[],dialogue_model=[],quest_path=None,project_path=PROJECT,
                placements={n:spots[SCENE_PEOPLE[n]] for n in missing}))
        report=json.loads((out/'report.json').read_text())
        if report['pending']:
            raise ValueError('Guard actor dependencies incomplete')
        changes.update(apply_overlay(package,out/'package',consume=True))
    from campaign_sources import RetailSourceReader
    with RetailSourceReader(source) as reader:
        model,_=import_quest(Path(source),QUEST,source_reader=reader)
    path=work/'quest-ir.json'
    path.write_text(json.dumps(model),encoding='utf-8')
    out=work/'dialogues'
    if not (out/'report.json').exists():
        dialogues(Path(source),Path(target),package,out,path,group)
    changes.update(apply_overlay(package,out/'package',consume=True))
    return changes, dict(status='built',actors=sorted(SCENE_PEOPLE),topics=list(group[2]),runtime_verified=False)
