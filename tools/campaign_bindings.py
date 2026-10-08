"""Resolve quest assets from retail links and tables, without story-specific IDs.

The binding plan is shared by behavior, dialogue and world conversion. A link
being resolved does not mean its target has been converted: readiness requires
the target GUID or item/soul registration in the destination manifest as well.
"""
import ast
from collections import Counter
import hashlib
import re
import xml.etree.ElementTree as ET

from campaign_entity_links import native_guid
from campaign_quest_graph import identifier, xml
from quest_import import literal, walk


ENTITY_ASSETS = {
    'TriggerArea': 'TriggerAreaAsset', 'TagPoint': 'TagPointAsset',
    'SmartAreaShape': 'SmartAreaAsset', 'SmartObjectHolder': 'SmartObjectAsset',
    'Stash': 'StashAsset', 'AnimDoor': 'AnimDoorAsset', 'Camera': 'CameraAsset',
    'CameraSource': 'CameraAsset', 'CutsceneHolder': 'CutsceneHolderAsset',
    'PredefinedPath': 'PredefinedPathAsset',
}
# These are source ABI names, not quest/actor names. The source questUtils
# loadAllReferences routine populates these associative arrays from link tags.
REFERENCE_ARRAYS = {'QuestActor': 't_actors', 'QuestPlace': 't_places',
                    'QuestItem': 't_qItems', 'QuestTrigger': 't_triggers'}
REFERENCE = re.compile(r"\$?(t_actors|t_places|t_qItems|t_triggers)\s*\[\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_]\w*))\s*\]")


def parse_link_label(value):
    """Parse the source link's constant arguments; never execute its syntax."""
    match = re.fullmatch(r'([A-Za-z_]\w*)(?:\[(.*)\])?', value.strip(), re.S)
    if not match:
        raise ValueError('Unsupported source link syntax: ' + value)
    tag, body = match.groups()
    if body is None:
        return dict(tag=tag, positional=[], named={})
    try:
        parsed = ast.parse(body, mode='eval').body
    except SyntaxError as error:
        raise ValueError('Invalid source link arguments: ' + value) from error

    def scalar(node):
        if isinstance(node, ast.Constant) and type(node.value) in (str, int, float, bool):
            return node.value
        if isinstance(node, ast.Name):
            # Packed link labels use bare identifiers as literal strings too,
            # e.g. QuestPlace[initMrsBeran], not behavior-language variables.
            return node.id == 'true' if node.id in ('true', 'false') else node.id
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) and type(node.operand.value) in (int, float):
            return -node.operand.value
        raise ValueError('Nonconstant source link argument: ' + value)

    positional, named = [], {}
    for node in parsed.elts if isinstance(parsed, ast.Tuple) else [parsed]:
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or len(node.args) != 1 or node.keywords or node.func.id in named:
                raise ValueError('Ambiguous source link argument: ' + value)
            named[node.func.id] = scalar(node.args[0])
        else:
            positional.append(scalar(node))
    return dict(tag=tag, positional=positional, named=named)


def parse_link_labels(value):
    """One link can carry several tags; commas inside [] are arguments."""
    try:
        tree = ast.parse(value.strip(), mode='eval').body
    except SyntaxError as error:
        raise ValueError('Invalid source link syntax: ' + value) from error
    nodes = tree.elts if isinstance(tree, ast.Tuple) else [tree]
    return [parse_link_label(ast.get_source_segment(value.strip(), n)) for n in nodes]


def asset_name(quest, kind, identity):
    identifier(quest)
    identifier(kind)
    # Digest the original identity as well: punctuation/case normalization must
    # not collapse two distinct source aliases into the same native asset.
    readable = re.sub(r'[^A-Za-z0-9_]', '_', str(identity))[:48]
    digest = hashlib.sha256(str(identity).encode()).hexdigest()[:12]
    return quest + '_' + kind + '_' + readable + '_' + digest


def soul_index(souls):
    result = {}
    for soul in souls.findall('./SoulList/Souls/Soul'):
        value = soul.findtext('EntityGuid')
        if not value:
            continue
        guid = int(value)
        if not 0 < guid <= 0xffffffffffffffff or guid in result:
            raise ValueError('Duplicate/invalid source soul entity identity')
        result[guid] = dict(shared_soul_guid=soul.findtext('SharedSoulGuid'),
                            instance_guid=soul.findtext('Guid'), name=soul.findtext('Name'))
    return result


