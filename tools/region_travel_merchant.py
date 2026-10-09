"""Connect an imported merchant to native Talk, shop storage and visit assets."""
import xml.etree.ElementTree as ET

from campaign_entity_links import append_links
from character_person_activity import source_lean_point, register_lean_activity
from character_person import actor_resources as person_resources, register_person
from character_person_shop import register_shop_activity, register_shop_service
from upgrade_map import xml

ROLE = 'GMTRAVEL_RATTAY_INNKEEPER'
DIALOG = 'rattay_innkeeper'


def shop_trace(event):
    """Log a dialogue action without letting a diagnostic abort that action."""
    return ("local ok, result = pcall(function() "
            f"local keeper = dc['{ROLE}']; "
            "if not keeper then return 'missing_participant' end; "
            "local shop = System.GetEntityByName('gmtravel_rattay_shop'); "
            "local stock = System.GetEntityByName('gmtravel_rattay_shop_stock'); "
            "return tostring(Shops.GetShopDBIdByKeeper(keeper.id)) .. ' keeper=' .. tostring(keeper:GetName()) "
            ".. ' shop_entity=' .. tostring(shop ~= nil) "
            ".. ' stock_shop=' .. tostring(stock and Shops.GetShopDBIdByLinkedEntityId(stock.id) or 'missing') "
            ".. ' ready=' .. tostring(keeper.soul:HasScriptContext('shop_sellerReadyToSell')) end); "
            f"System.LogAlways('GLUE_RATTAY_SHOP event={event} shop=' .. tostring(result) .. ' query_ok=' .. tostring(ok))")


def dialogue(person_name):
    root = ET.Element('Database', Name='brambora')
    dialog = ET.SubElement(ET.SubElement(root, 'Skald'), 'FaderDialog', Name=DIALOG)
    ET.SubElement(ET.SubElement(dialog, 'Ports'), 'Port', Name='dialog_started', Direction='Out', Type='trigger')
    body = ET.SubElement(dialog, 'Dialogue', TechnicalStatus='Enabled', Initiator='Player', NonSpeakerRoles=ROLE,
                         AllowGreeting='false', AllowFarewell='false')
    selected = ET.SubElement(body, 'SelectedSouls')
    ET.SubElement(selected, 'SelectedSoul', Role='HENRY', Voice='tomMcKay', Type='Wave', Language='ENG')
    ET.SubElement(selected, 'SelectedSoul', Role=ROLE, Soul=person_name, Type='Wave', Language='ENG')
    # Match the shipped traveling-merchant FaderDialog: a silent automatic
    # entry emits dialog_started before offering topics. BeforePlay alone did
    # not complete the objective in retail. No purchase or area visit is needed.
    entry = ET.SubElement(body, 'Decision', Name='conversation_entry', Priority='General', Autoselect='true')
    started = ET.SubElement(ET.SubElement(entry, 'Sequences'), 'Sequence', Name='begin_conversation',
                            EndType='Decision', ExitScript=shop_trace('talk_started'))
    ET.SubElement(ET.SubElement(started, 'Triggers'), 'Port', Name='dialog_started')
    ET.SubElement(ET.SubElement(started, 'Elements'), 'Response', Role='HENRY')
    choices = ET.SubElement(ET.SubElement(started, 'Decision', Name='services', Priority='General',
                                          Autoselect='false'), 'Sequences')
    # Shop is the selectable topic; OpenShop is the terminal action. Retail
    # executed a sole OpenShop automatically even with AllowFarewell=true.
    # Keep explicit Trade/End choices and put the native action behind Trade.
    buy = ET.SubElement(choices, 'Sequence', Name='trade', EndType='Decision', Type='Shop',
                        GrayOutIfSequencesUsed='Never', ExitScript=shop_trace('trade_selected'))
    ET.SubElement(buy, 'UiPrompt', StringName='ui_gmtravel_rattay_trade', Text='(Trade)')
    ET.SubElement(ET.SubElement(buy, 'Elements'), 'Response', Role='HENRY')
    terminal = ET.SubElement(buy, 'Decision', Name='shop_handoff', Priority='General')
    action = ET.SubElement(ET.SubElement(terminal, 'Sequences'), 'Sequence', Name='open_shop',
                          EndType='EndDialogue', Type='OpenShop', ExitScript=shop_trace('open_shop'))
    ET.SubElement(ET.SubElement(action, 'Elements'), 'Response', Role=ROLE)
    leave = ET.SubElement(choices, 'Sequence', Name='leave', EndType='EndDialogue',
                         GrayOutIfSequencesUsed='Never', ExitScript=shop_trace('leave_selected'))
    ET.SubElement(leave, 'UiPrompt', StringName='ui_end_topic', Text='(End dialog)')
    ET.SubElement(ET.SubElement(leave, 'Elements'), 'Response', Role='HENRY')
    return xml(root)


def actor_resources(target, merchant, character, namespace):
    return person_resources(target, merchant, character, namespace, role_name=ROLE,
        native_soul_table='Libs/Tables/rpg/soul__ttac.xml', native_soul_name='ttac_procek',
        native_role_name='PREVOZNIK_TROSECKO')


