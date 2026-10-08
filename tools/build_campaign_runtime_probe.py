"""Build an isolated native quest/trigger test level, preserving the base world.

All actions stay in a new staging directory. The diagnostic area listeners log
physical enter/leave events; they do not bypass source quest branch conditions.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import native_guid, guid_value, append_links
from campaign_package import campaign_spawn_objects, level_table_shells, level_registration
from campaign_quest_graph import identifier, xml, edge, constant
from campaign_sources import opening_spawn
from campaign_trigger_areas import convert_areas
from upgrade_map import read


def selected_triggers(links, quest):
    candidates = [(key, row) for key, row in links['world']['entities'].items()
                  if row['attributes'].get('Name') == quest and row['attributes'].get('EntityClass') == 'QuestObject']
    if len(candidates) != 1: raise ValueError('Missing/ambiguous quest source entity')
    selected = {}
    for link in links['world']['links']:
        if link['from'] != candidates[0][0]: continue
        record = links['world']['entities'][link['to']]
        if record['attributes'].get('EntityClass') == 'TriggerArea':
            selected[record['attributes']['EntityGuid']] = record
    if not selected: raise ValueError('No directly linked source trigger areas')
    return list(selected.values())


def add_concept_preprocess(data, graph, level, player_soul=None):
    root = ET.fromstring(data) if data else ET.Element('Root', version='1')
    root.set('version', '1')
    if player_soul is not None:
        soul_list = root.find('SoulList')
        if soul_list is None: soul_list = ET.SubElement(root, 'SoulList')
        souls = soul_list.find('Souls')
        if souls is None: souls = ET.SubElement(soul_list, 'Souls')
        existing_player = [s for s in souls if s.findtext('Player') == '1']
        if existing_player:
            if len(existing_player) != 1 or existing_player[0].findtext('SharedSoulGuid') != player_soul.findtext('SharedSoulGuid'):
                raise ValueError('Conflicting native player identity in base world')
        else:
            souls.append(copy.deepcopy(player_soul))
    manager = root.find('ConceptManager')
    if manager is None: manager = ET.SubElement(root, 'ConceptManager')
    includes = manager.find('IncludeModGraphs')
    if includes is None: includes = ET.SubElement(manager, 'IncludeModGraphs')
    includes.text = '0'
    paths = manager.find('ConceptPaths')
    if paths is None: paths = ET.SubElement(manager, 'ConceptPaths')
    if graph.lower() not in {(p.text or '').lower() for p in paths}:
        ET.SubElement(paths, 'Path').text = graph.lower()
    if manager.find('ConceptModules') is None: ET.SubElement(manager, 'ConceptModules')
    ai = root.find('AI')
    if ai is None: ai = ET.SubElement(root, 'AI')
    level_path = ai.find('LevelPath')
    if level_path is None: level_path = ET.SubElement(ai, 'LevelPath')
    level_path.text = 'data/levels/' + level
    if ai.find('SmartAreaManager') is None:
        ET.SubElement(ET.SubElement(ai, 'SmartAreaManager', version='2'), 'SmartAreas')
    return xml(root)


def build(base, source_game, native_game, quest_package, links, quest, level, output, level_id):
    identifier(level)
    base, output = Path(base), Path(output)
    quest_package = Path(quest_package)
    if output.exists(): raise FileExistsError('Choose a new output directory')
    manifest = json.loads((quest_package / 'conversion.json').read_text(encoding='utf-8'))
    if not manifest['diagnostics']: raise ValueError('This builder requires an explicit diagnostic package')
    project = manifest['project']
    if quest not in {q['quest'] for q in manifest['quests']}: raise ValueError('Unregistered quest')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((quest_package / 'Data/Quests' / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Quest package changed since conversion')
    records = selected_triggers(links, quest)
    graph_path = 'Quests/' + project + '.xml'
    graph = ET.fromstring((quest_package / 'Data' / graph_path).read_bytes())
    host = graph.find('./Skald/Project')
    assets = ET.SubElement(host, 'Assets')
    # Resolve the actual native player asset, not a guessed source soul ID.
    with zipfile.ZipFile(Path(native_game) / 'Data/Scripts.pak') as native:
        player = ET.fromstring(read(native, 'Quests/Final/Barbora.xml')).find("./Skald/Project/Assets/SoulAsset[@Name='player']")
        if player is None: raise ValueError('Native player asset is unavailable')
        assets.append(copy.deepcopy(player))
    with zipfile.ZipFile(Path(native_game) / 'Data/Levels/trosecko/level.pak') as native:
        native_data = ET.fromstring(read(native, 'whdata_0'))
        player_souls = [s for s in native_data.findall('./SoulList/Souls/Soul')
                        if s.findtext('Player') == '1'
                        and s.findtext('SharedSoulGuid') == player.get('SharedSoulGuids')]
        if len(player_souls) != 1: raise ValueError('Native player soul identity missing or ambiguous')
        player_soul = player_souls[0]
    nodes = host.find('Nodes')
    active = ET.SubElement(nodes, 'State', Name='observe_areas', TypeT='bool')
    constant(active, 'DefaultValue', 'true')
    fragment = ET.parse(quest_package / 'project-entities.xml').getroot()
    # The standalone project fragment uses UUIDs. Export native 64-bit entity
    # identities consistently for both the entity and AI link network.
    for entity in fragment:
        entity.set('EntityGuid', native_guid(int(entity.get('EntityGuid').replace('-', '')[:16], 16)))
    holder = fragment.find("Entity[@EntityClass='SmartObjectHolder']")
    entity_links = ET.SubElement(holder, 'EntityLinks')
    static_links = []
    spawn = opening_spawn(source_game)
    with zipfile.ZipFile(Path(source_game) / 'Data/Levels' / links['source_level'] / 'level.pak') as source:
        source_areas = read(source, 'triggerareas.fubar')
        version = int.from_bytes(hashlib.sha256(xml(graph) + source_areas).digest()[:4], 'little') or 1
        areas, area_report = convert_areas(source_areas, version,
            {int(r['attributes']['EntityGuid'].replace('-', ''), 16) for r in records})
    with zipfile.ZipFile(base / 'level.pak') as incoming:
        mission = ET.fromstring(campaign_spawn_objects(read(incoming, 'objects_mission0.xml'), spawn))
        existing = list(mission.iter('Entity'))
        used_names = {e.get('Name') for e in existing}
        used_guids = {e.get('EntityGuid', '').replace('-', '').lower() for e in existing}
        next_id = max(1000 + len(existing), max((int(e.get('EntityId', '0')) for e in existing), default=0) + 1)
        for record in records:
            source = record['attributes']
            compact = source['EntityGuid'].replace('-', '').lower()
            if source['Name'] in used_names or compact in used_guids:
                raise ValueError('Source trigger already exists in the base world')
            attrs = {k: source[k] for k in ('Name', 'Pos', 'Rotate', 'Scale') if k in source}
            attrs.update(EntityClass='TriggerArea', EntityGuid=native_guid(int(compact, 16)), EntityId=str(next_id))
            next_id += 1
            entity = ET.SubElement(mission, 'Entity', attrs)
            ET.SubElement(entity, 'Properties', bSaved_by_game='0', bTrackAlways='1')
            alias = 'area_' + compact
            ET.SubElement(entity_links, 'Link', TargetId=attrs['EntityId'], TargetGuid=attrs['EntityGuid'], Name="asset['" + alias + "']")
            static_links.append((holder.get('EntityGuid'), attrs['EntityGuid'], "asset['" + alias + "']"))
            ET.SubElement(assets, 'TriggerAreaAsset', Name=alias)
            trigger_name = 'listen_' + compact
            trigger = ET.SubElement(nodes, 'AreaTrigger', Name=trigger_name)
            ET.SubElement(trigger, 'Asset', Name='Souls', Alias='player')
            ET.SubElement(trigger, 'Asset', Name='Areas', Alias=alias)
            edge(trigger, 'observe_areas.State', 'IsActive')
            for event in ('OnEnter', 'OnLeave'):
                trace = ET.SubElement(nodes, 'Trace', Name=alias + '_' + event, TypeT='string')
                constant(trace, 'Value', source['Name'] + ':' + event)
                edge(trace, trigger_name + '.' + event, 'Exec')
        for entity in fragment:
            if entity.get('Name') in used_names: raise ValueError('Project entity name collision')
            entity.set('EntityId', str(next_id)); next_id += 1
            entity.set('Pos', spawn['position'])
            mission.append(entity)
        replacements = {'objects_mission0.xml': xml(mission), 'triggerareas.fubar': areas}
        names = {n.lower(): n for n in incoming.namelist()}
        replacements['waitinglinks.xml'] = append_links(
            read(incoming, names['waitinglinks.xml']) if 'waitinglinks.xml' in names else None, static_links)
        replacements['whdata_0'] = add_concept_preprocess(read(incoming, names['whdata_0']) if 'whdata_0' in names else None,
                                                        graph_path, level, player_soul)
        for name in ('levelinfo.xml', 'leveldata.xml'):
            root = ET.fromstring(read(incoming, name))
            if name == 'levelinfo.xml':
                root.set('Name', 'data/levels/' + level)
                export = root.find('ExportInfo')
                if export is None: export = ET.SubElement(root, 'ExportInfo')
                export.set('Version', str(version)); export.set('Computer', 'GlueMapper')
            else: root.find('LevelInfo').set('Name', level)
            replacements[name] = xml(root)
        for name, payload in level_table_shells(native_game).items():
            if name not in names: replacements[name] = payload
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.campaign-runtime-', dir=output.parent) as temp:
            stage = Path(temp) / 'stage'; stage.mkdir()
            shutil.copytree(quest_package / 'Data', stage / 'Data')
            (stage / 'Data' / graph_path).write_bytes(xml(graph))
            registration = stage / 'Data/Libs/Tables' / ('level__' + level + '.xml')
            registration.parent.mkdir(parents=True, exist_ok=True)
            registration.write_bytes(level_registration(native_game, level, level_id))
            level_path = stage / 'Data/Levels' / level
            level_path.mkdir(parents=True)
            shutil.copyfile(base / 'terrain.pak', level_path / 'terrain.pak')
            (level_path / 'levelinfo.xml').write_bytes(replacements['levelinfo.xml'])
            with zipfile.ZipFile(level_path / 'level.pak', 'x', zipfile.ZIP_STORED) as out:
                for entry in incoming.infolist():
                    if entry.filename.lower() not in replacements: out.writestr(entry, read(incoming, entry.filename))
                for name, data in sorted(replacements.items()): out.writestr(name, data)
            report = dict(schema=1, level=level, level_id=level_id, project=project, base=str(base),
                          trigger_areas=area_report, trigger_entities=[r['attributes']['Name'] for r in records],
                          quest_conversion=manifest, source_behavior_executed=False, campaign_ready=False,
                          area_listeners='diagnostic only; do not mutate quest state',
                          player_identity='native player soul registration; source inventory and stats still require conversion',
                          files={p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in stage.rglob('*') if p.is_file()})
            (stage / 'runtime-probe.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            stage.rename(output)
    return dict(level=level, project=project, triggers=len(records), campaign_ready=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--kcd1', type=Path, required=True)
    p.add_argument('--kcd2', type=Path, required=True)
    p.add_argument('--quest-package', type=Path, required=True)
    p.add_argument('--links', type=Path, required=True)
    p.add_argument('--quest', required=True)
    p.add_argument('--level', required=True)
    p.add_argument('--level-id', required=True, type=int, help='Unused native level ID for isolated save/load tests')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.base, a.kcd1, a.kcd2, a.quest_package,
                          json.loads(a.links.read_text(encoding='utf-8')), a.quest, a.level, a.output, a.level_id)))


if __name__ == '__main__': main()
