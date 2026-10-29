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
from character_person import native_xml, unique, resolve_person, resolve_link_targets


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
        # KCD1 includes every type row in the stock vector. default_on is
        # copied into an item-creation flag (0x8000), not an enabled-stock
        # predicate. Filtering it drops whole shops, including coal sellers.
        multiplier = Decimal(selected[0]['amount_multiplier']) if selected else Decimal(1)
        amount = Decimal(row['amount']) * Decimal(shop['amount_multiplier'] or '1') * multiplier
        if amount < 0 or amount != amount.to_integral_value():
            raise ValueError('Fractional/negative source stock needs an explicit rounding policy')
        if amount:
            result[item] = int(amount)
    if not result:
        raise ValueError('Source shop has no enabled stock')
    return result


def resolve_merchant(source, name, source_level='rataje'):
    person = resolve_person(source, name, source_level)
    with RetailSourceReader(source) as reader:
        tables = SourceTables(source, reader)
        soul = person['soul']
        keeper = tables.row('shop/shopkeeper', 'keeper_id', soul['soul_id'])
        shop = tables.row('shop/shop', 'shop_id', keeper['shop_id'])
        stock = resolve_stock(shop, tables.get('shop/shop_type2item')['rows'], tables.get('shop/shop2item')['rows'])
        table_evidence = {k: v['provenance'] for k, v in tables.cache.items()}
    with zipfile.ZipFile(Path(source) / 'Data/Levels' / source_level / 'level.pak') as archive:
        mission = read(archive, 'objects_mission0.xml')
        entities = list(ET.fromstring(mission).iter('Entity'))
        actor = person['actor']
        entities = resolve_link_targets(archive, actor, entities, person['provenance']['actor_sources'])
        work = unique((l for l in actor.findall('EntityLinks/Link') if l.get('Name', '').startswith('Work[')), 'source merchant work link')
        area = unique((e for e in entities if e.get('EntityId') == work.get('TargetId')), 'source work area')
        # Shop markers can live in source layers, just like their work areas.
        # Layers without the class name cannot hold one, so skip parsing them.
        present = {e.get('EntityId') for e in entities}
        for entry in archive.namelist():
            if entry.lower().startswith('layers/') and entry.lower().endswith('.xml'):
                blob = read(archive, entry)
                # UTF-16 and XML character references can spell Shop without
                # that literal byte sequence. Only skip plain UTF-8 layers.
                if b'Shop' not in blob and b'\x00' not in blob and b'&' not in blob:
                    continue
                for entity in ET.fromstring(blob).iter('Entity'):
                    if entity.get('EntityClass') == 'Shop' and entity.get('EntityId') not in present:
                        entities.append(entity)
                        present.add(entity.get('EntityId'))
        shop_entity = unique((e for e in entities if e.get('EntityClass') == 'Shop' and e.find('Properties') is not None
                              and e.find('Properties').get('iShopId') == shop['shop_id']), 'placed source shop')
    person.update(shop=shop, stock=stock, area=copy.deepcopy(area), shop_entity=copy.deepcopy(shop_entity))
    person['provenance']['tables'].update(table_evidence)
    return person


def import_shop_resources(merchant, source, target, namespace, package, output, *,
                          extra_item_patches=(), reuse_output=False, restock_policy='source_daily'):
    """Resolve all authored stock before creating the native shop preset.

    Clean travel builds have no converted pickup/container items yet. Existing
    merchant upgrades can already contain those definitions. Both paths use the
    same dependency importer and return its assets and localization for packing.
    Missing dependencies must fail conversion, never remove a stock entry.
    """
    from container_items import build as import_items, item_patches
    output = Path(output)
    extra_item_patches = list(extra_item_patches)
    dependencies = import_items(source, target, package, output, list(merchant['stock']),
        asset_namespace=namespace, origin='source merchant stock', reuse_output=reuse_output,
        extra_item_patches=extra_item_patches)
    if dependencies['unresolved_items']:
        raise ValueError('Unresolved source merchant stock: ' + ', '.join(dependencies['unresolved_items']))
    files, strings = {}, []
    for path in sorted((output/'converted').rglob('*')):
        if not path.is_file():
            continue
        name = path.relative_to(output/'converted').as_posix()
        payload = path.read_bytes()
        if name.lower().startswith('localization/english/'):
            strings.append(payload)
        elif name.lower().startswith('localization/'):
            raise ValueError('Unsupported merchant item localization: ' + name)
        else:
            files[name] = payload
    patches = item_patches(package) + extra_item_patches
    patches.extend(payload for name, payload in files.items()
        if re.fullmatch(r'libs/tables/item/item(?:__[^/]+)?\.xml', name, re.I))
    stock_files, report = shop_resources(merchant, target, namespace, patches, restock_policy=restock_policy)
    files.update(stock_files)
    report['item_dependencies'] = dependencies
    return files, strings, report


