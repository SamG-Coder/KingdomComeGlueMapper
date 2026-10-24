"""Source-derived dialogue scene lifecycle and bounded objective stat effects.

No dialogue aliases, actor names, coordinates or reward values are selected here.
The caller selects source records; this adapter rejects unsupported semantics.
"""
import copy
import hashlib
import math
import re
import xml.etree.ElementTree as ET

from campaign_quest_graph import xml, edge, constant, identifier
from campaign_entity_links import append_links, entity_link, native_guid


STATS = dict(str='strength', agi='agility', vit='vitality', spc='speech')


def bind_scene_positions(project_blob, mission_blob, waiting_blob, dialogue_name, participants):
    """Translate selected source scene spots to native dialogue-entry placement.

    Initial entity positions are not scene placement: actors can move or fall
    before dialogue entry. Native DialogueHolder teleportBefore links resolve
    soul assets through TagPoint useAsset links when the scene is prepared.
    Preserve authored transforms; never infer height from terrain or cameras.
    participants maps native soul aliases to original source spot attributes.
    """
    identifier(dialogue_name)
    root, mission = ET.fromstring(project_blob), ET.fromstring(mission_blob)
    project = root.find('./Skald/Project')
    hosts = [e for e in mission if e.get('Name') == project.get('Name')
             and e.get('EntityClass') == 'SmartObjectHolder']
    dialogs = [e for e in project.find('Nodes') if e.get('Name') == dialogue_name]
    if len(hosts) != 1 or len(dialogs) != 1 or not participants:
        raise ValueError('Missing or ambiguous scene project/dialogue/participants')
    assets = project.find('Assets')
    alias = dialogue_name + '_placement'
    if any(a.get('Name') == alias for a in assets) or dialogs[0].find("Asset[@Name='DialogueHolder']") is not None:
        raise ValueError('Scene placement already registered')
    for soul, pose in participants.items():
        identifier(soul)
        if len([a for a in assets if a.tag == 'SoulAsset' and a.get('Name') == soul]) != 1:
            raise ValueError('Missing scene participant soul asset: ' + soul)
        if any(pose.get(k) for k in ('ParentId', 'ParentGuid', 'AttachTo', 'AttachmentTarget')):
            raise ValueError('Parented scene spot requires world transform resolution')
        for attr, count, default in [('Pos', 3, ''), ('Rotate', 4, '1,0,0,0')]:
            values = tuple(float(v) for v in pose.get(attr, default).split(','))
            if len(values) != count or not all(math.isfinite(v) for v in values):
                raise ValueError('Invalid scene transform: ' + attr)
    next_id = max(int(e.get('EntityId', '0')) for e in mission) + 1
    waiting, report = [], []

    def entity(name, cls, pose):
        nonlocal next_id
        guid = native_guid(int.from_bytes(hashlib.sha256(
            ('scene-placement/' + project.get('Name') + '/' + name).encode()).digest()[:8], 'little'))
        if any(e.get('Name') == name or e.get('EntityGuid') == guid for e in mission):
            raise ValueError('Scene placement identity collision: ' + name)
        result = ET.SubElement(mission, 'Entity', Name=name, EntityClass=cls,
                               EntityId=str(next_id), EntityGuid=guid, Pos=pose['Pos'])
        if pose.get('Rotate'): result.set('Rotate', pose['Rotate'])
        next_id += 1
        return result

    holder = entity(alias, 'DialogueHolder', next(iter(participants.values())))
    ET.SubElement(assets, 'DialogueHolderAsset', Name=alias)
    ET.SubElement(dialogs[0], 'Asset', Name='DialogueHolder', Alias=alias)
    entity_link(hosts[0], holder, "asset['" + alias + "']", waiting)
    for soul, pose in participants.items():
        spot = entity(alias + '_' + soul, 'TagPoint', pose)
        ET.SubElement(spot, 'Properties', bSaved_by_game='0')
        entity_link(holder, spot, 'teleportBefore', waiting)
        entity_link(spot, spot, "useAsset['" + soul + "']", waiting)
        report.append(dict(soul_asset=soul, source_name=pose.get('Name'),
                           position=pose['Pos'], rotation=pose.get('Rotate'), phase='teleportBefore'))
    return xml(root), xml(mission), append_links(waiting_blob, waiting), report