def build_bindings(model, world, souls):
    """Resolve every direct quest link and every quest-table asset.

    Keep profile membership and cutscene aliases as data. This function never
    chooses a spawn, activates a profile or treats reference closure as startup.
    """
    quest = identifier(model['quest'])
    rows = model['tables']['quest']['rows']
    if len(rows) != 1 or rows[0]['quest_id'] != model['quest_id'] or rows[0]['quest_name'] != quest:
        raise ValueError('Quest/table identity mismatch')
    smart_object = rows[0].get('smart_object') or quest
    candidates = [key for key, row in world['entities'].items()
                  if row['attributes'].get('Name') == smart_object
                  and row['attributes'].get('EntityClass') == 'QuestObject']
    if len(candidates) > 1:
        raise ValueError('Ambiguous quest world controller: ' + smart_object)
    controller = candidates[0] if candidates else None
    soul_lookup = soul_index(souls)
    bindings, references, cutscenes, pending, seen = [], {}, [], [], {}
    if controller is None and model.get('behavior_status') != 'no_same_named_retail_program':
        pending.append(dict(kind='missing_controller', name=smart_object))
    if controller is not None and model.get('behavior_status') == 'no_same_named_retail_program':
        pending.append(dict(kind='controller_behavior_unresolved', controller=controller, name=smart_object))

    def bind(identity, target, site, label=None):
        record = world['entities'][target]
        attrs = record['attributes']
        try:
            guid = int(attrs['EntityGuid'].replace('-', ''), 16)
            target_guid = native_guid(guid)
        except (KeyError, ValueError) as error:
            raise ValueError('Invalid source entity GUID at ' + target) from error
        name = asset_name(quest, 'entity', identity + ':' + target_guid)
        cls = attrs.get('EntityClass')
        asset = ENTITY_ASSETS.get(cls)
        registration = dict(kind='entity', guid=target_guid, source_class=cls)
        attributes = dict(Name=name)
        if cls in ('NPC', 'NPC_Female'):
            soul = soul_lookup.get(guid)
            if soul is None or not soul['shared_soul_guid']:
                pending.append(dict(kind='missing_soul', site=site, entity=target))
            else:
                asset = 'SoulAsset'
                attributes['SharedSoulGuids'] = soul['shared_soul_guid']
                registration.update(shared_soul_guid=soul['shared_soul_guid'], kind='placed_soul')
        if asset is None:
            pending.append(dict(kind='entity_adapter', site=site, entity=target, source_class=cls))
        item = dict(name=name, asset_type=asset, attributes=attributes, registration=registration,
                    entity=target, source=record['source'], transform={k: attrs[k] for k in ('Pos', 'Rotate', 'Scale') if k in attrs},
                    layer=attrs.get('Layer'), sites=[site], label=label)
        if name in seen:
            previous = seen[name]
            if previous['entity'] != target:
                raise ValueError('Ambiguous quest binding: ' + identity)
            previous['sites'].append(site)
            return previous
        seen[name] = item
        bindings.append(item)
        return item

    for link in world['links']:
        if link['from'] != controller:
            continue
        label = link['source_link'].get('Name', '')
        site = dict(kind='entity_link', controller=controller, label=label, target=link['to'])
        try:
            parsed_labels = parse_link_labels(label)
        except ValueError as error:
            pending.append(dict(kind='link_syntax', site=site, reason=str(error)))
            continue
        item = bind(label, link['to'], site, parsed_labels)
        for parsed in parsed_labels:
            array = REFERENCE_ARRAYS.get(parsed['tag'])
            if array:
                if len(parsed['positional']) != 1 or parsed['named'] or not isinstance(parsed['positional'][0], str):
                    pending.append(dict(kind='reference_alias', site=site))
                    continue
                alias = parsed['positional'][0]
                key = array + '[' + repr(alias) + ']'
                references.setdefault(key, [])
                if item['name'] not in references[key]:
                    references[key].append(item['name'])
            elif parsed['tag'] == 'CutsceneSpot':
                cutscenes.append(dict(parameters=parsed['named'], positional=parsed['positional'], asset=item['name'], site=site))
    for unresolved in world.get('unresolved', []):
        if unresolved['from'] == controller:
            pending.append(dict(kind='unresolved_entity_link', source=unresolved))
    for key, targets in references.items():
        if len(targets) > 1:
            pending.append(dict(kind='multiple_alias_targets', reference=key, assets=targets))

    tables = model['tables']
    table_assets = {}
    for table, asset_type, field, attr, kind in [
        ('quest_npc', 'SoulAsset', 'soul_id', 'SharedSoulGuids', 'soul'),
        ('quest_item', 'ItemAsset', 'item_id', 'ItemClassGuids', 'item'),
    ]:
        for row in tables.get(table, {}).get('rows', []):
            key = row['quest_asset_id']
            if key in table_assets:
                raise ValueError('Duplicate quest table asset: ' + key)
            if not row.get(field):
                pending.append(dict(kind='asset_filter_adapter', table=table, source=row))
                continue
            # A category/faction filter cannot be replaced by a single ID.
            filters = [k for k, v in row.items() if k not in ('quest_asset_id', 'quest_id', field)
                       and v not in ('', '0', 'False')]
            if filters:
                pending.append(dict(kind='asset_filter_adapter', table=table, source=row, fields=filters))
                continue
            name = asset_name(quest, 'asset', key)
            item = dict(name=name, asset_type=asset_type, attributes={'Name': name, attr: row[field]},
                        registration=dict(kind=kind, guid=row[field]), sites=[dict(kind='table', table=table, row=row)])
            bindings.append(item)
            table_assets[key] = name
    for row in tables.get('quest_place', {}).get('rows', []):
        key = row['quest_asset_id']
        if key in table_assets:
            raise ValueError('Duplicate quest table asset: ' + key)
        matches = [k for k, v in world['entities'].items() if v['attributes'].get('Name') == row['entity']]
        if len(matches) != 1 or row.get('map'):
            pending.append(dict(kind='place_resolution', source=row, candidates=matches))
            continue
        item = bind('table_place_' + key, matches[0], dict(kind='table', table='quest_place', row=row))
        table_assets[key] = item['name']
    tracked = []
    for row in tables.get('quest_tracked_asset', {}).get('rows', []):
        item = dict(source=row)
        for field in ('quest_asset_id', 'map_asset_id'):
            value = row.get(field)
            item[field] = table_assets.get(value) if value else None
            if value and value not in table_assets:
                pending.append(dict(kind='tracked_asset_resolution', source=row, field=field))
        tracked.append(item)

    uses = []
    for document, data in model['behavior_documents'].items():
        # Helpers use their caller's bindings; do not accidentally bind another
        # quest document's aliases to this quest's controller.
        if document != 'libs/ai/quests/' + quest.lower() + '.xml':
            continue
        for tree, body in data['trees'].items():
            for index, node in enumerate(walk(body['root'])):
                for argument, value in node['attributes'].items():
                    for match in REFERENCE.finditer(literal(value)):
                        array, single, double, bare = match.groups()
                        alias = single or double or bare
                        key = array + '[' + repr(alias) + ']'
                        site = dict(document=document, tree=tree, preorder_index=index, operation=node['op'], argument=argument)
                        use = dict(expression=match[0], assets=references.get(key, []), site=site)
                        uses.append(use)
                        if not use['assets']:
                            pending.append(dict(kind='behavior_reference', **use))
    return dict(schema=1, quest=quest, controller=controller, bindings=bindings,
                references=references, cutscene_spots=cutscenes, tracked_assets=tracked,
                behavior_references=uses, unresolved=pending, executable=False,
                counts=dict(Counter(b['asset_type'] or 'Unsupported' for b in bindings)))


