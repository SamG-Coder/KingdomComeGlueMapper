"""Discover work programs invoked through source behavior-tag registries.

An AI IncludeTree is not the only program dependency: smart-area requests
dispatch through database tags. Preserve all matching implementations and
report dynamic/fallback selection, rather than choosing one actor's routine.
"""
import re


def discover(trees, table_rows, *, include_area_context=False):
    from quest_import import walk, literal, behavior_path
    calls = []
    for tree_name, tree in trees.items():
        for index, node in enumerate(walk(tree['root'])):
            if node['op'] not in ('SmartAreaBehaviorTag', 'SmartObjectBehaviorTag', 'CallBehavior'):
                continue
            attrs = node['attributes']
            direct = node['op'] == 'CallBehavior'
            preferred = literal(attrs.get('BehaviorName' if direct else 'preferredBehaviors', ''))
            registries = ('sa_behaviour_tag', 'so_behaviour_tag') if direct else (
                ('sa_behaviour_tag',) if node['op'] == 'SmartAreaBehaviorTag' else ('so_behaviour_tag',))
            call = dict(tree=tree_name, preorder_index=index, operation=node['op'],
                arguments=dict(attrs), candidates=[], unresolved=[])
            if not re.fullmatch(r'[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*', preferred):
                call['unresolved'].append('Dynamic or empty preferred work selection requires runtime dispatch')
            else:
                for name in re.split(r'\s*,\s*', preferred):
                    matches = [(registry, r) for registry in registries for r in table_rows(registry)
                               if r.get(registry + '_name') == name]
                    if not matches:
                        call['unresolved'].append('Source work behavior is not registered: ' + name)
                    for registry, row in matches:
                        if not row.get('tree_file') or not row.get('tree_name'):
                            call['unresolved'].append('Source work behavior has no program: ' + name)
                        else:
                            try:
                                file = behavior_path(row['tree_file'])
                            except ValueError as error:
                                call['unresolved'].append(str(error))
                                call.setdefault('invalid_programs', []).append(dict(table=registry, row=dict(row), error=str(error)))
                            else:
                                call['candidates'].append(dict(tag=name, table=registry, row=dict(row),
                                    file=file, tree=row['tree_name']))
            if not direct and literal(attrs.get('preferredBehaviorsOnly', 'false')) != 'true':
                call['unresolved'].append('Unrestricted area fallback needs the placed area behavior registry')
            if include_area_context:
                call['area_context'] = area_context(call['candidates'], table_rows, retain_invalid=True)
                call['candidates'].extend(call['area_context']['programs'])
                call['unresolved'].extend(e['error'] for e in call['area_context']['invalid_programs'])
            calls.append(call)
    return calls


def area_context(candidates, table_rows, *, retain_invalid=False):
    """A requested job also depends on its area's callbacks and sibling jobs.

    Work loops dispatch other behaviors dynamically. Discovering only their
    first named entry loses both the area lifecycle and those subsequent jobs.
    Retain all possible source areas; placement narrows which one is used.
    Import audits can retain malformed registrations as explicit failures;
    callers that cannot propagate those failures remain strict by default.
    """
    from quest_import import behavior_path
    areas, programs, invalid = [], [], []
    def program(file, tree, **metadata):
        try:
            path = behavior_path(file)
        except ValueError as error:
            if not retain_invalid:
                raise
            invalid.append(dict(metadata, file=file, tree=tree, error=str(error)))
        else:
            programs.append(dict(metadata, file=path, tree=tree))
    # Tools dispatch through SmartObject registrations, including useItem's
    # continuation and release callbacks. Following SmartAreas alone loses
    # those jobs even when the area program itself was found successfully.
    for prefix, entity_table in (('sa', 'sa_smart_area'), ('so', 'so_smart_object')):
        tag_table = prefix + '_behaviour_tag'
        tag_id, entity_id = tag_table + '_id', entity_table + '_id'
        tags = {c['row'][tag_id] for c in candidates
                if c['table'] == tag_table and c['row'].get(tag_id)}
        if not tags:
            continue
        links = table_rows(entity_table + '2' + tag_table)
        ids = {r[entity_id] for r in links if r[tag_id] in tags}
        areas.extend(dict(r) for r in table_rows(entity_table) if r[entity_id] in ids)
        sibling_ids = {r[tag_id] for r in links if r[entity_id] in ids}
        for row in table_rows(tag_table):
            if row[tag_id] in sibling_ids and row.get('tree_file') and row.get('tree_name'):
                program(row['tree_file'], row['tree_name'], kind='area_behavior' if prefix == 'sa' else 'object_behavior',
                    table=tag_table, row=dict(row), tag=row[tag_table + '_name'],
                )
    if not areas:
        return dict(templates=[], programs=programs, invalid_programs=invalid)
    brains={r['brain_id'] for r in areas if r.get('brain_id')}
    if brains:
        sub_ids={r['subbrain_id'] for r in table_rows('brain2subbrain') if r['brain_id'] in brains}
        types={r['subbrain_type_id']:r['subbrain_type_name'] for r in table_rows('subbrain_type')}
        schemas={'SmartArea':('subbrain_smart_area',('on_enter_tree','on_exit_tree','on_update_tree','on_request_tree','on_release_tree')),
                 'SmartObject':('subbrain_smart_object',('on_update_tree','on_request_tree','on_release_tree')),
                 'BehaviorTree':('subbrain_behaviour_tree',('tree_name',))}
        for sub in table_rows('subbrain'):
            if sub['subbrain_id'] not in sub_ids:continue
            schema=schemas.get(types[sub['subbrain_type']])
            if schema is None:continue # SubbrainAdapter reports unsupported runtime types.
            table,fields=schema
            for row in table_rows(table):
                if row['subbrain_id']!=sub['subbrain_id']:continue
                for field in fields:
                    if row.get(field):
                        program(row['file_name'], row[field], kind='area_brain_callback',table=table,row=dict(row),
                            callback=field)
    unique={(p['file'],p['tree']):p for p in programs}
    return dict(templates=areas,programs=list(unique.values()),invalid_programs=invalid)
