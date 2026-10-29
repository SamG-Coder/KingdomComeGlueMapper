"""Cross-import caches retain precedence and invalidate after package writes."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import campaign_sources
import character_person
import container_items
from region_travel_services import resolve_merchant


def write_pak(path, members):
    previous = path.stat().st_mtime_ns if path.exists() else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    os.utime(path, ns=(path.stat().st_atime_ns, max(path.stat().st_mtime_ns, previous + 1_000_000)))


class SourceReadCacheTests(unittest.TestCase):
    def setUp(self):
        campaign_sources._RESOLVED.clear()
        character_person._LAYER_INDEX.clear()
        container_items._PAK_ITEM_PATCHES.clear()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_shared_retail_cache_closes_handles_and_isolates_provenance(self):
        path = self.root / 'Data/Scripts.pak'
        write_pak(path, {'file.xml': b'first'})
        request = {'file': ('Scripts.pak', 'file.xml')}
        first = campaign_sources.retail_sources(self.root, request)['file']
        first['candidates'][0]['sha256'] = 'mutated'
        with patch.object(campaign_sources, 'read', side_effect=AssertionError('cached read')):
            second = campaign_sources.retail_sources(self.root, request)['file']
        self.assertNotEqual(first['candidates'], second['candidates'])
        write_pak(path, {'file.xml': b'other'})  # same byte length, changed mtime
        self.assertEqual(campaign_sources.retail_sources(self.root, request)['file']['data'], b'other')
        path.unlink()  # no shared open handle on Windows

    def test_new_dlc_and_patch_removal_invalidate_positive_and_negative_results(self):
        write_pak(self.root / 'Data/Scripts.pak', {'file.xml': b'base'})
        request = {'file': ('Scripts.pak', 'dlc.xml')}
        with self.assertRaises(campaign_sources.MissingRetailSource):
            campaign_sources.retail_sources(self.root, request)
        write_pak(self.root / 'Data/Scripts_DLC1.pak', {'dlc.xml': b'dlc'})
        self.assertEqual(campaign_sources.retail_sources(self.root, request)['file']['data'], b'dlc')
        patch_path = self.root / 'Data/patch/patch_12.pak'
        write_pak(patch_path, {'dlc.xml': b'patched'})
        self.assertEqual(campaign_sources.retail_sources(self.root, request)['file']['data'], b'patched')
        patch_path.unlink()
        self.assertEqual(campaign_sources.retail_sources(self.root, request)['file']['data'], b'dlc')

    def test_item_cache_invalidates_changed_removed_and_added_archives(self):
        path = self.root / 'Data/items.pak'
        entry = 'Libs/Tables/item/item__a.xml'
        write_pak(path, {entry: b'first'})
        self.assertEqual(container_items.item_patches(self.root), [b'first'])
        with patch.object(container_items, 'read', side_effect=AssertionError('cached read')):
            self.assertEqual(container_items.item_patches(self.root), [b'first'])
        write_pak(path, {entry: b'other'})
        self.assertEqual(container_items.item_patches(self.root), [b'other'])
        path.unlink()
        self.assertEqual(container_items.item_patches(self.root), [])

    def test_link_index_reuses_layer_scan_but_parses_fresh_entities(self):
        path = self.root / 'level.pak'
        write_pak(path, {'layers/a.xml': '<Objects><Entity EntityId="2" Name="Home"/></Objects>',
                         'layers/b.xml': '<Objects><Entity EntityId="3"/></Objects>'})
        actor = ET.fromstring('<Entity EntityId="1"><EntityLinks><Link TargetId="2"/></EntityLinks></Entity>')
        with zipfile.ZipFile(path) as archive:
            first = character_person.resolve_link_targets(archive, actor, [actor], {})
        first[-1].set('Name', 'mutated')
        with zipfile.ZipFile(path) as archive, patch.object(character_person, 'read', wraps=character_person.read) as read:
            second = character_person.resolve_link_targets(archive, actor, [actor], {})
            self.assertEqual(second[-1].get('Name'), 'Home')
            self.assertEqual([call.args[1] for call in read.call_args_list], ['layers/a.xml'])
        write_pak(path, {'layers/b.xml': '<Objects><Entity EntityId="2" Name="Moved"/></Objects>'})
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(character_person.resolve_link_targets(archive, actor, [actor], {})[-1].get('Name'), 'Moved')

    def test_merchant_prefilter_preserves_utf16_and_xml_references(self):
        actor = ET.fromstring('<Entity EntityId="1"><EntityLinks><Link Name="Work[0]" TargetId="2"/></EntityLinks></Entity>')
        person = dict(actor=actor, soul={'soul_id': 'person'}, provenance={'actor_sources': {}, 'tables': {}})
        shop = dict(shop_id='5', shop_type_id='1', amount_multiplier='1')
        class Tables:
            cache = {}
            def row(self, table, field, value):
                return {'shop_id': '5'} if table.endswith('shopkeeper') else shop
            def get(self, table):
                return {'rows': [dict(shop_type_id='1', item_id='item', amount='1')] if table.endswith('shop_type2item') else []}
        plain = '<Objects><Entity EntityId="3" EntityClass="Shop"><Properties iShopId="5"/></Entity></Objects>'
        for blob in (plain.encode('utf-16'), plain.replace('"Shop"', '"Sh&#111;p"').encode()):
            write_pak(self.root / 'Data/Levels/rataje/level.pak', {
                'objects_mission0.xml': '<Objects><Entity EntityId="2"/></Objects>', 'layers/shop.xml': blob})
            with patch('region_travel_services.resolve_person', return_value=copy.deepcopy(person)), \
                 patch('region_travel_services.SourceTables', return_value=Tables()):
                self.assertEqual(resolve_merchant(self.root, 'merchant')['shop_entity'].get('EntityId'), '3')


if __name__ == '__main__':
    unittest.main()