def registration_errors(plan, registered):
    """Destination evidence is separate from successful source resolution."""
    missing = list(plan['unresolved'])
    for binding in plan['bindings']:
        reg = binding['registration']
        requirements = [('entities', reg['guid'])] if reg['kind'] in ('entity', 'placed_soul') else [(reg['kind'] + 's', reg['guid'])]
        if reg['kind'] == 'placed_soul':
            requirements.append(('souls', reg['shared_soul_guid']))
        for group, value in requirements:
            if value.lower() not in {s.lower() for s in registered.get(group, [])}:
                missing.append(dict(kind='unregistered_target', asset=binding['name'], group=group, identity=value))
    return missing


def emit_assets(plan, registered):
    errors = registration_errors(plan, registered)
    if errors:
        raise ValueError(f"Quest {plan['quest']} has {len(errors)} unresolved/unregistered asset dependencies")
    assets = ET.Element('Assets')
    for item in plan['bindings']:
        attributes = dict(item['attributes'])
        for attribute, group in [('SharedSoulGuids', 'souls'), ('ItemClassGuids', 'items')]:
            source = attributes.get(attribute)
            mapping = registered.get(group, {})
            if source and isinstance(mapping, dict):
                matches = [v for k, v in mapping.items() if k.lower() == source.lower()]
                if len(matches) != 1:
                    raise ValueError('Missing/ambiguous destination identity: ' + source)
                attributes[attribute] = matches[0]
        ET.SubElement(assets, item['asset_type'], attributes)
    return xml(assets)


def native_links(plan, holder_guid):
    """Use the same binding identity for Skald and waitinglinks.xml."""
    return [(holder_guid, b['registration']['guid'], "asset['" + b['name'] + "']")
            for b in plan['bindings'] if b['registration']['kind'] == 'entity' and b['asset_type']]
