import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_person_shop import register_shop_activity, register_shop_service
from campaign_entity_links import native_guid


class PersonShopTests(unittest.TestCase):
    def test_live_service_binding_is_level_owned_and_independent_of_quest(self):
        base = b'<Database><Skald><Level Name="imported"><Nodes><State Name="previous"/></Nodes></Level></Skald></Database>'
        result = register_shop_service(base, 'source-person-soul', 'imported_shop')
        level = ET.fromstring(result).find('Skald/Level')
        self.assertEqual(level.find('Assets/SoulAsset').get('SharedSoulGuids'), 'source-person-soul')
        self.assertEqual(level.find('Assets/ShopAsset').get('Name'), 'imported_shop')
        self.assertIsNotNone(level.find('Nodes/State[@Name="previous"]'))
        owner = level.find('Nodes/SetOwner')
        self.assertEqual(owner.find('Asset[@Name="What"]').get('Alias'), 'imported_shop')
        self.assertEqual(owner.find('Asset[@Name="Who"]').get('Alias'), 'imported_shop_keeper')
        self.assertEqual(owner.find('Edge[@To="IsActive"]').get('From'), 'imported_shop_service_active.State')
        ready = level.find('Nodes/SetEntityContext')
        self.assertEqual(ready.find('Asset[@Name="Souls"]').get('Alias'), 'imported_shop_keeper')
        self.assertEqual(ready.find('Constant[@Name="Context"]').get('Value'), 'shop_sellerReadyToSell')
        active = level.find('Nodes/State[@Name="imported_shop_service_active"]')
        self.assertEqual([e.get('From') for e in active.findall('Edge')], ['OnWake', 'OnLevelSwitched'])
        self.assertTrue(all(e.get('To') == 'SetTrue' for e in active.findall('Edge')))
        self.assertEqual(ready.find('Edge[@To="IsActive"]').get('From'), 'imported_shop_service_active.State')
        with self.assertRaisesRegex(ValueError, 'already registered'):
            register_shop_service(result, 'source-person-soul', 'imported_shop')
        with self.assertRaisesRegex(ValueError, 'hosted by a Level'):
            register_shop_service(b'<Database><Skald><Quest/></Skald></Database>', 'soul', 'shop')

    def test_shop_role_and_lifecycle_preserve_existing_activity(self):
        base = b'''<database><Schedulers version="1">
          <C_SmartHub EntityGuid="100"><Links><S_ActivityLink TargetGuid="200">
            <Parameters Priority="0"/></S_ActivityLink></Links></C_SmartHub>
          <C_SmartHub EntityGuid="200"><Links><S_ActivityLink TargetGuid="300">
            <Parameters BehaviorName="use"/></S_ActivityLink></Links></C_SmartHub>
          <C_SmartHub EntityGuid="300"/>
          <C_SmartHub EntityGuid="999" StupidHub="false"/>
        </Schedulers></database>'''
        npc, hub, shop = [ET.Element('Entity', EntityGuid=native_guid(n)) for n in (100, 200, 400)]
        template = (
            ET.Element('ElementInitializerOpenShop', Role='innkeeper', TargetGuid='900'),
            ET.Element('ElementInitializerAddContext', Role='innkeeper', TargetGuid='900',
                       Context='shop_sellerReadyToSell', Invert='false'),
            ET.Element('C_SmartHub', EntityGuid='900', IgnoreDeadEnds='false', StupidHub='false'))
        with patch('character_person_shop.native_shop_activity', return_value=template):
            result = register_shop_activity(base, '', npc, hub, shop)
        rows = ET.fromstring(result).find('Schedulers')
        actor = rows.find("C_SmartHub[@EntityGuid='100']")
        self.assertEqual(actor.find('.//Parameters').get('ProvidedRole'), 'innkeeper')
        work = rows.find("C_SmartHub[@EntityGuid='200']")
        self.assertEqual(work.find('.//S_ActivityLink').get('TargetGuid'), '300')
        self.assertEqual(work.find('.//Parameters').get('BehaviorName'), 'use')
        effects = work.findall('PostSearchData/SmartHubPostSearchData/ElementInitializers/*')
        self.assertEqual([e.tag for e in effects], ['ElementInitializerOpenShop', 'ElementInitializerAddContext'])
        self.assertEqual({e.get('TargetGuid') for e in effects}, {'400'})
        self.assertEqual({e.get('Role') for e in effects}, {'innkeeper'})
        self.assertEqual(effects[1].get('Context'), 'shop_sellerReadyToSell')
        self.assertEqual(len(rows.find("C_SmartHub[@EntityGuid='400']")), 0)
        self.assertEqual(ET.tostring(rows.find("C_SmartHub[@EntityGuid='999']")).strip(),
                         b'<C_SmartHub EntityGuid="999" StupidHub="false" />')
        with patch('character_person_shop.native_shop_activity', return_value=template):
            with self.assertRaisesRegex(ValueError, 'already registered'):
                register_shop_activity(result, '', npc, hub, shop)


if __name__ == '__main__': unittest.main()
