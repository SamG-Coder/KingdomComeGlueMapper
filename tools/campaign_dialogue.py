"""Import connected retail dialogue decisions, voices and objective callbacks.

The source is the installed game database, never a rewritten conversation.
Native presentation differences remain in the report. Unsupported gameplay
conditions/actions fail emission instead of turning into successful no-ops.
"""
import argparse
import copy
from collections import defaultdict, deque
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_sources import RetailSourceReader, retail_sources
from campaign_quest_graph import definition, identifier, objective_identifier, xml
from upgrade_map import read


def expose_sequence_history(root):
    """Register local history references with the native dialogue evaluator.

    Shipped KCD2 dialogues mark SequenceUsed targets Visibility=Internal;
    the default None makes evaluation fail, including self references.
    Apply within each dialogue so identical names in other graphs cannot bind.
    """
    changed = []
    dialogues = [root] if root.tag == 'Dialogue' else list(root.iter('Dialogue'))
    for dialogue in dialogues:
        sequences = list(dialogue.iter('Sequence'))
        targets = defaultdict(list)
        for sequence in sequences:
            targets[sequence.get('Name')].append(sequence)
        references = set()
        for element in dialogue.iter():
            for expression in element.attrib.values():
                references.update(re.findall(r"\bSequenceUsed\('([^']+)'\)", expression))
        for name in sorted(references):
            matches = targets.get(name, [])
            if len(matches) != 1:
                raise ValueError('Unresolved or ambiguous local sequence history: ' + name)
            target = matches[0]
            if target.get('Visibility', 'None') == 'None':
                target.set('Visibility', 'Internal')
                changed.append(name)
    return changed
from campaign_entity_links import native_guid
from campaign_dialogue_conditions import QuestConditions
from campaign_dialogue_effects import predicate as effect_predicate


TABLES = ('topic', 'topic2sequence', 'sequence', 'sequence_line', 'response',
          'v_dialogue_commands', 'v_response_wave_file_recorded',
          'sequence2quest_objective', 'sequence_flowchart', 'topictorole',
          'v_branch', 'v_topic2subchapter_id', 'dialogue_mood')


def referenced_roles(model):
    """Include silent presentation participants as well as response speakers."""
    needed = {p['role_id'] for p in model.get('participants', []) if p['topic_id'] in model['topics']}
    for topic in model['topics'].values():
        for sequence in topic['sequences']:
            for line in sequence['lines']:
                if line.get('response'): needed.add(line['response']['role_id'])
                for command in line['commands']:
                    needed.update(command[k] for k in ('who', 'target') if command.get(k) not in (None, '', '0'))
    missing = needed - model['roles'].keys()
    if missing: raise ValueError('Unresolved dialogue role dependencies: ' + ', '.join(sorted(missing)))
    return needed


def unique_index(rows, key):
    result = {}
    for row in rows:
        identity = row[key]
        if identity in result: raise ValueError('Duplicate ' + key + ': ' + identity)
        result[identity] = row
    return result


def group(rows, key):
    result = defaultdict(list)
    for row in rows: result[row[key]].append(row)
    return result


def index_tables(tables):
    """Index one source snapshot once when importing many actor conversations."""
    return dict(topics=unique_index(tables['topic'], 'topic_id'),
                sequences=unique_index(tables['sequence'], 'sequence_id'),
                membership=group(tables['topic2sequence'], 'topic_id'),
                lines=group(tables['sequence_line'], 'sequence_id'),
                responses=unique_index(tables['response'], 'sequence_line_id'),
                commands=group(tables['v_dialogue_commands'], 'sequence_line_id'),
                waves=group(tables['v_response_wave_file_recorded'], 'sequence_line_id'),
                effects=group(tables['sequence2quest_objective'], 'sequence_id'),
                charts=group(tables['sequence_flowchart'], 'sequence_line_id'))