def bind_scene_cameras(project_blob, mission_blob, waiting_blob, dialogue_blob):
    """Register custom-camera assets and their native world links.

    CustomCamera references a Skald asset alias, not an entity-name lookup.
    Preserving the CameraSource entity alone leaves the runtime using fallback
    conversation cameras, even though the original transform was imported.
    """
    project_root, mission = ET.fromstring(project_blob), ET.fromstring(mission_blob)
    project = project_root.find('./Skald/Project')
    hosts = [e for e in mission.iter('Entity') if e.get('Name') == project.get('Name')
             and e.get('EntityClass') == 'SmartObjectHolder']
    if len(hosts) != 1: raise ValueError('Expected one native scene project holder')
    host = hosts[0]
    assets = project.find('Assets')
    if assets is None: assets = ET.SubElement(project, 'Assets')
    links = host.find('EntityLinks')
    if links is None: links = ET.SubElement(host, 'EntityLinks')
    waiting, report = [], []
    names = {e.get('CustomCamera') for e in ET.fromstring(dialogue_blob).iter('CameraCommand')
             if e.get('CustomCamera')}
    for name in sorted(names):
        cameras = [e for e in mission.iter('Entity') if e.get('Name') == name]
        if len(cameras) != 1 or cameras[0].get('EntityClass') != 'CameraSource':
            raise ValueError('Missing or ambiguous imported camera: ' + name)
        camera = cameras[0]
        if any(e.get('Name') == name for e in assets): raise ValueError('Camera alias already registered: ' + name)
        ET.SubElement(assets, 'CameraAsset', Name=name)
        label = "asset['" + name + "']"
        if any(e.get('Name') == label for e in links): raise ValueError('Camera asset link already registered')
        ET.SubElement(links, 'Link', Name=label, TargetId=camera.get('EntityId'), TargetGuid=camera.get('EntityGuid'))
        waiting.append((host.get('EntityGuid'), camera.get('EntityGuid'), label))
        report.append(dict(alias=name, entity_guid=camera.get('EntityGuid'), position=camera.get('Pos'), rotation=camera.get('Rotate')))
    return xml(project_root), xml(mission), append_links(waiting_blob, waiting), report


def stat_effects(behavior, objectives):
    """Recognize the original completed-objective Get/Advance stat idiom."""
    known = {r['objective_name']: r for r in objectives}
    effects = {}
    for gate in ET.fromstring(behavior).iter('IfCondition'):
        code = gate.find('ExecuteLua')
        if code is None or 'AdvanceToStatLevel' not in code.get('code', ''):
            continue
        condition = gate.get('condition', '').strip('"').strip()
        head = r'\(\$questObjectiveUpdate.State\s*==\s*\$enum:ObjectiveState.Completed\)\s*&\s*\((.*)\)'
        match = re.fullmatch(head, condition, re.S)
        if not match:
            raise ValueError('Unsupported stat reward condition: ' + condition)
        names = []
        for term in match[1].split('|'):
            m = re.fullmatch(r"\s*\$questObjectiveUpdate.ObjectiveName\s*==\s*'([^']+)'\s*", term)
            if not m or m[1] not in known:
                raise ValueError('Unresolved stat reward objective')
            names.append(m[1])
        script = code.get('code').strip('"').strip()
        m = re.fullmatch(r"local\s+(\w+)\s*=\s*player.soul:GetStatLevel\('([a-z]+)'\);\s*"
                         r"player.soul:AdvanceToStatLevel\('\2',\s*\1\s*\+\s*(\d+)\)", script)
        if not m or m[2] not in STATS or int(m[3]) < 1:
            raise ValueError('Unsupported source stat reward: ' + script)
        for name in names:
            if name in effects:
                raise ValueError('Duplicate stat reward objective: ' + name)
            effects[name] = dict(stat=STATS[m[2]], amount=int(m[3]), source=script,
                                 objective_id=known[name]['objective_id'])
    return effects


def bind_stat_effects(blob, effects, player_asset):
    """Use the shipped native Get/Add/ToInt/ToUint/Advance contract."""
    identifier(player_asset)
    root = ET.fromstring(blob)
    nodes = root.find('./Skald/Quest/Nodes')
    if nodes is None:
        raise ValueError('Expected native quest definition')
    for name, spec in effects.items():
        stem = 'reward_' + identifier(name)
        state = 'objective_' + spec['objective_id']
        if nodes.find("State[@Name='" + state + "']") is None:
            raise ValueError('Missing reward objective state')
        # One-shot persisted guard also protects against a repeated completion.
        latch = ET.SubElement(nodes, 'State', Name=stem + '_paid', TypeT='bool')
        constant(latch, 'DefaultValue', 'false')
        invert = ET.SubElement(nodes, 'Function', Name=stem + '_unpaid',
                               MethodName='math::boolean::Not', DeclaringType='math::boolean')
        edge(invert, stem + '_paid.State', 'Value')
        gate = ET.SubElement(nodes, 'If', Name=stem + '_gate')
        edge(gate, stem + '_unpaid.bool', 'Condition'); edge(gate, state + '.OnCompleted', 'Exec')
        get = ET.SubElement(nodes, 'MemberFunction', Name=stem + '_get',
                            MethodName='GetStatLevel', DeclaringType='wh::rpgmodule::I_Soul')
        ET.SubElement(get, 'Asset', Name='Soul', Alias=player_asset)
        constant(get, 'Stat', spec['stat'])
        add = ET.SubElement(nodes, 'Function', Name=stem + '_add',
                            MethodName='wh::conceptmodule::math::AddFloat', DeclaringType='wh::conceptmodule::math')
        edge(add, stem + '_get.Value', 'A'); constant(add, 'B', spec['amount'])
        previous = stem + '_add.float'
        for typ, source_type in [('int', 'float'), ('uint', 'int')]:
            convert = ET.SubElement(nodes, 'Function', Name=stem + '_' + typ,
                                    MethodName=f'math::conversion::To{typ.title()}({source_type})',
                                    DeclaringType='math::conversion')
            edge(convert, previous, source_type); previous = stem + '_' + typ + '.' + typ
        advance = ET.SubElement(nodes, 'MemberFunction', Name=stem + '_advance',
                                MethodName='AdvanceToStatLevel', DeclaringType='wh::rpgmodule::I_Soul')
        ET.SubElement(advance, 'Asset', Name='Soul', Alias=player_asset)
        constant(advance, 'Stat', spec['stat'])
        edge(advance, previous, 'Level'); edge(advance, stem + '_gate.True', 'Exec')
        edge(latch, stem + '_advance.OnExec', 'SetTrue')
    return xml(root)


