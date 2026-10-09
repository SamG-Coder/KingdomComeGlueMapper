"""Bind an imported person's activity to a native staffed shop."""
import copy
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import guid_value
from character_person import unique
from character_person_activity import validate_scheduler_targets
from upgrade_map import read, xml


def register_shop_service(level_graph, soul_guid, shop_alias):
    """Native spawned_shop contract, owned by the destination Level lifecycle.

    Mirrors random_events/events_common/spawned_shop.xml: SetOwner receives the
    live shop and soul, and SetEntityContext enables the merchant service. The
    caller binds the ShopAsset with an asset link on its LevelHolder.
    """
    root = ET.fromstring(level_graph)
    level = root.find('Skald/Level')
    if level is None:
        raise ValueError('Shop service must be hosted by a Level')
    assets = level.find('Assets')
    if assets is None: assets = ET.SubElement(level, 'Assets')
    owner_alias = shop_alias + '_keeper'
    if any(e.get('Name') in (shop_alias, owner_alias) for e in assets):
        raise ValueError('Shop service assets already registered')
    ET.SubElement(assets, 'ShopAsset', Name=shop_alias)
    ET.SubElement(assets, 'SoulAsset', Name=owner_alias, SharedSoulGuids=soul_guid)
    nodes = level.find('Nodes')
    if nodes is None: nodes = ET.SubElement(level, 'Nodes')
    active = ET.SubElement(nodes, 'State', Name=shop_alias + '_service_active', TypeT='bool')
    ET.SubElement(active, 'Constant', Name='DefaultValue', Value='false')
    for event in ('OnWake', 'OnLevelSwitched'):
        ET.SubElement(active, 'Edge', From=event, To='SetTrue')
    owner = ET.SubElement(nodes, 'SetOwner', Name=shop_alias + '_owner')
    ET.SubElement(owner, 'Edge', From=active.get('Name') + '.State', To='IsActive')
    ET.SubElement(owner, 'Asset', Name='What', Alias=shop_alias)
    ET.SubElement(owner, 'Asset', Name='Who', Alias=owner_alias)
    ready = ET.SubElement(nodes, 'SetEntityContext', Name=shop_alias + '_ready')
    ET.SubElement(ready, 'Edge', From=active.get('Name') + '.State', To='IsActive')
    ET.SubElement(ready, 'Constant', Name='Context', Value='shop_sellerReadyToSell')
    ET.SubElement(ready, 'Asset', Name='Souls', Alias=owner_alias)
    return xml(root)


def native_shop_activity(target, role):
    with zipfile.ZipFile(Path(target) / 'Data/Levels/trosecko/level.pak') as archive:
        scheduler = ET.fromstring(read(archive, 'tables/ai/scheduler.xml'))
    rows = scheduler.findall('Schedulers/C_SmartHub')
    for row in rows:
        effects = row.findall('PostSearchData/SmartHubPostSearchData/ElementInitializers/*')
        for opening in effects:
            if opening.tag != 'ElementInitializerOpenShop' or opening.get('Role') != role:
                continue
            ready = [e for e in effects if e.tag == 'ElementInitializerAddContext'
                     and e.get('Context') == 'shop_sellerReadyToSell' and e.get('Role') == role
                     and e.get('TargetGuid') == opening.get('TargetGuid')]
            if len(ready) != 1:
                continue
            terminal = unique((r for r in rows if r.get('EntityGuid') == opening.get('TargetGuid')),
                              'native shop terminal')
            if len(terminal):
                continue
            return copy.deepcopy(opening), copy.deepcopy(ready[0]), copy.deepcopy(terminal)
    raise ValueError('No native shop-opening activity for role: ' + role)


def register_shop_activity(scheduler_blob, target, npc, hub, shop, role='innkeeper'):
    """Keep the person's activity and add native shop lifecycle effects.

    owner/shopKeeper entity links alone do not staff the shop. The scheduler
    provides the occupation role and opens it while the NPC uses this hub.
    """
    opening, ready, terminal = native_shop_activity(target, role)
    root = ET.fromstring(scheduler_blob)
    rows = root.find('Schedulers')
    if rows is None:
        raise ValueError('Missing native schedulers container')
    npc_id, hub_id, shop_id = (str(guid_value(e.get('EntityGuid'))) for e in (npc, hub, shop))
    actor = unique((r for r in rows if r.get('EntityGuid') == npc_id), 'person scheduler')
    work = unique((r for r in rows if r.get('EntityGuid') == hub_id), 'person work hub')
    incoming = unique((l for l in actor.findall('Links/S_ActivityLink') if l.get('TargetGuid') == hub_id),
                      'person work activity')
    params = incoming.find('Parameters')
    if params is None or params.get('ProvidedRole') not in (None, role):
        raise ValueError('Person work activity has an incompatible role')
    if work.find('PostSearchData') is not None or any(r.get('EntityGuid') == shop_id for r in rows):
        raise ValueError('Shop activity is already registered')
    params.set('ProvidedRole', role)
    effects = ET.SubElement(ET.SubElement(ET.SubElement(work, 'PostSearchData'),
                                        'SmartHubPostSearchData'), 'ElementInitializers')
    for effect in (opening, ready):
        effect.set('TargetGuid', shop_id)
        effects.append(effect)
    terminal.set('EntityGuid', shop_id)
    terminal.attrib.pop('Position', None)
    rows.append(terminal)
    validate_scheduler_targets(root)
    return xml(root)