def resolve(tables, alias=None, *, start_topic=None, indexes=None):
    indexes = index_tables(tables) if indexes is None else indexes
    topics, sequences, membership = (indexes[k] for k in ('topics', 'sequences', 'membership'))
    if (alias is None) == (start_topic is None):
        raise ValueError('Select exactly one dialogue alias or source start topic')
    if start_topic is not None:
        start = str(start_topic)
        if start not in topics or not any(r['start_topic'] == start for r in tables['v_branch']):
            raise ValueError('Source start topic is not a registered dialogue branch: ' + start)
        alias = topics[start]['label']
    else:
        if not alias: raise ValueError('Empty dialogue alias; use its source start topic')
        starts = [r for r in topics.values() if r['label'] == alias]
        if len(starts) != 1: raise ValueError('Dialogue alias is missing or ambiguous: ' + alias)
        start = starts[0]['topic_id']
    queue, selected, seq_ids = deque([start]), {}, set()
    while queue:
        tid = queue.popleft()
        if tid in selected: continue
        if tid not in topics or tid not in membership: raise ValueError('Missing dialogue decision: ' + tid)
        entries = sorted(membership[tid], key=lambda r: (int(sequences[r['sequence_id']]['priority']), int(r['sequence_id'])))
        selected[tid] = dict(source=topics[tid], sequences=[])
        for entry in entries:
            sid = entry['sequence_id']; row = sequences[sid]; seq_ids.add(sid)
            selected[tid]['sequences'].append(dict(source=row, entry=entry))
            if row['next'] != '0': queue.append(row['next'])
    lines, responses, commands, waves, effects, charts = (
        indexes[k] for k in ('lines', 'responses', 'commands', 'waves', 'effects', 'charts'))
    for topic in selected.values():
        for sequence in topic['sequences']:
            sid = sequence['source']['sequence_id']
            sequence['effects'] = sorted(effects[sid], key=lambda r: int(r['order_by']))
            sequence['lines'] = []
            seen_orders = set()
            for line in sorted(lines[sid], key=lambda r: int(r['order_by'])):
                if line['order_by'] in seen_orders: raise ValueError('Ambiguous dialogue line order')
                seen_orders.add(line['order_by'])
                lid = line['sequence_line_id']
                sequence['lines'].append(dict(source=line, response=responses.get(lid),
                                               commands=commands[lid], voices=waves[lid], flowcharts=charts[lid]))
    return dict(schema=1, alias=alias, start_topic=start, topics=selected,
                participants=tables['topictorole'],
                branch=[r for r in tables['v_branch'] if r['start_topic'] == start],
                subchapter=[r for r in tables['v_topic2subchapter_id'] if r['start_topic'] == start],
                moods=tables['dialogue_mood'],
                functions={r['function_name']: r['expression'] for r in
                           unique_index(tables.get('dialogue_functions', []), 'function_name').values()},
                executable=False)


def load(game, alias=None, locale='English', *, start_topic=None):
    return load_many(game, [(alias, start_topic)], locale)[0]


_SNAPSHOTS = {}


def snapshot(game, locale):
    """The parsed dialogue tables and every localized dialogue string of one installation.

    Every actor conversation resolves against the same snapshot; parse the
    tables and the 300,000-row string table once per unchanged installation.
    """
    paths = {n: ('Tables.pak', 'Libs/Tables/text/' + n + '.xml') for n in TABLES}
    paths['role'] = ('Tables.pak', 'Libs/Tables/rpg/role.xml')
    paths['dialogue_functions'] = ('Tables.pak', 'Libs/Tables/text/dialogue_functions.xml')
    localization = Path(game) / 'Localization' / (locale + '_xml.pak')
    with RetailSourceReader(game) as reader:
        stat = localization.stat()
        key = (reader.game, locale, reader.signature('Tables.pak'), stat.st_size, stat.st_mtime_ns)
        if key in _SNAPSHOTS: return _SNAPSHOTS[key]
        sources = reader(game, paths)
    tables = {n: [dict(r.attrib) for r in ET.fromstring(s['data']).findall('./table/rows/row')]
              for n, s in sources.items()}
    with zipfile.ZipFile(localization) as z:
        data = read(z, 'text_ui_dialog.xml')
    strings, duplicates = {}, set()
    for row in ET.fromstring(data).findall('Row'):
        cells = row.findall('Cell')
        if len(cells) < 3: raise ValueError('Unsupported localization row')
        name = cells[0].text
        if name in strings: duplicates.add(name)
        else: strings[name] = ''.join(cells[2].itertext())
    _SNAPSHOTS.clear()
    _SNAPSHOTS[key] = dict(sources=sources, tables=tables, strings=strings, duplicates=duplicates,
                           sha256=hashlib.sha256(data).hexdigest())
    return _SNAPSHOTS[key]


