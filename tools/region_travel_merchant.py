"""Connect an imported merchant to native Talk, shop storage and visit assets."""
import copy
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from campaign_entity_links import native_guid, guid_value, append_links
from campaign_trigger_areas import read_areas, write_areas, TriggerArea
from region_travel_services import native_xml
from upgrade_map import read, xml

ROLE = 'GMTRAVEL_RATTAY_INNKEEPER'
DIALOG = 'rattay_innkeeper'


def dialogue():
    root = ET.Element('Database', Name='brambora')
    dialog = ET.SubElement(ET.SubElement(root, 'Skald'), 'FaderDialog', Name=DIALOG)
    body = ET.SubElement(dialog, 'Dialogue', TechnicalStatus='Enabled', Initiator='Player', NonSpeakerRoles=ROLE)
    selected = ET.SubElement(body, 'SelectedSouls')
    ET.SubElement(selected, 'SelectedSoul', Role='HENRY', Voice='tomMcKay', Type='Wave', Language='ENG')
    ET.SubElement(selected, 'SelectedSoul', Role=ROLE, Type='Wave', Language='ENG')
    choices = ET.SubElement(ET.SubElement(body, 'Decision', Name='services', Priority='Shop'), 'Sequences')
    # OpenShop consumes the merchant response role, as in the native chat shop.
    # No invented Lua shop API or inventory transfer is involved.
    buy = ET.SubElement(choices, 'Sequence', Name='trade', EndType='EndDialogue', Type='OpenShop', GrayOutIfSequencesUsed='Never')
    ET.SubElement(buy, 'UiPrompt', StringName='ui_gmtravel_rattay_trade', Text='(Trade)')
    ET.SubElement(ET.SubElement(buy, 'Elements'), 'Response', Role=ROLE)
    return xml(root)


def actor_resources(target, merchant, character, namespace):
    with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
        native = native_xml(read(archive, 'Libs/Tables/rpg/soul__ttac.xml'))
        template = next(e for e in native.iter('soul') if e.get('soul_name') == 'ttac_procek')
        roles = native_xml(read(archive, 'Libs/Tables/rpg/role.xml'))
        role = copy.deepcopy(next(e for e in roles.iter('role') if e.get('role_name') == 'PREVOZNIK_TROSECKO'))
    root = ET.Element('database', name='barbora')
    soul = ET.SubElement(ET.SubElement(root, 'souls', version='2'), 'soul', dict(template.attrib))
    soul.set('soul_id', merchant['soul']['soul_id']); soul.set('soul_name', merchant['name'])
    soul.set('soul_archetype_id', character['archetype'])
    soul.attrib.pop('skald_character_name', None)
    # This isolated service uses the native merchant brain. Original quest AI
    # and the inn's daily schedule are not implied by importing its appearance.
    role.set('role_name', ROLE)
    role_root = ET.Element('database', name='barbora')
    ET.SubElement(role_root, 'roles', version='1').append(role)
    files = {'Libs/Tables/rpg/soul__' + namespace + '.xml': xml(root),
             'Libs/Tables/rpg/role__' + namespace + '.xml': xml(role_root)}
    with zipfile.ZipFile(Path(target) / 'Data/IPL_GameData.pak') as archive:
        storm = ET.fromstring(read(archive, 'Libs/Storm/storm.xml'))
    operations = {
        'roles': [('addRole', dict(name=ROLE))],
        'names': [('setUiName', dict(name=merchant['instance'].findtext('StaticData/NameStringId')))],
        'appearance': [('set' + k.title(), dict(name=v)) for k, v in character['appearance'].items()],
        'equipment': [('setInventory', dict(preset=character['inventory']))],
    }
    for task, steps in operations.items():
        filename = task + '/' + namespace + '.xml'
        definition = storm.find(f"tasks/task[@name='{task}']")
        if definition is None: raise ValueError('Missing native Storm task: ' + task)
        ET.SubElement(definition, 'source', path=filename)
        rules = ET.Element('storm')
        rule = ET.SubElement(ET.SubElement(rules, 'rules'), 'rule', name=namespace + '_' + task)
        ET.SubElement(ET.SubElement(rule, 'selectors'), 'hasName', name=merchant['name'])
        ops = ET.SubElement(rule, 'operations')
        for name, attributes in steps: ET.SubElement(ops, name, attributes)
        files['Libs/Storm/' + filename] = xml(rules)
    files['Libs/Storm/storm.xml'] = xml(storm)
    return files


