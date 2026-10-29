"""Restore authored KCD1 chairs using the exported KCD2 sitting contract.

Only Chair entities in active source layers are upgraded. Decorative furniture
is not inferred from its mesh name. Existing converted geometry stays in use.
"""
import copy
import xml.etree.ElementTree as ET

from campaign_entity_links import append_links
from cooking_pots import rotate, vector
from entity_visuals import initial_entity, world_transform, quaternion_product as qmul
from upgrade_map import read

PREFIX = 'gm_chair_'
# Ordinary chair with native sit/stand interactions and coincident in/out anchors.
DONOR = '20382'
ROLES = ('attachable', 'chairPointIn', 'chairPointOut', '')


def inventory(source, kind='Chair', *, notify=lambda message: None):
    entities, active = {}, set()
    paths = [path for path in source.namelist() if path.lower() == 'objects_mission0.xml'
             or (path.lower().startswith('layers/') and path.lower().endswith('.xml'))]
    for number, path in enumerate(paths):
        notify(f'Scanning source {kind} objects ({number + 1}/{len(paths)}): {path}')
        main = path.lower() == 'objects_mission0.xml'
        for node in ET.fromstring(read(source, path)).iter('Entity'):
            key = node.get('EntityId')
            entities.setdefault(key, node)
            if (main and not node.get('Layer')) or initial_entity(node):
                active.add(key)
    chairs = [e for e in entities.values() if e.get('EntityClass') == kind] if kind != 'Bench' else [
        e for e in entities.values() if e.get('EntityClass') == 'SmartObjectHolder' and e.find('Properties') is not None
        and e.find('Properties').get('soclasses_SmartObjectHelpers', '').startswith('Bench_')]
    return entities, [e for e in chairs if e.get('EntityId') in active], [
        {'source_id': e.get('EntityId'), 'layer': e.get('Layer')} for e in chairs if e.get('EntityId') not in active]


def template(native):
    entities = {e.get('EntityId'): e for e in ET.fromstring(read(native, 'objects_mission0.xml')).iter('Entity')}
    owner = entities[DONOR]
    if owner.get('EntityClass') != 'StanceSmartObject':
        raise ValueError('Native chair donor changed')
    links = {l.get('Name'): l.get('TargetId') for l in owner.findall('EntityLinks/Link')}
    selected = {DONOR, *(links[role] for role in ROLES)}
    nodes = {key: copy.deepcopy(entities[key]) for key in selected}
    if nodes[links['attachable']].get('EntityClass') != 'Chair' or nodes[links['']].find("Properties/Click[@UseMessage='@ui_use_sit']") is None:
        raise ValueError('Native chair interaction contract changed')
    for node in nodes.values():
        container = node.find('EntityLinks')
        if container is not None:
            for link in list(container):
                if link.get('TargetId') not in selected:
                    container.remove(link)
    return nodes, links


def existing_group(root, prefix, expected):
    """Preserve complete contracts; never silently accept partially installed seats."""
    nodes = [e for e in root.iter('Entity') if e.get('Name', '').startswith(prefix)]
    if not nodes:
        return False
    if len(nodes) != len(expected) or {e.get('Name') for e in nodes} != expected:
        raise ValueError(f'Incomplete seating contract: {prefix}')
    ids = {e.get('EntityId'): e.get('EntityGuid') for e in nodes}
    for node in nodes:
        for link in node.findall('EntityLinks/Link'):
            if link.get('TargetId') not in ids or ids[link.get('TargetId')] != link.get('TargetGuid'):
                raise ValueError(f'Broken seating link: {prefix}')
    return True


def convert(files, source, native, guid, incremental=False):
    source_entities, chairs, deferred = inventory(source)
    originals, roles = template(native)
    root = ET.fromstring(files['objects_mission0.xml'])
    if not incremental and any(e.get('Name', '').startswith(PREFIX) for e in root.iter('Entity')):
        raise ValueError('Chairs already converted; use the pre-conversion baseline')
    visuals = {e.get('Name'): e for e in root.iter('Entity')}
    next_id = max((int(e.get('EntityId', '0')) for e in root.iter('Entity')), default=0) + 1
    dp, dq, _ = world_transform(originals[roles['attachable']], originals)
    records, network, remove = [], [], set()
    for chair in sorted(chairs, key=lambda e: int(e.get('EntityId'))):
        key = chair.get('EntityId')
        if incremental and existing_group(root, f'{PREFIX}{key}_', {f'{PREFIX}{key}_{old}' for old in originals}):
            continue
        visual = visuals.get('glue_item_' + key)
        if visual is None or not visual.get('Geometry'):
            raise ValueError(f'Chair {key}: converted furniture geometry is missing')
        sp, sq, ss = world_transform(chair, source_entities)
        delta = qmul(sq, (dq[0], -dq[1], -dq[2], -dq[3]))
        mapped = {}
        for old_id, original in sorted(originals.items(), key=lambda pair: int(pair[0])):
            node = copy.deepcopy(original)
            for attribute in ('Prefab', 'EditorLayer', 'Layer', 'ParentId', 'ParentGuid'):
                node.attrib.pop(attribute, None)
            node.set('EntityId', str(next_id)); next_id += 1
            node.set('EntityGuid', guid(f'chair/{key}/{old_id}'))
            node.set('Name', f'{PREFIX}{key}_{old_id}')
            p, q, scale = world_transform(original, originals)
            local = rotate((dq[0], -dq[1], -dq[2], -dq[3]), tuple(p[i] - dp[i] for i in range(3)))
            offset = rotate(sq, tuple(local[i] * ss[i] for i in range(3)))
            node.set('Pos', vector(tuple(sp[i] + offset[i] for i in range(3))))
            node.set('Rotate', vector(qmul(delta, q)))
            node.set('Scale', vector(tuple(scale[i] * ss[i] for i in range(3))))
            mapped[old_id] = node
        furniture = mapped[roles['attachable']]
        furniture.find('Properties').set('object_Model', visual.get('Geometry'))
        for attr in ('Material', 'ViewDistRatio', 'CastShadowMinSpec'):
            if attr in visual.attrib: furniture.set(attr, visual.get(attr))
        for node in mapped.values():
            for link in node.findall('EntityLinks/Link'):
                target = mapped[link.get('TargetId')]
                link.set('TargetId', target.get('EntityId'))
                link.set('TargetGuid', target.get('EntityGuid'))
                if link.get('Name'):
                    network.append((node.get('EntityGuid'), target.get('EntityGuid'), link.get('Name')))
        root.extend(mapped.values())
        remove.add(visual.get('Name'))
        records.append({'source_id': key, 'position': list(sp), 'source_model': chair.find('Properties').get('object_Model'),
                        'converted_model': visual.get('Geometry'), 'entity_guids': [e.get('EntityGuid') for e in mapped.values()]})
    for parent in root.iter():
        for child in list(parent):
            if child.get('Name') in remove: parent.remove(child)
    result = dict(files)
    result['objects_mission0.xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    result['waitinglinks.xml'] = append_links(files.get('waitinglinks.xml'), network)
    from seating_animations import install
    result, animation_report = install(result, native)
    return result, {'converted': len(records), 'entities_added': 5 * len(records), 'visuals_replaced': len(remove),
                    'inactive_layer_chairs': deferred, 'records': records,
                    'animation_contract': animation_report,
                    'native_stance': originals[DONOR].find('Properties').get('soclass_SmartObjectHelpers')}