def load_many(game, selections, locale='English'):
    """Resolve a campaign dialogue closure against one source database snapshot."""
    if not re.fullmatch(r'[A-Za-z]+', locale): raise ValueError('Invalid locale')
    shared = snapshot(game, locale)
    # Models carry their rows onward; give each call its own copies.
    tables = {n: [dict(r) for r in rows] for n, rows in shared['tables'].items()}
    indexes = index_tables(tables)
    models = [resolve(tables, alias, start_topic=start, indexes=indexes) for alias, start in selections]
    for model in models:
        model['roles'] = {r['role_id']: r for r in tables['role']}
    keys = set()
    model_keys = []
    for model in models:
        selected_keys = set()
        for topic in model['topics'].values():
            for seq in topic['sequences']:
                selected_keys.add(seq['source']['ui_prompt'])
                selected_keys.update(line['response']['string_name'] for line in seq['lines'] if line['response'])
        selected_keys.discard('')
        model_keys.append(selected_keys)
        keys.update(selected_keys)
    keys.discard('')
    duplicated = sorted(keys & shared['duplicates'])
    if duplicated: raise ValueError('Duplicate localized dialogue: ' + duplicated[0])
    strings = shared['strings']
    if keys - strings.keys(): raise ValueError('Missing dialogue text: ' + ', '.join(sorted(keys - strings.keys())))
    for model, selected_keys in zip(models, model_keys):
        model['strings'] = {key: strings[key] for key in selected_keys}
        model['provenance'] = {n: list(s['candidates']) for n, s in shared['sources'].items()}
        model['localization'] = dict(locale=locale, entry='text_ui_dialog.xml', sha256=shared['sha256'])
    return models


def response_camera_command(response, commands, model, *, in_world=False):
    """Preserve native speaker selection; emit only supported shot overrides."""
    mode = response.get('camera_mode', '0')
    if in_world:
        return None
    # The shipped runtime represents Auto by omitting CameraType on a command,
    # not by omitting the command itself. The command requests a fresh shot.
    # An authored cut at response entry already owns that shot; delayed cuts
    # still need an initial speaker shot before their scheduled override.
    if mode == '0':
        if any(c['dialogue_command_type'] == '2' and float(c.get('delay') or 0) <= 0
               for c in commands):
            return None
        return ET.Element('CameraCommand', Role=model['roles'][response['role_id']]['role_name'])
    if mode not in ('0', '1', '2', '3', '4'):
        raise ValueError('Unconverted response camera mode: ' + mode)
    return presentation_command(dict(who=response['role_id'], target='0',
        dialogue_command_type='2', int_param=mode, char_param=''), model)


def presentation_command(command, model, *, scene_bound=True):
    """Translate authored dialogue commands into native KCD2 command records.

    Preserve camera identities and fragment/variant requests. Their assets must
    be packaged separately; matching command syntax does not validate the rig.
    """
    role = model['roles'].get(command['who'])
    if role is None:
        raise ValueError('Unresolved presentation role: ' + command['who'])
    attrs = {'Role': role['role_name']}
    delay = command.get('delay', '')
    if delay:
        if not math.isfinite(float(delay)):
            raise ValueError('Non-finite presentation delay')
        attrs['Delay'] = delay
    typ = command['dialogue_command_type']
    if typ == '4':
        target = model['roles'].get(command.get('target'))
        if target is None or command.get('target') == '0':
            raise ValueError('Unresolved look-at target role: ' + command.get('target', ''))
        if command.get('char_param') or command.get('int_param'):
            raise ValueError('Unconverted look-at parameters')
        return ET.Element('LookAtCommand', attrs, LookAtRole=target['role_name'])
    if command.get('target', '0') != '0':
        raise ValueError('Unconverted presentation target: ' + command['target'])
    if typ == '1':
        moods = {r['dialogue_mood_id']: r['dialogue_mood_name'] for r in model['moods']}
        if command['int_param'] not in moods:
            raise ValueError('Unknown source facial mood')
        return ET.Element('FacialMoodCommand', attrs, FacialMood=moods[command['int_param']])
    if typ == '2':
        if command['int_param'] == '0' and not command['char_param']:
            return ET.Element('CameraCommand', attrs)
        if command['int_param'] == '5' and command['char_param']:
            # A world-space CameraSource is only meaningful with scene placement.
            # Free conversations can start after either participant has moved.
            # Keep the authored target role and timing, but let the native rig
            # resolve its shot against that actor's current transform.
            if not scene_bound:
                return ET.Element('CameraCommand', attrs)
            return ET.Element('CameraCommand', attrs, CustomCamera=command['char_param'])
        modes = {'1': 'Master', '2': 'Medium', '3': 'CloseShot', '4': 'CloseUp'}
        if command['int_param'] not in modes or command['char_param']:
            raise ValueError('Unconverted camera command')
        return ET.Element('CameraCommand', attrs, CameraType=modes[command['int_param']])
    if typ == '3':
        if not command['char_param'] or int(command['int_param']) < 0:
            raise ValueError('Invalid source animation fragment/variant')
        return ET.Element('AnimationCommand', attrs, FragmentId=command['char_param'],
                          Variant=command['int_param'])
    raise ValueError('Unconverted presentation command type: ' + typ)


