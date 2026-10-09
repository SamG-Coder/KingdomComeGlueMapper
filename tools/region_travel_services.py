"""Import a selected retail merchant, its authored site and original stock.

Selection is configuration; identity, placement, stock and quantities are read
from the installed source. Unsupported stock policies fail before installation.
"""
import copy
from decimal import Decimal
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_dependency_adapters import SourceTables
from campaign_sources import RetailSourceReader
from upgrade_map import read, xml
from character_person import native_xml, unique, resolve_person


def resolve_stock(shop, defaults, overrides):
    if Decimal(shop.get('amount_multiplier_random_add') or '0') != 0:
        raise ValueError('Random shop quantity multipliers require conversion')
    result = {}
    for row in defaults:
        if row['shop_type_id'] != shop['shop_type_id']:
            continue
        item = row['item_id']
        if item in result:
            raise ValueError('Duplicate source shop item')
        selected = [o for o in overrides if o['shop_id'] == shop['shop_id'] and o['item_id'] == item]
        if len(selected) > 1:
            raise ValueError('Ambiguous source shop override')
        if not selected and row['default_on'].lower() != 'true':
            continue
        multiplier = Decimal(selected[0]['amount_multiplier']) if selected else Decimal(1)
        amount = Decimal(row['amount']) * Decimal(shop['amount_multiplier'] or '1') * multiplier
        if amount < 0 or amount != amount.to_integral_value():
            raise ValueError('Fractional/negative source stock needs an explicit rounding policy')
        if amount:
            result[item] = int(amount)
    if not result:
        raise ValueError('Source shop has no enabled stock')
    return result


def resolve_merchant(source, name):
    person = resolve_person(source, name)
    with RetailSourceReader(source) as reader:
        tables = SourceTables(source, reader)
        soul = person['soul']
        keeper = tables.row('shop/shopkeeper', 'keeper_id', soul['soul_id'])
        shop = tables.row('shop/shop', 'shop_id', keeper['shop_id'])
        stock = resolve_stock(shop, tables.get('shop/shop_type2item')['rows'], tables.get('shop/shop2item')['rows'])
        table_evidence = {k: v['provenance'] for k, v in tables.cache.items()}
    with zipfile.ZipFile(Path(source) / 'Data/Levels/rataje/level.pak') as archive:
        mission = read(archive, 'objects_mission0.xml')
        entities = list(ET.fromstring(mission).iter('Entity'))
        actor = person['actor']
        work = unique((l for l in actor.findall('EntityLinks/Link') if l.get('Name', '').startswith('Work[')), 'source merchant work link')
        area = unique((e for e in entities if e.get('EntityId') == work.get('TargetId')), 'source work area')
        shop_entity = unique((e for e in entities if e.get('EntityClass') == 'Shop' and e.find('Properties') is not None
                              and e.find('Properties').get('iShopId') == shop['shop_id']), 'placed source shop')
    person.update(shop=shop, stock=stock, area=copy.deepcopy(area), shop_entity=copy.deepcopy(shop_entity))
    person['provenance']['tables'].update(table_evidence)
    return person


def shop_resources(merchant, target, namespace):
    """Keep shared item identities; never redefine KCD2 items or other shops."""
    definitions = {}
    with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
        shops = native_xml(read(archive, 'Libs/Tables/shop/shop.xml'))
        for name in archive.namelist():
            if re.fullmatch(r'Libs/Tables/item/item(?:__[^/]+)?\.xml', name, flags=re.I):
                for row in native_xml(read(archive, name)).findall('./ItemClasses/*'):
                    if row.get('Id') in merchant['stock']:
                        if row.get('Id') in definitions:
                            raise ValueError('Ambiguous native item identity: ' + row.get('Id'))
                        definitions[row.get('Id')] = dict(row.attrib)
    missing = set(merchant['stock']) - definitions.keys()
    if missing:
        raise ValueError('Stock items need native item conversion: ' + ', '.join(sorted(missing)))
    if merchant['shop']['restock_day_mask'] != '127':
        raise ValueError('Non-daily source restock schedule requires conversion')
    # Named shops avoid collisions with source or native integer IDs.
    existing = {int(e.get('shop_id')) for e in shops.findall('./Shops/ShopData')}
    shop_id = 20000 + int(merchant['shop']['shop_id'])
    if shop_id in existing:
        raise ValueError('Imported shop ID collision')
    name = namespace + '_shop'
    root = ET.Element('database', name='barbora')
    record = ET.SubElement(ET.SubElement(root, 'Shops', version='5'), 'ShopData',
        shop_id=str(shop_id), shop_name=name, restock_period='1', inventory_preset=name + '_stock')
    ET.SubElement(record, 'ShopItemFilter', filter='player_item.*.*',
        price_buy_multiplier=merchant['shop']['price_buy_multiplier'] or '1',
        price_sell_multiplier=merchant['shop']['price_sell_multiplier'] or '1')
    preset_root = ET.Element('database', name='barbora')
    preset = ET.SubElement(ET.SubElement(preset_root, 'InventoryPresets', version='2'), 'InventoryPreset',
                          Name=name + '_stock', Mode='All', Health='1')
    for item, amount in sorted(merchant['stock'].items()):
        ET.SubElement(preset, 'PresetItem', Name=definitions[item]['Name'], ItemClassId=item, Amount=str(amount))
    return {'Libs/Tables/shop/shop__' + namespace + '.xml': xml(root),
            'Libs/Tables/item/InventoryPreset__' + namespace + '_stock.xml': xml(preset_root)}, dict(
        shop_id=shop_id, shop_name=name, source_shop=merchant['shop'], stock=merchant['stock'],
        native_items=definitions, item_definitions='Existing native IDs; native item prices and effects retained',
        stock_types=len(definitions), runtime_verified=False)