def shop_resources(merchant, target, namespace, item_patches=(), *, restock_policy='source_daily'):
    """Keep shared item identities; never redefine KCD2 items or other shops."""
    from world_item_definitions import native_items, native_item_registry
    items = native_items(target, item_patches)
    registry = native_item_registry(items)
    definitions = {key:dict(items[registry[key]].attrib) for key in merchant['stock'] if key in registry}
    with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
        shops = native_xml(read(archive, 'Libs/Tables/shop/shop.xml'))
    missing = set(merchant['stock']) - definitions.keys()
    if missing:
        raise ValueError('Stock items need native item conversion: ' + ', '.join(sorted(missing)))
    mask = int(merchant['shop']['restock_day_mask'])
    if not 0 < mask <= 127:
        raise ValueError('Invalid or disabled source restock policy')
    period = '1'
    if restock_policy == 'native_merchant':
        # KCD2 models replenishment as a period, not a weekday bitmask. The
        # population upgrader explicitly selects the installed retail merchant
        # cadence. Keep the source mask and this policy change in the receipt.
        reference = unique((r for r in shops.findall('./Shops/ShopData')
                            if r.get('shop_name') == 'common_merchant'), 'native merchant restock policy')
        period = reference.get('restock_period')
        if not period or int(period) <= 0:
            raise ValueError('Invalid native merchant restock period')
    elif restock_policy != 'source_daily' or mask != 127:
        raise ValueError('Non-daily source restock schedule requires an explicit target policy')
    # Named shops avoid collisions with source or native integer IDs.
    existing = {int(e.get('shop_id')) for e in shops.findall('./Shops/ShopData')}
    shop_id = 20000 + int(merchant['shop']['shop_id'])
    if shop_id in existing:
        raise ValueError('Imported shop ID collision')
    name = namespace + '_shop'
    root = ET.Element('database', name='barbora')
    record = ET.SubElement(ET.SubElement(root, 'Shops', version='5'), 'ShopData',
        shop_id=str(shop_id), shop_name=name, restock_period=period, inventory_preset=name + '_stock')
    ET.SubElement(record, 'ShopItemFilter', filter='player_item.*.*',
        price_buy_multiplier=merchant['shop']['price_buy_multiplier'] or '1',
        price_sell_multiplier=merchant['shop']['price_sell_multiplier'] or '1')
    preset_root = ET.Element('database', name='barbora')
    preset = ET.SubElement(ET.SubElement(preset_root, 'InventoryPresets', version='2'), 'InventoryPreset',
                          Name=name + '_stock', Mode='All', Health='1')
    for item, amount in sorted(merchant['stock'].items()):
        ET.SubElement(preset, 'PresetItem', Name=definitions[item]['Name'], ItemClassId=definitions[item]['Id'], Amount=str(amount))
    return {'Libs/Tables/shop/shop__' + namespace + '.xml': xml(root),
            'Libs/Tables/item/InventoryPreset__' + namespace + '_stock.xml': xml(preset_root)}, dict(
        shop_id=shop_id, shop_name=name, source_shop=merchant['shop'], stock=merchant['stock'],
        native_items=definitions, item_definitions='Native or dependency-converted items; source stock and quantities retained',
        stock_types=len(definitions), runtime_verified=False,
        restock=dict(policy=restock_policy, source_day_mask=mask, native_period_days=int(period),
                     preserves_source_weekdays=restock_policy == 'source_daily'))
