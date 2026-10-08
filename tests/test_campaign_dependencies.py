import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_dependencies import DependencyImporter, ImportPlan
from campaign_dependency_adapters import EntityAdapter, InventoryAdapter, ItemAdapter
from campaign_trigger_areas import TriggerArea, write_areas, read_areas
from campaign_entity_links import native_guid


class Adapter:
    def __init__(self, dependencies=()): self.dependencies, self.calls = list(dependencies), []
    def plan(self, identity):
        self.calls.append(identity)
        return ImportPlan(self.dependencies, dict(original=identity))
    def convert(self, identity, plan, registered):
        return 'converted-' + identity, {identity + '.xml': b'<registered/>'}, dict(dependencies=plan.dependencies)


class Tables:
    def __init__(self, rows): self.rows = rows
    def get(self, name): return dict(rows=self.rows.get(name, []), provenance=[dict(archive='retail-test')])
    def row(self, name, field, value, optional=False):
        found = [r for r in self.get(name)['rows'] if r.get(field) == value]
        if optional and not found: return {}
        if len(found) != 1: raise ValueError('Missing fixture row ' + name)
        return found[0]


class CampaignDependencyTests(unittest.TestCase):
    def test_existing_registry_is_normalized_and_conflicts_are_rejected(self):
        importer = DependencyImporter({}, {'items': {'ORIGINAL': 'native-item'}})
        self.assertEqual(importer.ensure('items','original'), 'native-item')
        self.assertEqual(importer.registered['items']['original'], 'native-item')
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            DependencyImporter({}, {'items': {'a':'one', 'A':'two'}})

    def test_missing_registration_is_imported_recursively_then_reused(self):
        assets, items = Adapter(), Adapter([('assets', 'model')])
        importer = DependencyImporter(dict(assets=assets, items=items))
        self.assertEqual(importer.ensure('items', 'quest-item', dict(quest='a')), 'converted-quest-item')
        self.assertEqual(importer.ensure('items', 'QUEST-ITEM', dict(quest='b')), 'converted-quest-item')
        self.assertEqual(items.calls, ['quest-item'])
        self.assertEqual(assets.calls, ['model'])
        self.assertEqual(len(importer.jobs[('items', 'quest-item')]['requested_by']), 2)
        self.assertEqual(importer.registered['items']['quest-item'], 'converted-quest-item')
        self.assertEqual(importer.report()['counts'], dict(imported=2))

    def test_dependency_cycles_do_not_publish_false_registration(self):
        importer = DependencyImporter(dict(a=Adapter([('b', 'second')]), b=Adapter([('a', 'first')])))
        self.assertIsNone(importer.ensure('a', 'first'))
        self.assertFalse(importer.registered['a'])
        self.assertFalse(importer.registered['b'])
        self.assertTrue(importer.jobs[('a','first')]['cycle_detected'])

    def test_trigger_import_uses_original_guid_and_world_polygon(self):
        guid = 0x123456789abcdef0
        area = TriggerArea(guid, 8.5, ((4,5,6), (7,8,9), (10,11,12)))
        world = dict(entities={'a': dict(source='layers/place.xml', attributes=dict(
            EntityGuid=f'{guid:016x}', EntityClass='TriggerArea', Name='arbitrary_area', Pos='99,98,97', Layer='phase_two'))})
        adapter = EntityAdapter(world, E.fromstring('<Root/>'), write_areas([area], 10), 20)
        importer = DependencyImporter(dict(entities=adapter))
        identity = native_guid(guid)
        self.assertEqual(importer.ensure('entities', identity), identity)
        shape = read_areas(importer.files['world/triggerareas/' + identity + '.fubar'])
        self.assertEqual(shape['areas'][0].points, area.points)
        self.assertEqual(shape['export_version'], 20)
        entity = E.fromstring(importer.files['world/entities/' + identity + '.xml'])
        self.assertEqual(entity.get('Pos'), '99,98,97')
        self.assertIsNone(entity.get('EntityId'))

    def test_item_import_creates_definition_and_converts_model_and_text(self):
        iid = '12345678-1234-1234-1234-123456789abc'
        tables = Tables({'item/item': [dict(item_id=iid, item_category_id='0', item_name='Any source item')],
            'item/item_category': [dict(item_category_id='0', item_category_name='misc')],
            'item/pickable_item': [dict(item_id=iid, model='props/something', weight='2', price='5', owner_fading_coef='1', visibility_coef='1')],
            'item/player_item': [dict(item_id=iid, ui_name='original_name', ui_info='original_info', icon_id='21')],
            'item/questible_item': [dict(item_id=iid, is_quest='True')]})
        before = copy.deepcopy(tables.rows)
        importer = DependencyImporter(dict(items=ItemAdapter(tables, 'any_campaign'), assets=Adapter(), strings=Adapter()))
        target = importer.ensure('items', iid)
        self.assertNotEqual(target, iid)
        data = next(v for k,v in importer.files.items() if k.startswith('Libs/Tables/item/item__'))
        item = E.fromstring(data).find('ItemClasses/MiscItem')
        self.assertEqual(item.get('Id'), target)
        self.assertEqual(item.get('UIName'), 'converted-original_name')
        self.assertEqual(item.get('Model'), 'converted-objects/props/something.cgf')
        self.assertEqual(item.get('IsQuestItem'), 'true')
        self.assertIsNone(item.get('IconId')) # A source numeric atlas index is not a native icon name.
        self.assertEqual(tables.rows, before)

    def test_inventory_import_pulls_in_missing_item_before_native_preset(self):
        iid, inv = 'original-item', 'original-inventory'
        tables = Tables({'inventory/inventory': [dict(inventory_id=inv, max_items='', read_only='False', restock_interval='0')],
            'inventory/inventory2item': [dict(inventory_id=inv, item_id=iid, amount='3', amount_random_add='0', health='0.7')]})
        importer = DependencyImporter(dict(inventories=InventoryAdapter(tables, 'any_campaign'), items=Adapter()))
        self.assertIsNotNone(importer.ensure('inventories', inv))
        data = next(v for k,v in importer.files.items() if 'InventoryPreset__' in k)
        item = E.fromstring(data).find('InventoryPresets/InventoryPreset/PresetItem')
        self.assertEqual(item.attrib, dict(ItemClassId='converted-original-item', Amount='3', Health='0.7'))


if __name__ == '__main__': unittest.main()
