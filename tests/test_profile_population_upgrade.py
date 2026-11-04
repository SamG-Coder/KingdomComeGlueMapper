"""Profile selection and service conversion must not depend on NPC names."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from profile_population_upgrade import shops_for_people, validate_people, validate_services, retain_work
from character_market_jobs import resolve, contract
from merchant_stash import source_stashes, source_stash
from source_merchant_upgrade import bind_storage
from upgrade_map import xml


class PopulationTests(unittest.TestCase):
    def test_receipt_refresh_does_not_change_hardlinked_installed_receipt(self):
        from build_profile_population_upgrade import refresh_receipt
        from upgrade_map import digest
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'package';package.mkdir()
            original=root/'original.json';original.write_text('{"files": {"obsolete": "old"}}')
            os.link(original,package/'gluemapper-install.json')
            (package/'new.pak').write_bytes(b'new payload')
            with patch('setup_campaign.verify_package') as validate:
                refresh_receipt(package,dict(version=1,profiles=['any_profile'],people=1))
                validate.assert_called_once_with(package,hashes=False)
            self.assertEqual(json.loads(original.read_text()),dict(files=dict(obsolete='old')))
            receipt=json.loads((package/'gluemapper-install.json').read_text())
            self.assertEqual(receipt['files'],{'new.pak':digest(package/'new.pak')})
            self.assertFalse(receipt['profile_population']['runtime_verified'])

    def test_repeat_upgrade_retains_unresolved_work_without_duplicate_reports(self):
        previous=dict(converted=[dict(person='existing',job='sweeping',pending=['cooking'])],
                      pending=[dict(person='legacy'),dict(person='outside_profile')])
        result=retain_work(previous,dict(converted=[dict(person='new',pending=[])],pending=[]),
                           ['existing','legacy','new'])
        self.assertEqual([r['person'] for r in result['pending']],['legacy'])
        self.assertEqual([r['person'] for r in result['converted']],['existing','new'])
        self.assertFalse(result['complete'])
        self.assertEqual(retain_work(result,dict(converted=[],pending=[]),['existing','legacy','new']),result)
    def test_shop_membership_follows_profile_souls_not_names_or_faction(self):
        roster=[dict(name='any_person',soul_id='soul')]
        keepers=[dict(keeper_id='soul',shop_id='8'),dict(keeper_id='not_in_profile',shop_id='9')]
        shops=[dict(shop_id='8'),dict(shop_id='9')]
        found=shops_for_people(roster,keepers,shops)
        self.assertEqual([(s['person'],s['shop']['shop_id']) for s in found],[('any_person','8')])
        with self.assertRaisesRegex(ValueError,'competing placements'):
            shops_for_people(roster+roster,keepers,shops)
        with self.assertRaisesRegex(ValueError,'missing shop'):
            shops_for_people(roster,keepers,[])
        with self.assertRaisesRegex(ValueError,'multi-keeper'):
            shops_for_people(roster,keepers+keepers[:1],shops)

    def test_every_selected_person_must_have_one_soul_and_placed_actor(self):
        roster=[dict(name='any_person',soul_id='soul')]
        mission=E.fromstring('<Objects><Entity Name="any_person" EntityGuid="guid" EntityClass="NPC"/></Objects>')
        souls=E.fromstring('<World><SoulList><Souls><Soul><SharedSoulGuid>soul</SharedSoulGuid><EntityGuid>guid</EntityGuid></Soul></Souls></SoulList></World>')
        self.assertEqual(validate_people(roster,mission,souls),1)
        E.SubElement(mission,'Entity',Name='anonymous_geometry_1')
        E.SubElement(mission,'Entity',Name='anonymous_geometry_2')
        self.assertEqual(validate_people(roster,mission,souls),1)
        souls.find('SoulList/Souls').append(copy.deepcopy(souls.find('.//Soul')))
        with self.assertRaisesRegex(ValueError,'duplicate population soul'): validate_people(roster,mission,souls)

    def test_selling_location_uses_merchant_point_not_area_inside(self):
        mission=b'<Objects><Entity EntityId="1" EntityClass="TagPoint" Pos="1,2,3"/><Entity EntityId="2" EntityClass="TagPoint" Pos="8,9,10"/></Objects>'
        entry=dict(area=dict(EntityId='3'),target_links=[dict(Name='inside',TargetId='1'),
                   dict(Name='merchantPoint',TargetId='2')])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'level.pak'
            with zipfile.ZipFile(path,'w') as z:z.writestr('objects_mission0.xml',mission)
            with zipfile.ZipFile(path) as z:converted=resolve(entry,z)
        self.assertEqual(converted['arrival']['Pos'],'8,9,10')
        self.assertNotIn('arrival',entry)

    def test_native_seller_must_own_movement_and_service_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Data').mkdir()
            with zipfile.ZipFile(root/'Data/Tables.pak','w') as z:
                z.writestr('Libs/Tables/ai/smartEntity/SmartEntity__so_seller.xml',
                    '<database><SmartEntityTemplate DatabaseId="native"><BehaviorTemplates><SmartBehaviorTemplate Name="seller_market" InitialState="Enabled"><TreeLocation FileName="market.xml" TreeName="sell"/></SmartBehaviorTemplate></BehaviorTemplates></SmartEntityTemplate></database>')
            for readiness in ('<EntityContextElement context="shop_sellerReadyToSell"/>',''):
                with zipfile.ZipFile(root/'Data/Scripts.pak','w') as z:
                    z.writestr('AI/market.xml','<BehaviorTrees><BehaviorTree name="sell"><Root><ExactMove/>'+readiness+'</Root></BehaviorTree></BehaviorTrees>')
                if readiness:self.assertEqual(contract(root).get('DatabaseId'),'native')
                else:
                    with self.assertRaisesRegex(ValueError,'readiness'):contract(root)

    def test_all_linked_storage_is_selected_without_display_props(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Data/Levels/custom').mkdir(parents=True)
            mission=E.Element('Objects')
            for number,kind in [(1,'Stash'),(2,'Stash'),(3,'AreaBox')]:
                E.SubElement(mission,'Entity',EntityId=str(number),EntityGuid=str(number),EntityClass=kind)
            shop=E.fromstring('<Entity><EntityLinks><Link TargetId="1"/><Link TargetId="2"/><Link TargetId="3"/></EntityLinks></Entity>')
            with zipfile.ZipFile(root/'Data/Levels/custom/level.pak','w') as z:z.writestr('objects_mission0.xml',E.tostring(mission))
            found=source_stashes(root,shop,'custom')
            self.assertEqual([e.get('EntityId') for e in found],['1','2'])
            with patch('merchant_stash.source_stashes',return_value=found):
                with self.assertRaisesRegex(ValueError,'exactly one'):source_stash(root,shop)

    def test_existing_physical_container_is_reused_with_its_lock_and_inventory(self):
        files={'objects_mission0.xml':b'<Objects><Entity Name="service_shop" EntityGuid="00000001-0000-0000" EntityId="1"/><Entity Name="person" EntityGuid="00000002-0000-0000" EntityId="2"/><Entity Name="gm_container_3" EntityGuid="00000003-0000-0000" EntityId="3" EntityClass="Stash"><Properties object_Model="converted.cga" Preserved="lock-and-loot"/></Entity></Objects>',
               'waitinglinks.xml':b'<StaticLinksInfo version="1"><WaitingLinks/></StaticLinksInfo>'}
        authored=E.fromstring('<Entity EntityId="3"><Properties object_Model="source.cga"><Database guidInventoryDBId="0"/><Lock/></Properties></Entity>')
        with patch('ordinary_containers.properties',return_value=E.Element('Properties')):
            result,_=bind_storage(files,dict(name='person',shop_entity=None),None,'service','converted.cga',authored=authored)
        root=E.fromstring(result['objects_mission0.xml'])
        self.assertEqual(len(list(root)),3)
        self.assertEqual(root.find("Entity[@Name='gm_container_3']/Properties").get('Preserved'),'lock-and-loot')
        self.assertEqual(root.find("Entity[@Name='service_shop']/EntityLinks/Link").get('TargetId'),'3')

    def test_storage_on_a_parsed_mission_matches_the_serialized_path(self):
        files={'objects_mission0.xml':b'<Objects><Entity Name="service_shop" EntityGuid="00000001-0000-0000" EntityId="1"/><Entity Name="person" EntityGuid="00000002-0000-0000" EntityId="2"/></Objects>',
               'waitinglinks.xml':b'<StaticLinksInfo version="1"><WaitingLinks/></StaticLinksInfo>'}
        authored=E.fromstring('<Entity EntityId="3" Pos="1,2,3"><Properties object_Model="source.cga"><Database guidInventoryDBId="0"/><Lock/></Properties></Entity>')
        with patch('ordinary_containers.properties',side_effect=lambda *a:E.Element('Properties')):
            serialized,report=bind_storage(files,dict(name='person',shop_entity=None),None,'service','converted.cga',authored=authored)
            mission=E.fromstring(files['objects_mission0.xml'])
            parsed,same=bind_storage(files,dict(name='person',shop_entity=None),None,'service','converted.cga',authored=authored,mission=mission)
        self.assertEqual(parsed['objects_mission0.xml'],files['objects_mission0.xml'])
        self.assertEqual(xml(mission),serialized['objects_mission0.xml'])
        self.assertEqual((parsed['waitinglinks.xml'],same),(serialized['waitinglinks.xml'],report))

    def test_independent_shop_chest_inventory_cannot_be_erased(self):
        original=E.fromstring('<Entity EntityId="3"><Properties><Database guidInventoryDBId="authored-loot"/></Properties></Entity>')
        with self.assertRaisesRegex(ValueError,'independent inventory'):
            bind_storage({},dict(shop_entity=None),None,'service','model',authored=original)

    def test_existing_shop_requires_real_stock_and_keeper_not_just_an_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            package=Path(tmp);(package/'Data/Levels/world').mkdir(parents=True)
            (package/'mod.manifest').write_text('<mod><info><modid>test</modid></info></mod>')
            mission=b'<Objects><Entity Name="seller" EntityGuid="npc" EntityClass="NPC"><EntityLinks><Link Name="shopKeeper" TargetGuid="shop"/></EntityLinks></Entity><Entity EntityGuid="shop" EntityClass="Shop"><Properties iShopId="20008" sShopName="store"/></Entity></Objects>'
            scheduler=b'<database/>'
            def write_level(blob):
                with zipfile.ZipFile(package/'Data/Levels/world/level.pak','w') as z:
                    z.writestr('objects_mission0.xml',blob);z.writestr('tables/ai/scheduler.xml',scheduler)
            write_level(mission)
            with zipfile.ZipFile(package/'Data/gluemappertravel.pak','w') as z:
                z.writestr('Mods/test/Data/Levels/world/tables/ai/scheduler.xml',scheduler)
                z.writestr('Libs/Tables/shop/shop__test.xml','<database><Shops><ShopData shop_id="20008" shop_name="store" inventory_preset="stock"/></Shops></database>')
                z.writestr('Libs/Tables/item/InventoryPreset__test.xml','<database><InventoryPresets><InventoryPreset Name="stock"><PresetItem Name="item" ItemClassId="id" Amount="2"/></InventoryPreset></InventoryPresets></database>')
            shops=[dict(person='seller',shop=dict(shop_id='8'))]
            with patch('container_items.item_patches',return_value=[]),patch('world_item_definitions.native_items',return_value={'id':E.Element('Item',Name='item')}),patch('character_person_activity.validate_scheduler_targets'):
                self.assertEqual(validate_services(package,None,shops,'world')[0]['stock_rows'],1)
                with self.assertRaisesRegex(ValueError,'storage is missing'):
                    validate_services(package,None,shops,'world',require_storage=['20008'])
                write_level(mission.replace(b'TargetGuid="shop"',b'TargetGuid="wrong"'))
                with self.assertRaisesRegex(ValueError,'assigned keeper'):
                    validate_services(package,None,shops,'world')
                write_level(mission)
                with patch('world_item_definitions.native_items',return_value={}):
                    with self.assertRaisesRegex(ValueError,'stock item is unresolved'):
                        validate_services(package,None,shops,'world')


if __name__=='__main__':unittest.main()