def camera_assets(model, level_archive):
    """Resolve dialogue camera references from the supplied source level/layers."""
    names = {c['char_param'] for topic in model['topics'].values()
             for sequence in topic['sequences'] for line in sequence['lines']
             for c in line['commands'] if c['dialogue_command_type'] == '2' and c['int_param'] == '5'}
    found, provenance = {}, {}
    with zipfile.ZipFile(level_archive) as archive:
        for path in archive.namelist():
            normalized = path.replace('\\', '/').lower()
            if normalized != 'objects_mission0.xml' and not (normalized.startswith('layers/') and normalized.endswith('.xml')):
                continue
            data = read(archive, path)
            if not any(name.encode() in data for name in names):
                continue
            for source in ET.fromstring(data).iter('Entity'):
                name = source.get('Name')
                if name not in names:
                    continue
                if source.get('EntityClass') != 'CameraSource' or source.find('CameraProxy') is None:
                    raise ValueError('Referenced camera is not a CameraSource: ' + name)
                if name in found:
                    raise ValueError('Ambiguous source camera: ' + name)
                entity = copy.deepcopy(source)
                entity.attrib.pop('EntityId', None)
                layer = entity.attrib.pop('Layer', '')
                entity.set('EditorLayer', layer)
                guid = source.get('EntityGuid', '')
                if len(guid) != 16:
                    raise ValueError('Unexpected source camera identity: ' + guid)
                entity.set('EntityGuid', native_guid(int(guid, 16)))
                found[name] = entity
                provenance[name] = dict(entry=path, source_guid=guid, target_guid=entity.get('EntityGuid'),
                                        source_sha256=hashlib.sha256(data).hexdigest())
    if names - found.keys():
        raise ValueError('Missing source dialogue cameras: ' + ', '.join(sorted(names - found.keys())))
    root = ET.Element('Objects')
    root.extend(found[name] for name in sorted(found))
    return xml(root), provenance