def register_world(files, graphs, graph_path, source, merchant, shop, identity):
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
    original = merchant['actor']
    npc = add(merchant['name'], 'NPC' if original.get('EntityClass') == 'NPC' else 'NPC_Female', original.get('Pos'),
              native_guid(int(original.get('EntityGuid'), 16)), original.get('Rotate'))
    home = add('gmtravel_rattay_home', 'SchedulerHub', original.get('Pos'))
    original_shop = merchant['shop_entity']
    shop_entity = add('gmtravel_rattay_shop', 'Shop', original_shop.get('Pos'))
    ET.SubElement(shop_entity, 'Properties', sShopName=shop['shop_name'], iShopId=str(shop['shop_id']))
    stash = add('gmtravel_rattay_shop_stock', 'Stash', original_shop.get('Pos'))
    # An invisible inventory holder keeps shop stock separate from worn clothes.
    props = ET.SubElement(stash, 'Properties', object_Model='', bSaved_by_game='1')
    ET.SubElement(props, 'Database', sGeneratedInventory='')
    soul = ET.SubElement(wh.find('./SoulList/Souls'), 'Soul', version='8')
    for key, value in dict(SharedSoulGuid=merchant['soul']['soul_id'], Guid=merchant['instance'].findtext('Guid'),
                           EntityGuid=npc.get('EntityGuid'), Name=merchant['name']).items():
        ET.SubElement(soul, key).text = value
    area_source = merchant['area']
    area = add('gmtravel_rattay_inn_area', 'TriggerArea', area_source.get('Pos'))
    ET.SubElement(area, 'Properties', bTrackAlways='1', bSaved_by_game='0')
    with zipfile.ZipFile(Path(source) / 'Data/Levels/rataje/level.pak') as archive:
        original_areas = read_areas(read(archive, 'triggerareas.fubar'))
    area_number = int(area_source.get('EntityGuid'), 16)
    shapes = [a for a in original_areas['areas'] if a.guid == area_number]
    if not shapes and area_source.get('EntityClass') == 'SmartAreaShape':
        # Smart areas keep their authored polygon on the entity rather than
        # in the TriggerArea-only fubar. Transform LOCAL points exactly once.
        from region_travel import rotate
        authored = area_source.find('Area')
        if authored is None: raise ValueError('Original inn boundary is missing')
        origin = tuple(map(float, area_source.get('Pos').split(',')))
        rotation = tuple(map(float, area_source.get('Rotate', '1,0,0,0').split(',')))
        scale = tuple(map(float, area_source.get('Scale', '1,1,1').split(',')))
        points = []
        for p in authored.findall('Points/Point'):
            local = tuple(a*b for a,b in zip(map(float,p.get('Pos').split(',')),scale))
            points.append(tuple(a+b for a,b in zip(origin,rotate(rotation,local))))
        shapes = [TriggerArea(area_number, float(authored.get('Height'))*scale[2], tuple(points))]
    if len(shapes) != 1: raise ValueError('Original inn boundary is missing or ambiguous')
    areas = read_areas(files['triggerareas.fubar'])
    areas['areas'].append(TriggerArea(guid_value(area.get('EntityGuid')), shapes[0].height, shapes[0].points))
    files['triggerareas.fubar'] = write_areas(areas['areas'], areas['export_version'])
    holder = add(project.get('Name'), 'SmartObjectHolder', original.get('Pos'))
    ET.SubElement(holder, 'Properties', guidSmartObjectType='DEF0005E-0000-0000-0000-DEF00000005E', bSaved_by_game='0')
    links = [(npc, home, '_!home'), (npc, home, 'owner'), (npc, shop_entity, 'owner'),
             (npc, shop_entity, 'shopKeeper'), (shop_entity, stash, 'shopStash'),
             (holder, area, "asset['rattay_inn_area']")]
    for a, b, label in links:
        container = a.find('EntityLinks')
        if container is None: container = ET.SubElement(a, 'EntityLinks')
        ET.SubElement(container, 'Link', Name=label, TargetId=b.get('EntityId'), TargetGuid=b.get('EntityGuid'))
    files['waitinglinks.xml'] = append_links(files.get('waitinglinks.xml'),
        [(a.get('EntityGuid'), b.get('EntityGuid'), label) for a, b, label in links])
    ET.SubElement(project.find('Definitions'), 'Definition', File='rattay_innkeeper.xml')
    ET.SubElement(project.find('Nodes'), DIALOG, Name=DIALOG)
    graphs[graph_path] = xml(root)
    graphs[graph_path.rsplit('/', 1)[0] + '/rattay_innkeeper.xml'] = dialogue()
    files['objects_mission0.xml'] = xml(mission); files['whdata_0'] = xml(wh)
    return files, graphs, dict(npc=merchant['name'], position=original.get('Pos'), entity=npc.get('EntityGuid'),
        soul=merchant['soul']['soul_id'], instance=merchant['instance'].findtext('Guid'),
        source_area=area_source.get('Name'), trigger_vertices=len(shapes[0].points),
        shop=shop['shop_name'], role=ROLE, runtime_verified=False,
        limitations=['Native merchant brain; original daily schedule and lodging service are not converted'])