def register_world(files, graphs, graph_path, source, target, merchant, shop, identity):
    files, graphs = dict(files), dict(graphs)
    mission = ET.fromstring(files['objects_mission0.xml'])
    wh = ET.fromstring(files['whdata_0'])
    root = ET.fromstring(graphs[graph_path]); project = root.find('./Skald/Project')
    entities = list(mission.iter('Entity'))
    next_id = max(int(e.get('EntityId', '0')) for e in entities) + 1
    names = {e.get('Name') for e in entities}; ids = {e.get('EntityGuid') for e in entities}
    def add(name, cls, position, guid=None, rotation=None):
        nonlocal next_id
        guid = guid or identity(name)
        if name in names or guid in ids: raise ValueError('Merchant entity collision: ' + name)
        attrs = dict(Name=name, EntityClass=cls, EntityId=str(next_id), EntityGuid=guid, Pos=position, CastShadowMinSpec='1')
        if rotation: attrs['Rotate'] = rotation
        entity = ET.SubElement(mission, 'Entity', attrs); next_id += 1
        names.add(name); ids.add(guid)
        return entity
    point, helper = source_lean_point(source, merchant, merchant['area'])
    npc = register_person(add, wh, merchant, placement=point)
    home = add('gmtravel_rattay_home', 'SchedulerHub', point.get('Pos'))
    files['tables/ai/scheduler.xml'], lean = register_lean_activity(
        files['tables/ai/scheduler.xml'], add, npc, home, point, helper, target, 'gmtravel_rattay_lean')
    original_shop = merchant['shop_entity']
    shop_entity = add('gmtravel_rattay_shop', 'Shop', original_shop.get('Pos'))
    ET.SubElement(shop_entity, 'Properties', sShopName=shop['shop_name'], iShopId=str(shop['shop_id']), bOwnerIsSpawned='1')
    files['tables/ai/scheduler.xml'] = register_shop_activity(
        files['tables/ai/scheduler.xml'], target, npc, home, shop_entity)
    stash = add('gmtravel_rattay_shop_stock', 'Stash', original_shop.get('Pos'))
    # An invisible inventory holder keeps shop stock separate from worn clothes.
    props = ET.SubElement(stash, 'Properties', object_Model='', bSaved_by_game='1')
    ET.SubElement(props, 'Database', sGeneratedInventory='')
    links = [(npc, home, '_|innkeeper'), (npc, home, 'owner'), (home, lean, '_use'),
             (npc, shop_entity, 'owner'), (npc, shop_entity, 'shopKeeper'), (shop_entity, stash, 'shopStash'),
             (home, shop_entity, '#OpenShop[innkeeper]'),
             (home, shop_entity, '#AddContext[innkeeper,shop_sellerReadyToSell]')]
    # Use the native spawned_shop ownership/context contract at Level scope.
    # It binds the live imported soul on load, independently of quest progress.
    holders = [e for e in mission.iter('Entity') if e.get('EntityClass') == 'LevelHolder']
    if len(holders) != 1:
        raise ValueError('Merchant service requires one destination LevelHolder')
    holder = holders[0]
    entry_path = graph_path.rsplit('/', 1)[0] + '/entry/' + holder.get('Name') + '.xml'
    graphs[entry_path] = register_shop_service(graphs[entry_path], merchant['soul']['soul_id'], 'rattay_shop')
    links.append((holder, shop_entity, "asset['rattay_shop']"))
    for a, b, label in links:
        container = a.find('EntityLinks')
        if container is None: container = ET.SubElement(a, 'EntityLinks')
        ET.SubElement(container, 'Link', Name=label, TargetId=b.get('EntityId'), TargetGuid=b.get('EntityGuid'))
    files['waitinglinks.xml'] = append_links(files.get('waitinglinks.xml'),
        [(a.get('EntityGuid'), b.get('EntityGuid'), label) for a, b, label in links
         if not label.startswith(('_', '#'))])
    ET.SubElement(project.find('Definitions'), 'Definition', File='rattay_innkeeper.xml')
    ET.SubElement(project.find('Nodes'), DIALOG, Name=DIALOG)
    graphs[graph_path] = xml(root)
    graphs[graph_path.rsplit('/', 1)[0] + '/rattay_innkeeper.xml'] = dialogue(merchant['name'])
    files['objects_mission0.xml'] = xml(mission); files['whdata_0'] = xml(wh)
    return files, graphs, dict(npc=merchant['name'], position=point.get('Pos'), rotation=point.get('Rotate', '1,0,0,0'), entity=npc.get('EntityGuid'),
        soul=merchant['soul']['soul_id'], instance=merchant['instance'].findtext('Guid'),
        source_area=merchant['area'].get('Name'), source_activity=point.get('Name'), native_activity=helper,
        compiled_scheduler_records=4, quest_completion='dialog_started from automatic conversation entry; no area trigger',
        shop=shop['shop_name'], role=ROLE, runtime_verified=False,
        limitations=['Native merchant brain; original daily schedule and lodging service are not converted'])
