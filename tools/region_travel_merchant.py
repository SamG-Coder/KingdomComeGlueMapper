"""Connect an imported merchant to native Talk, shop storage and visit assets."""
import xml.etree.ElementTree as ET

from campaign_entity_links import append_links
from character_person_activity import source_lean_point, register_lean_activity
from character_person import actor_resources as person_resources, register_person
from upgrade_map import xml

ROLE = 'GMTRAVEL_RATTAY_INNKEEPER'
DIALOG = 'rattay_innkeeper'


def dialogue(person_name):
    root = ET.Element('Database', Name='brambora')
    dialog = ET.SubElement(ET.SubElement(root, 'Skald'), 'FaderDialog', Name=DIALOG)
    body = ET.SubElement(dialog, 'Dialogue', TechnicalStatus='Enabled', Initiator='Player', NonSpeakerRoles=ROLE)
    selected = ET.SubElement(body, 'SelectedSouls')
    ET.SubElement(selected, 'SelectedSoul', Role='HENRY', Voice='tomMcKay', Type='Wave', Language='ENG')
    ET.SubElement(selected, 'SelectedSoul', Role=ROLE, Soul=person_name, Type='Wave', Language='ENG')
    choices = ET.SubElement(ET.SubElement(body, 'Decision', Name='services', Priority='General'), 'Sequences')
    # OpenShop consumes the merchant response role, as in the native chat shop.
    # No invented Lua shop API or inventory transfer is involved.
    buy = ET.SubElement(choices, 'Sequence', Name='trade', EndType='EndDialogue', Type='OpenShop', GrayOutIfSequencesUsed='Never')
    ET.SubElement(buy, 'UiPrompt', StringName='ui_gmtravel_rattay_trade', Text='(Trade)')
    ET.SubElement(ET.SubElement(buy, 'Elements'), 'Response', Role=ROLE)
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
    ET.SubElement(shop_entity, 'Properties', sShopName=shop['shop_name'], iShopId=str(shop['shop_id']))
    stash = add('gmtravel_rattay_shop_stock', 'Stash', original_shop.get('Pos'))
    # An invisible inventory holder keeps shop stock separate from worn clothes.
    props = ET.SubElement(stash, 'Properties', object_Model='', bSaved_by_game='1')
    ET.SubElement(props, 'Database', sGeneratedInventory='')
    links = [(npc, home, '_'), (npc, home, 'owner'), (home, lean, '_use'),
             (npc, shop_entity, 'owner'), (npc, shop_entity, 'shopKeeper'), (shop_entity, stash, 'shopStash')]
    for a, b, label in links:
        container = a.find('EntityLinks')
        if container is None: container = ET.SubElement(a, 'EntityLinks')
        ET.SubElement(container, 'Link', Name=label, TargetId=b.get('EntityId'), TargetGuid=b.get('EntityGuid'))
    files['waitinglinks.xml'] = append_links(files.get('waitinglinks.xml'),
        [(a.get('EntityGuid'), b.get('EntityGuid'), label) for a, b, label in links])
    ET.SubElement(project.find('Definitions'), 'Definition', File='rattay_innkeeper.xml')
    ET.SubElement(project.find('Nodes'), DIALOG, Name=DIALOG)
    graphs[graph_path] = xml(root)
    graphs[graph_path.rsplit('/', 1)[0] + '/rattay_innkeeper.xml'] = dialogue(merchant['name'])
    files['objects_mission0.xml'] = xml(mission); files['whdata_0'] = xml(wh)
    return files, graphs, dict(npc=merchant['name'], position=point.get('Pos'), rotation=point.get('Rotate', '1,0,0,0'), entity=npc.get('EntityGuid'),
        soul=merchant['soul']['soul_id'], instance=merchant['instance'].findtext('Guid'),
        source_area=merchant['area'].get('Name'), source_activity=point.get('Name'), native_activity=helper,
        compiled_scheduler_records=3, quest_completion='dialogue BeforePlay; no area trigger',
        shop=shop['shop_name'], role=ROLE, runtime_verified=False,
        limitations=['Native merchant brain; original daily schedule and lodging service are not converted'])