def attach_scene(project_blob, quest_project_blob, dialogue_name, quest_name,
                 arrival, completion_port, participant_asset):
    """Merge a source quest and dialogue into a destination native project.

    Only a native New Game arrival starts the quest and its ForcedDialog.
    Existing-player arrivals do not request campaign initialization.
    """
    identifier(dialogue_name); identifier(quest_name)
    root, incoming = ET.fromstring(project_blob), ET.fromstring(quest_project_blob)
    host, source = root.find('./Skald/Project'), incoming.find('./Skald/Project')
    nodes = host.find('Nodes')
    for section in ('Definitions', 'Nodes', 'Types'):
        dest = host.find(section)
        if dest is None: dest = ET.SubElement(host, section)
        for node in source.find(section):
            key = 'File' if section == 'Definitions' else 'TypeName' if section == 'Types' else 'Name'
            if any(n.get(key) == node.get(key) for n in dest):
                raise ValueError('Scene registration collision: ' + str(node.get(key)))
            dest.append(copy.deepcopy(node))
    # The imported quest enum uses existing log types plus Canceled if absent.
    logtypes = host.find('ObjectiveValueTypes')
    for node in source.find('ObjectiveValueTypes'):
        if not any(n.get('Type') == node.get('Type') for n in logtypes): logtypes.append(copy.deepcopy(node))
    quest = next(n for n in nodes if n.get('Name') == quest_name)
    dialog = next(n for n in nodes if n.get('Name') == dialogue_name)
    latch = ET.SubElement(nodes, 'State', Name=dialogue_name + '_started', TypeT='bool')
    constant(latch, 'DefaultValue', 'false')
    invert = ET.SubElement(nodes, 'Function', Name=dialogue_name + '_unstarted',
                           MethodName='math::boolean::Not', DeclaringType='math::boolean')
    edge(invert, dialogue_name + '_started.State', 'Value')
    gate = ET.SubElement(nodes, 'If', Name=dialogue_name + '_begin')
    edge(gate, arrival, 'Exec'); edge(gate, dialogue_name + '_unstarted.bool', 'Condition')
    sequence = ET.SubElement(nodes, 'TriggerSequence', Name=dialogue_name + '_initialize')
    edge(sequence, dialogue_name + '_begin.True', 'Exec')
    edge(latch, dialogue_name + '_initialize.A', 'SetTrue')
    edge(quest, dialogue_name + '_initialize.B', 'start')
    edge(dialog, dialogue_name + '_initialize.C', 'EnqueueDialogue')
    params = ET.SubElement(nodes, 'Function', Name=dialogue_name + '_params',
                           MethodName='wh::dialogmodule::CreateDialogParams', DeclaringType='wh::dialogmodule')
    ET.SubElement(params, 'Asset', Name='Participants', Alias=participant_asset)
    edge(dialog, dialogue_name + '_params.DialogParams', 'DialogParams')
    waiter = ET.SubElement(nodes, 'SceneFinishedWaiter', Name=dialogue_name + '_finished')
    edge(waiter, dialogue_name + '.' + completion_port, 'Enqueue')
    trace = ET.SubElement(nodes, 'Trace', Name=dialogue_name + '_gameplay', TypeT='string')
    constant(trace, 'Value', 'Imported character dialogue finished; gameplay released')
    edge(trace, dialogue_name + '_finished.OnFinished', 'Exec')
    from campaign_start_policy import gate_start
    return gate_start(xml(root), dialogue_name)