def emit(model, name, quest_models, active_port='active', kind='FaderDialog', convert_presentation=False,
         selection_compiler=None, registered_items=(), participant_assets=None, area_assets=None,
         context_entities=None, scene_bound=False, metarole_dispatch=None):
    identifier(name); identifier(active_port)
    if kind not in ('FaderDialog', 'ForcedDialog', 'Dialog'): raise ValueError('Unsupported dialogue mode')
    root, host = definition(kind, name)
    ports = ET.SubElement(host, 'Ports')
    ET.SubElement(ports, 'Port', Name=active_port, Direction='In', Type='bool')
    settings = dict(TechnicalStatus='Enabled', AllowGreeting='false',
                    Initiator='Player' if kind == 'FaderDialog' else 'NonPlayer')
    branches = model.get('branch', [])
    if branches:
        if len(branches) != 1 or branches[0]['start_topic'] != model['start_topic']:
            raise ValueError('Ambiguous source dialogue branch settings')
        branch = branches[0]
        expected_type = '6' if kind == 'Dialog' else '0'
        if metarole_dispatch is not None:
            if kind != 'Dialog' or branch.get('branch_type_id') != '4':
                raise ValueError('Work metarole dispatch requires its source ambient branch')
            from campaign_work_metaroles import validate_dispatch
            validate_dispatch(model, metarole_dispatch)
            expected_type = '4'
        if branch.get('branch_type_id', '0') != expected_type or branch.get('reputation_condition_id'):
            raise ValueError('Dialogue branch type/reputation policy requires conversion')
        priority = branch['priority']
        if not priority.isdigit() or int(priority) > 2147483647:
            raise ValueError('Invalid source dialogue priority')
        if kind == 'Dialog' and priority != '0':
            raise ValueError('Nondefault in-world branch priority requires conversion')
        if branch['allow_greeting'] not in ('True', 'False'):
            raise ValueError('Invalid source dialogue greeting policy')
        if kind == 'FaderDialog':
            # Both shipped formats store numerical branch priority. Native
            # conversations with Priority=1/2 distinguish mandatory branches
            # from the ordinary Priority=0 question menu.
            settings.update(Priority=priority, AllowGreeting=branch['allow_greeting'].lower())
    elif kind == 'Dialog':
        raise ValueError('In-world dialogue requires its source branch declaration')
    if kind == 'Dialog':
        # Shipped ingame monologues use Dialog, not ForcedDialog: they must
        # leave the player's camera/control alone. Invocation is owned by the
        # source controller adapter; merely registering this graph does not
        # schedule a bark or establish a proximity trigger.
        settings = dict(TechnicalStatus='Enabled', Type='ingame monolog',
                        Initiator='NonPlayer', ClashPriority='Flavor')
    dialogue = ET.SubElement(host, 'Dialogue', settings)
    quest_ids = {m['quest_id']: m for m in quest_models}
    actions = {}
    action_programs = {}
    branch_endings = []
    presentation = []
    scripts = []
    visited = set()
    converted_commands = []
    sequence_records = {}
    for topic in model['topics'].values():
        for record in topic['sequences']:
            sid = record['source']['sequence_id']
            if sid in sequence_records and sequence_records[sid] != record:
                raise ValueError('Conflicting source sequence: ' + sid)
            sequence_records[sid] = record
    conditions = QuestConditions(quest_models, model.get('functions'), registered_items,
                                 participant_assets, sequence_records, area_assets, context_entities)
    from campaign_dialogue_outcomes import outcomes, lower as lower_outcomes
    outcome_specs = {}

    def decision(parent, tid):
        visited.add(tid)
        topic = model['topics'][tid]
        attrs = dict(Name='topic_' + tid)
        if topic['source']['label']: attrs['Alias'] = topic['source']['label']
        d = ET.SubElement(parent, 'Decision', attrs)
        sequences = ET.SubElement(d, 'Sequences')
        for record in topic['sequences']:
            source, entry = record['source'], record['entry']; sid = source['sequence_id']
            selection_attrs = {}
            expression = entry['entry_condition']
            variants = outcomes(expression, model.get('functions', {}))
            if variants and selection_compiler is None:
                raise ValueError('Skill outcome requires a selection compiler')
            if variants:
                expression = variants['Success']
            if selection_compiler is not None:
                selection_attrs, expression = selection_compiler.compile(source, expression)
            elif source['type'] != '0' or source['group'] != '0' or source['skill_check_difficulty_id'] or source['reputation'] != '0':
                raise ValueError('Unconverted dialogue selection semantics for sequence ' + sid)
            condition = conditions.sequence_condition(sid, expression)
            if entry['npc_entry_condition']: raise ValueError('Unconverted NPC dialogue condition')
            if tid == model['start_topic']:
                condition = "Port('" + active_port + "')" + (' AND (' + condition + ')' if condition else '')
            if variants:
                failure = conditions.sequence_condition(sid, variants['Fail'])
                if tid == model['start_topic']:
                    failure = "Port('" + active_port + "')" + (' AND (' + failure + ')' if failure else '')
                outcome_specs['seq_' + sid] = dict(Success=condition, Fail=failure)
            attrs = dict(Name='seq_' + sid, EndType='EndDialogue')
            if source['flags'] == '0' and source['next'] == '0':
                # Native SequenceEndTypes.EndTopic is zero and the editor's
                # get_EndTypeSpecified omits it. An ordinary unflagged branch
                # ending must not force teardown of the conversation session.
                # ForcedDialog controls how a branch starts, not whether the
                # source branch ends the session. Keep follow-up topics open.
                attrs.pop('EndType')
                branch_endings.append(dict(sequence=sid, source_next='0', source_flags='0',
                                           native_end='EndTopic', runtime_verified=False))
            attrs.update(selection_attrs)
            if condition: attrs['EntryCondition'] = condition
            if source['script']:
                # Preserve the actual exit script. This is not evidence that its
                # APIs survived the engine migration; require a live API audit.
                from campaign_script_import import translate
                attrs['ExitScript'] = translate(source['script'])
                scripts.append(dict(sequence=sid, source=source['script'], runtime_verified=False))
            if source['flags'] != '0':
                presentation.append(dict(kind='sequence_flags', sequence=sid, value=source['flags']))
            s = ET.SubElement(sequences, 'Sequence', attrs)
            if source['ui_prompt']:
                ET.SubElement(s, 'UiPrompt', StringName=source['ui_prompt'], Text=model['strings'][source['ui_prompt']])
            ordered_effects = any(effect['condition'] for effect in record['effects'])
            program = []
            for effect in record['effects']:
                q = quest_ids.get(effect['quest_id'])
                if q is None: raise ValueError('Unregistered dialogue quest ' + effect['quest_id'])
                candidates = [r for r in q['tables']['quest_objective']['rows'] if r['objective_id'] == effect['objective_id']]
                if len(candidates) != 1: raise ValueError('Unresolved dialogue objective')
                verb = {'1': 'start', '2': 'complete', '3': 'cancel'}.get(effect['action'])
                if verb is None: raise ValueError('Unsupported dialogue objective action')
                action = dict(quest=q['quest'], input=verb + '_' + objective_identifier(candidates[0]['objective_name']), source=effect)
                if ordered_effects:
                    program.append(dict(action, predicate=effect_predicate(conditions.compile(effect['condition']))))
                    continue
                pname = 'action_' + effect['quest_id'] + '_' + verb + '_' + effect['objective_id']
                if pname not in actions:
                    actions[pname] = action
                    ET.SubElement(ports, 'Port', Name=pname, Direction='Out', Type='trigger')
                triggers = s.find('Triggers')
                if triggers is None: triggers = ET.SubElement(s, 'Triggers')
                ET.SubElement(triggers, 'Port', Name=pname)
            if program:
                from campaign_dialogue_effects import global_snapshots
                snapshots = global_snapshots(program)
                if snapshots: s.set('ExitScript', s.get('ExitScript','')+'\n'+snapshots)
                pname = 'effects_' + sid
                if pname in action_programs and action_programs[pname] != program:
                    raise ValueError('Conflicting dialogue effect program: ' + sid)
                if pname not in action_programs:
                    action_programs[pname] = program
                    ET.SubElement(ports, 'Port', Name=pname, Direction='Out', Type='trigger')
                ET.SubElement(ET.SubElement(s, 'Triggers'), 'Port', Name=pname)
            elements = ET.SubElement(s, 'Elements')
            pending_commands = []
            for line in record['lines']:
                row = line['source']; typ = row['sequence_line_type']
                if typ == '1':
                    response = line['response']
                    if not response: raise ValueError('Missing source response')
                    role = model['roles'][response['role_id']]['role_name']
                    current_response = ET.SubElement(elements, 'Response', Role=role)
                    ET.SubElement(current_response, 'Text', StringName=response['string_name'], Text=model['strings'][response['string_name']])
                    camera = response_camera_command(response, pending_commands, model, in_world=kind == 'Dialog')
                    if camera is not None:
                        ET.SubElement(current_response, 'Commands').append(camera)
                    if pending_commands:
                        commands = current_response.find('Commands')
                        if commands is None: commands = ET.SubElement(current_response, 'Commands')
                        for command in pending_commands:
                            converted = presentation_command(command, model, scene_bound=scene_bound)
                            commands.append(converted)
                            converted_commands.append(dict(sequence=sid, response=row['sequence_line_id'],
                                                           source=command, target=dict(tag=converted.tag, **converted.attrib)))
                        pending_commands = []
                elif typ == '5':
                    if not line['commands']: raise ValueError('Missing source dialogue command')
                    # Source camera entities and old gesture variants require
                    # independent conversion; keep the exact commands visible.
                    if convert_presentation:
                        # KCD1 stores commands BEFORE the response they affect.
                        # Attaching to the preceding spoken line shifts every shot.
                        pending_commands.extend(line['commands'])
                    else:
                        targeting = [c for c in line['commands'] if c['dialogue_command_type'] in ('2', '4')]
                        pending_commands.extend(targeting)
                        remaining = [c for c in line['commands'] if c['dialogue_command_type'] not in ('2', '4')]
                        if remaining:
                            presentation.append(dict(kind='response_commands', sequence=sid, line=row, commands=remaining))
                elif typ in ('2', '4'):
                    presentation.append(dict(kind='authoring_direction', sequence=sid, line=row))
                else:
                    raise ValueError('Unconverted executable dialogue line type: ' + typ)
            if pending_commands:
                raise ValueError('Presentation commands have no following response in sequence ' + sid)
            nxt = source['next']
            if nxt != '0':
                if nxt in visited:
                    s.set('EndType', 'GoTo'); s.set('GoToDecision', 'topic_' + nxt)
                else:
                    s.set('EndType', 'Decision'); decision(s, nxt)
    decision(dialogue, model['start_topic'])
    outcome_aliases = lower_outcomes(dialogue, outcome_specs)
    expose_sequence_history(dialogue)
    from campaign_dialogue_activity import expose as expose_activity
    activity_port = expose_activity(host)
    for port in conditions.bindings:
        ET.SubElement(ports, 'Port', Name=port, Direction='In', Type='bool')
    return xml(root), dict(actions=actions, action_programs=action_programs, conditions=conditions.bindings, active_port=active_port,
                           activity_port=activity_port,
                           skill_outcome_aliases=outcome_aliases,
                           branch_endings=branch_endings,
                           branch_settings=dict(source=branches, native=settings, runtime_verified=False),
                           exit_scripts=scripts, presentation_pending=presentation,
                           converted_commands=converted_commands, presentation_runtime_verified=False,
                           source_alias=model['alias'], native_name=name, kind=kind,
                           topic_count=len(model['topics']), campaign_ready=False)


