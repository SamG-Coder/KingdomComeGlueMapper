from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
import zlib
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from upgrade_source_appearances import imports, owned_shards, validate_resources, verify_bindings, write_delta, appearance_objects, retire_shared_definitions, shared_overlay, retire_shared_assets
from setup_package_upgrade import rewrite
from build_profile_population_upgrade import clone_missing
from upgrade_source_appearances import restore_missing_inventory_presets, preserve_carried_assets

NS = 'gm_person_' + 'a' * 32


def archive(entries):
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        for name, value in entries.items():
            z.writestr(name, value)
    stream.seek(0)
    return zipfile.ZipFile(stream)


class SourceAppearanceUpgradeTests(unittest.TestCase):
    def test_recovery_restores_wrappers_but_keeps_relocated_and_changed_definitions(self):
        shared = 'Libs/Tables/item/InventoryPreset__shared.xml'
        own = 'Libs/Tables/item/InventoryPreset__person_clothes.xml'
        recovery = {shared: b'<database><InventoryPresets version="2">'
            b'<InventoryPreset Name="person_equipped"><InventoryPresetRef Name="person_clothes"/></InventoryPreset>'
            b'<InventoryPreset Name="person_clothes"/><InventoryPreset Name="person_shop_stock" Health="1"/>'
            b'</InventoryPresets></database>'}
        current = {shared: b'<database><InventoryPresets version="2">'
            b'<InventoryPreset Name="person_shop_stock" Health="2"/></InventoryPresets></database>',
            own: b'<database><InventoryPresets><InventoryPreset Name="person_clothes"/></InventoryPresets></database>'}
        patched, restored = restore_missing_inventory_presets(current, recovery)
        self.assertEqual([r['definition'] for r in restored], ['person_equipped'])
        self.assertIn(b'Health="2"', patched[shared])
        self.assertNotIn(b'<InventoryPreset Name="person_clothes"', patched[shared])
        self.assertEqual(restore_missing_inventory_presets({**current, **patched}, recovery), ({}, []))

    def test_full_upgrade_retires_shared_assets_without_mutating_hardlinked_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); base=root/'base'; candidate=root/'candidate'; (base/'Data').mkdir(parents=True)
            relative='Data/shared_characters.pak'
            with zipfile.ZipFile(base/relative,'w') as z:
                z.writestr('objects/characters/gluetravel/old_first/body.skin',b'old skin')
                z.writestr('objects/characters/gluetravel/other/body.skin',b'other person')
                z.writestr('world/data.xml',b'world')
            clone_missing(base,candidate)
            self.assertEqual(owned_shards(base,[relative],'old_first',allow_shared=True),[])
            changed=retire_shared_assets(candidate,[relative],['old_first'],[])
            self.assertEqual(set(changed),{relative})
            with zipfile.ZipFile(candidate/relative) as z:
                self.assertEqual(z.namelist(),['objects/characters/gluetravel/other/body.skin','world/data.xml'])
                self.assertEqual(z.read('world/data.xml'),b'world')
            with zipfile.ZipFile(base/relative) as z:
                self.assertEqual(len(z.namelist()),3)
            with self.assertRaisesRegex(ValueError,'No owned appearance'):
                retire_shared_assets(candidate,[relative],['missing'],[])

    def test_shared_overlay_reads_import_owned_tables_without_touching_world_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'Data').mkdir()
            name='Libs/Tables/Character/CharacterComponent__shared.xml'
            with zipfile.ZipFile(root/'Data/gluemappertravel.pak','w') as z:
                z.writestr(name,b'<database><CharacterComponents><Component Name="old_first_root"/>'
                           b'<Component Name="keep_root"/></CharacterComponents></database>')
                z.writestr('Libs/Storm/story.xml',b'keep')
            baseline=SimpleNamespace(root=root,collisions=['libs/tables/item/inventorypreset__loot.xml'])
            destination=root/'migration.pak'
            incoming={'Libs/Tables/Character/CharacterComponent__old_first.xml':
                      b'<database><Component Name="old_first_root"/></database>'}
            result=shared_overlay(baseline,destination,['old_first'],incoming)
            self.assertEqual(len(result),1)
            with zipfile.ZipFile(destination) as z:
                self.assertEqual(z.namelist(),[name])
                self.assertIn(b'keep_root',z.read(name))
                self.assertNotIn(b'old_first_root',z.read(name))

    def test_shared_migration_is_cumulative_preserves_other_people_and_references(self):
        import xml.etree.ElementTree as ET
        shared = 'Libs/Tables/Character/CharacterComponent__shared.xml'
        own = 'Libs/Tables/Character/CharacterComponent__old_first.xml'
        unrelated = 'Libs/Tables/rpg/soul__shared.xml'
        material = 'Libs/Tables/Character/ClothingMaterial__shared.xml'
        resources = {
            shared: b'<database><Components><Component Name="old_first_root"><Body Name="old_first_body"/></Component>'
                    b'<Component Name="old_second_root"/><Component Name="keep_root"/>'
                    b'<ComponentRef Name="old_first_root"/></Components></database>',
            own: b'<database><Component Name="old_first_root"/></database>',
            unrelated: b'<database><Component Name="old_first_root"/></database>',
            material: b'<database><Materials><Material Name="old_second_fabric"/><Material Name="keep_fabric"/></Materials></database>'}
        incoming = {own: b'<database><Component Name="old_first_root"><Body Name="old_first_body"/></Component></database>',
            'Libs/Tables/Character/CharacterComponent__old_second.xml':
                b'<database><Component Name="old_second_root"/></database>',
            'Libs/Tables/Character/ClothingMaterial__old_second.xml':
                b'<database><Material Name="old_second_fabric"/></database>'}
        changed, removed = retire_shared_definitions(resources, ['old_first', 'old_second'], incoming)
        self.assertEqual(set(changed), {shared, material})
        root = ET.fromstring(changed[shared])
        self.assertEqual([n.get('Name') for n in root.iter('Component')], ['keep_root'])
        self.assertEqual(root.find('.//ComponentRef').get('Name'), 'old_first_root')
        self.assertEqual({r['definition'] for r in removed},
                         {'old_first_root', 'old_first_body', 'old_second_root', 'old_second_fabric'})
        repeated, _ = retire_shared_definitions({**resources, **changed}, ['old_first', 'old_second'], incoming)
        self.assertFalse(repeated)

    def test_migration_preserves_gameplay_presets_with_same_appearance_prefix(self):
        shared = 'Libs/Tables/item/InventoryPreset__shared.xml'
        wrappers = 'Libs/Tables/item/InventoryPreset__old_first_clothes_source_carried.xml'
        own = 'Libs/Tables/item/InventoryPreset__old_first_clothes.xml'
        resources = {
            shared: b'<database><InventoryPreset Name="old_first_clothes"/>'
                b'<InventoryPreset Name="old_first_equipped"><InventoryPresetRef Name="old_first_clothes"/></InventoryPreset>'
                b'<InventoryPreset Name="old_first_shop_stock"/><InventoryPreset Name="old_first_other_clothes"/></database>',
            wrappers: b'<database><InventoryPreset Name="old_first_clothes_source_carried">'
                b'<InventoryPresetRef Name="old_first_clothes"/><InventoryPresetRef Name="source_loot"/>'
                b'</InventoryPreset></database>',
            own: b'<database><InventoryPreset Name="old_first_clothes"/></database>'}
        incoming = {own: resources[own]}
        changed, retired = retire_shared_definitions(resources, ['old_first'], incoming)
        self.assertEqual(set(changed), {shared})
        self.assertEqual([r['definition'] for r in retired], ['old_first_clothes'])
        self.assertIn(b'Name="old_first_equipped"', changed[shared])
        self.assertIn(b'Name="old_first_shop_stock"', changed[shared])
        self.assertIn(b'Name="old_first_other_clothes"', changed[shared])
        self.assertIn(b'InventoryPresetRef Name="old_first_clothes"', changed[shared])

    def test_no_replacement_definitions_means_no_retirement(self):
        resources = {'Libs/Tables/item/InventoryPreset__shared.xml':
                     b'<database><InventoryPreset Name="old_first_clothes"/></database>'}
        self.assertEqual(retire_shared_definitions(resources, ['old_first'], {}), ({}, []))

    def test_final_dependencies_include_authored_lods_and_streams_but_use_native_donors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); folder=root/'objects/characters/example';folder.mkdir(parents=True)
            for name in ('body.skin','body_lod1.skin','fabric.dds','fabric.dds.1','unused.dds'):
                (folder/name).write_bytes(b'fixture')
            (folder/'body.mtl').write_text('<Material><Textures><Texture File="objects/characters/example/fabric.dds"/>'
                                         '<Texture File="native/detail.dds"/><Texture File="nearest_cubemap"/></Textures></Material>')
            resources={'Libs/Tables/Character/CharacterComponent__test.xml':b'<database><CharacterComponents><Component Name="root" FilePath="example"><DerivedComponents><Body Name="body"><Elements><SkinElement EquipmentPart="torso" Model="body.skin" Material="body.mtl"/></Elements></Body></DerivedComponents></Component></CharacterComponents></database>'}
            result=appearance_objects(root,resources,SimpleNamespace(index={'native/detail.dds':None}))
            self.assertEqual({Path(p).name for p in result},{'body.skin','body_lod1.skin','body.mtl','fabric.dds','fabric.dds.1'})
            with self.assertRaisesRegex(ValueError,'Missing final appearance assets'):
                appearance_objects(root,resources)

    def test_named_legacy_receipt_namespaces_are_preserved(self):
        row=dict(person='source_npc',namespace='gm_original_alias',appearance='character_person_appearance')
        with archive({'Libs/GlueMapper/PersonImport/gm_original_alias.json':json.dumps(row)}) as z:
            self.assertEqual(imports(z),{'source_npc':'gm_original_alias'})

    def test_all_imported_uses_receipts_and_rejects_unsupported_ownership(self):
        row = dict(person='source_npc', namespace=NS, appearance='character_person_appearance')
        name = 'Libs/GlueMapper/PersonImport/'+NS+'.json'
        with archive({name:json.dumps(row)}) as z:
            self.assertEqual(imports(z), {'source_npc':NS})
        row['appearance'] = 'another_converter'
        with archive({name:json.dumps(row)}) as z:
            with self.assertRaisesRegex(ValueError, 'Unsupported appearance adapter'):
                imports(z)

    def test_developer_delta_keeps_changed_assets_and_tables_without_copying_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); appearance = root/'person'
            obj = appearance/'objects/characters/gluetravel'/NS; obj.mkdir(parents=True)
            (obj/'same.skin').write_bytes(b'same')
            (obj/'changed.skin').write_bytes(b'new')
            relative = 'Libs/Tables/Character/CharacterComponent__'+NS+'.xml'
            resource = appearance/relative; resource.parent.mkdir(parents=True)
            component = ('<database><CharacterComponents><Component Name="root" FilePath="gluetravel/'+NS+'"><DerivedComponents>'
                         '<Body Name="body"><Elements><SkinElement EquipmentPart="torso" Model="same.skin"/>'
                         '<SkinElement EquipmentPart="arms" Model="changed.skin"/></Elements></Body>'
                         '</DerivedComponents></Component></CharacterComponents></database>').encode()
            resource.write_bytes(component)
            (obj/'unused.skin').write_bytes(b'conversion intermediate')
            base = SimpleNamespace(index={
                (obj/'same.skin').relative_to(appearance).as_posix(): (None,None,zlib.crc32(b'same'),4),
                (obj/'changed.skin').relative_to(appearance).as_posix(): (None,None,zlib.crc32(b'old'),3)})
            result = write_delta(appearance, root/'delta.pak', NS, base)
            self.assertEqual(result['changed_assets'], 2)
            with zipfile.ZipFile(root/'delta.pak') as z:
                self.assertEqual(z.read(relative), component)
                self.assertEqual(z.read((obj/'changed.skin').relative_to(appearance).as_posix()), b'new')
                self.assertFalse(any(n.endswith('same.skin') for n in z.namelist()))
                self.assertFalse(any(n.endswith('unused.skin') for n in z.namelist()))
            self.assertFalse((root/'delta.pending').exists())

    def test_import_requires_explicit_unambiguous_owned_identity(self):
        row = dict(person='source_npc', namespace=NS, appearance='character_person_appearance')
        with archive({'Libs/GlueMapper/PersonImport/' + NS + '.json': json.dumps(row)}) as z:
            self.assertEqual(imports(z, ['source_npc']), {'source_npc': NS})
            with self.assertRaisesRegex(ValueError, 'No imported'):
                imports(z, ['unregistered'])
        row['namespace'] = '../outside'
        with archive({'Libs/GlueMapper/PersonImport/' + NS + '.json': json.dumps(row)}) as z:
            with self.assertRaisesRegex(ValueError, 'ownership'):
                imports(z, ['source_npc'])

    def test_only_appearance_tables_are_accepted(self):
        validate_resources({'Libs/Tables/Character/CharacterComponent__' + NS + '.xml': b'<database/>'}, NS)
        validate_resources({'Libs/Tables/item/clothing_preset__' + NS + '.xml': b'<database/>'}, NS)
        for path in ('Libs/Storm/storm.xml', 'Libs/Tables/item/item__another.xml',
                     'Libs/Tables/rpg/soul__' + NS + '.xml'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'unrelated'):
                validate_resources({path: b'<database/>'}, NS)

    def test_changed_storm_identities_require_explicit_migration(self):
        entries = {'Libs/Storm/appearance/' + NS + '.xml':
                   '<storm><rules><rule><operations><setBody name="body"/></operations></rule></rules></storm>'}
        with archive(entries) as z:
            character = dict(appearance={'body': 'body'}, inventory=NS + '_clothes')
            verify_bindings(z, character, NS)
            character['appearance']['body'] = 'new_body'
            with self.assertRaisesRegex(ValueError, 'identities changed'):
                verify_bindings(z, character, NS)

    def test_refuses_shard_with_weapon_or_unrelated_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'Data').mkdir()
            relative = 'Data/' + NS + '.pak'
            with zipfile.ZipFile(root / relative, 'w') as z:
                z.writestr('objects/characters/gluetravel/' + NS + '/body.skin', b'body')
            self.assertEqual(owned_shards(root, {relative: 'hash'}, NS), [relative])
            with zipfile.ZipFile(root / relative, 'a') as z:
                z.writestr('objects/weapons/sword.cgf', b'weapon')
            with self.assertRaisesRegex(ValueError, 'unrelated assets'):
                owned_shards(root, {relative: 'hash'}, NS)

    def test_mixed_actor_shards_require_explicit_equipment_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'Data').mkdir(); relative='Data/'+NS+'_03.pak'
            prefix='objects/gluecampaign/'+NS+'_weapons/'
            with zipfile.ZipFile(root/relative,'w') as z:
                z.writestr(prefix+'sword.cgf',b'sword')
            with self.assertRaisesRegex(ValueError,'unrelated assets'):
                owned_shards(root,[relative],NS)
            self.assertEqual(owned_shards(root,[relative],NS,allow_equipment=True),[relative])
            for foreign in ('objects/gluecampaign/other_weapons/sword.cgf',
                            prefix+'../other/file', 'objects/characters/gluetravel/'+NS+'/../other/body.skin'):
                with zipfile.ZipFile(root/relative,'w') as z:z.writestr(foreign,b'other')
                with self.assertRaisesRegex(ValueError,'unrelated assets'):
                    owned_shards(root,[relative],NS,allow_equipment=True)

    def test_preserved_equipment_is_exact_bounded_and_does_not_edit_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); base=root/'base'; candidate=root/'candidate'; (base/'Data').mkdir(parents=True)
            relative='Data/'+NS+'_03.pak'; prefix='objects/gluecampaign/'+NS+'_weapons/'
            equipment={prefix+n:bytes([i])*700 for i,n in enumerate(('sword.cgf','sword.dds','sword.dds.1'),1)}
            with zipfile.ZipFile(base/relative,'w') as z:
                z.writestr('objects/characters/gluetravel/'+NS+'/body.skin',b'old body')
                for name,blob in equipment.items():z.writestr(name,blob)
            original=(base/relative).read_bytes(); clone_missing(base,candidate)
            kept=preserve_carried_assets(candidate,NS,[relative],limit=1024)
            self.assertEqual(len(kept),3)
            actual={}
            for name in kept:
                self.assertLessEqual((candidate/name).stat().st_size,1024)
                with zipfile.ZipFile(candidate/name) as z:
                    self.assertIsNone(z.testzip()); actual.update({n:z.read(n) for n in z.namelist()})
            self.assertEqual(actual,equipment)
            self.assertEqual((base/relative).read_bytes(),original)
            self.assertEqual(preserve_carried_assets(candidate,NS,[relative],limit=1024),kept)
            self.assertEqual(owned_shards(candidate,[relative,*kept],NS,allow_equipment=True),[relative])
            first=next(iter(kept)); (candidate/first).unlink(); (candidate/first).write_bytes(b'different existing file')
            with self.assertRaisesRegex(ValueError,'different contents'):
                preserve_carried_assets(candidate,NS,[relative],limit=1024)
            self.assertEqual((candidate/first).read_bytes(),b'different existing file')

    def test_conflicting_duplicate_carried_assets_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Data').mkdir(); paths=[]
            for suffix,blob in (('',b'first'),('_01',b'conflict')):
                relative='Data/'+NS+suffix+'.pak';paths.append(relative)
                with zipfile.ZipFile(root/relative,'w') as z:
                    z.writestr('objects/gluecampaign/'+NS+'_weapons/sword.cgf',blob)
            with self.assertRaisesRegex(ValueError,'Conflicting carried'):
                preserve_carried_assets(root,NS,paths)
            self.assertEqual(sorted(p.name for p in (root/'Data').iterdir()),sorted(Path(p).name for p in paths))

    def test_copy_on_replace_preserves_baseline_and_gameplay_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = root / 'baseline', root / 'candidate'
            baseline.mkdir()
            with zipfile.ZipFile(baseline / 'travel.pak', 'w') as z:
                z.writestr('appearance.xml', b'old')
                z.writestr('quests.xml', b'original quests')
                z.writestr('scheduler.xml', b'original schedules')
            original = (baseline / 'travel.pak').read_bytes()
            clone_missing(baseline, candidate)
            rewrite(candidate, 'travel.pak', {'appearance.xml': b'new'})
            self.assertEqual((baseline / 'travel.pak').read_bytes(), original)
            with zipfile.ZipFile(candidate / 'travel.pak') as z:
                self.assertEqual(z.read('appearance.xml'), b'new')
                self.assertEqual(z.read('quests.xml'), b'original quests')
                self.assertEqual(z.read('scheduler.xml'), b'original schedules')


if __name__ == '__main__':
    unittest.main()