def voices(game, model, directory):
    directory = PurePosixPath(directory)
    if directory.is_absolute() or '..' in directory.parts or ':' in str(directory): raise ValueError('Invalid voice directory')
    requested = {}
    for topic in model['topics'].values():
        for seq in topic['sequences']:
            for line in seq['lines']:
                if not line['response']: continue
                if not line['voices']: raise ValueError('No recorded voice for ' + line['response']['string_name'])
                for voice in line['voices']:
                    source = 'Dialog/' + voice['path'].zfill(6) + '/' + voice['wave_file'] + '.ogg'
                    target = 'dialog/' + directory.as_posix().lower() + '/' + voice['wave_file'] + '.ogg'
                    if target in requested and requested[target] != source: raise ValueError('Conflicting voice mapping')
                    requested[target] = source
    locale = re.escape(model['localization']['locale'])
    archives = sorted(p for p in (Path(game) / 'Localization').glob('*.pak')
                      if re.fullmatch(r'(?:IPL_)?' + locale + r'(?:-part\d+)?\.pak', p.name, re.I))
    if not archives: raise ValueError('Source language voice packages are unavailable')
    result = {}; report = []
    for archive in archives:
        with zipfile.ZipFile(archive) as z:
            names = {n.lower(): n for n in z.namelist()}
            for target, source in requested.items():
                if source.lower() not in names: continue
                data = read(z, names[source.lower()])
                if not data.startswith(b'OggS'): raise ValueError('Unsupported recorded voice format')
                if target in result and result[target] != data:
                    raise ValueError('Ambiguous voice package precedence: ' + source)
                result[target] = data
                report.append(dict(archive=archive.name, source=names[source.lower()], target=target,
                                   sha256=hashlib.sha256(data).hexdigest()))
    if requested.keys() - result.keys():
        raise ValueError('Missing recorded voices: ' + ', '.join(requested[n] for n in sorted(requested.keys() - result.keys())))
    return result, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kcd1', type=Path, required=True)
    selection = p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--alias')
    selection.add_argument('--start-topic', help='Original database branch start topic, including unnamed branches')
    p.add_argument('--name', required=True)
    p.add_argument('--quest-ir', type=Path, action='append', required=True)
    p.add_argument('--voice-directory', required=True)
    p.add_argument('--locale', default='English')
    p.add_argument('--kind', choices=['FaderDialog', 'ForcedDialog', 'Dialog'], default='FaderDialog')
    p.add_argument('--convert-presentation', action='store_true', help='Emit original camera, gesture and facial-mood commands; requires matching assets and runtime testing')
    p.add_argument('--camera-level', type=Path, help='Source level.pak from which referenced custom cameras are converted')
    p.add_argument('--scene-bound', action='store_true', help='Retain fixed custom cameras for a scene with explicit actor placement; otherwise use actor-relative shots')
    p.add_argument('--output', type=Path, required=True)
    contract_source = p.add_mutually_exclusive_group()
    contract_source.add_argument('--selection-contract', type=Path,
                   help='Validated source/target dialogue selection registries and explicit skill upgrades')
    contract_source.add_argument('--target', type=Path, help='Read selection registries from the installed target game')
    p.add_argument('--skill-upgrade', action='append', default=[], metavar='SOURCE=NATIVE')
    p.add_argument('--preserve-reputation-caps', action='store_true')
    p.add_argument('--registered-items', type=Path, help='JSON list of verified registered item GUIDs')
    a = p.parse_args()
    if a.output.exists(): raise FileExistsError('Choose a new staging directory')
    model = load(a.kcd1, a.alias, a.locale, start_topic=a.start_topic)
    selection_compiler = None
    if a.selection_contract:
        from campaign_dialogue_selection import SelectionCompiler
        selection_compiler = SelectionCompiler.from_contract(a.selection_contract)
    elif a.target:
        from campaign_dialogue_selection import SelectionCompiler, load_contract
        adapters = {}
        for pair in a.skill_upgrade:
            source, separator, target = pair.partition('=')
            if not separator or not source or not target or source in adapters:
                p.error('Each skill upgrade must be a unique SOURCE=NATIVE pair')
            adapters[source] = target
        contract = load_contract(a.kcd1, a.target, adapters)
        contract['preserve_reputation_caps'] = a.preserve_reputation_caps
        selection_compiler = SelectionCompiler(**contract)
    elif a.skill_upgrade or a.preserve_reputation_caps:
        p.error('Selection upgrades require --target')
    registered_items = json.loads(a.registered_items.read_text(encoding='utf-8')) if a.registered_items else []
    if not isinstance(registered_items, list) or not all(isinstance(i, str) for i in registered_items):
        p.error('Registered items must be a JSON list of item GUIDs')
    data, report = emit(model, a.name, [json.loads(p.read_text(encoding='utf-8')) for p in a.quest_ir], kind=a.kind,
                        convert_presentation=a.convert_presentation, selection_compiler=selection_compiler,
                        registered_items=registered_items, scene_bound=a.scene_bound)
    if a.selection_contract:
        report['selection_contract_sha256'] = hashlib.sha256(a.selection_contract.read_bytes()).hexdigest()
    audio, report['voices'] = voices(a.kcd1, model, a.voice_directory)
    a.output.mkdir(parents=True)
    (a.output / 'dialogue.xml').write_bytes(data)
    if a.target:
        (a.output / 'selection-contract.json').write_text(json.dumps(contract, indent=2), encoding='utf-8')
    if a.camera_level:
        cameras, report['cameras'] = camera_assets(model, a.camera_level)
        (a.output / 'camera-entities.xml').write_bytes(cameras)
    for name, payload in audio.items():
        path = a.output / 'Data' / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload)
    if selection_compiler is not None:
        report['resources'] = {}
        for name, payload in selection_compiler.resources().items():
            path = a.output / 'Data' / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload)
            report['resources'][name] = hashlib.sha256(payload).hexdigest()
    (a.output / 'dialogue-ir.json').write_text(json.dumps(model, indent=2), encoding='utf-8')
    (a.output / 'conversion.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(dict(topics=len(model['topics']), voices=len(audio), objective_callbacks=len(report['actions']), campaign_ready=False)))


if __name__ == '__main__': main()
