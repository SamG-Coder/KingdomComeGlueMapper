import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_population import placement_person, source_membership, migrate_texture_cache, stage
from character_population_factions import register_catalog
from character_person_ai import register_factions, FACTION_PATH
from character_person_appearance import resolve_default_body, resolve_exported_variant
from character_texture_cache import TextureCache
from package_character_population import add_people
from character_person_brain import upgrade_helper_tree, NODE_CONTRACTS
from character_person_ai import native_brain_and_class
from character_person_world import same_area_geometry
from campaign_trigger_areas import TriggerArea
from region_travel_locations import merge_missing_strings
from region_travel_tables import managed_patches
from build_npc_probe import asset_path
from character_source_clothing import garment_layout


class PopulationTests(unittest.TestCase):
    def test_cache_snapshot_has_its_own_lock_and_preserves_live_writer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'population.lock').write_text('writer')
            (root / 'population.json').write_text('live checkpoint')
            with patch('character_population._stage', return_value={'snapshot': True}) as build:
                result = stage('source', 'target', 'base', root, cached_only=True)
                self.assertEqual(result, {'snapshot': True})
                self.assertTrue(build.call_args.kwargs['cached_only'])
            self.assertEqual((root / 'population.lock').read_text(), 'writer')
            self.assertEqual((root / 'population.json').read_text(), 'live checkpoint')
            self.assertFalse((root / 'population-snapshot.lock').exists())
            with self.assertRaises(FileExistsError): stage('source', 'target', 'base', root)

    def test_retired_asset_suffix_and_named_dress_use_source_metadata(self):
        index = {'objects/characters/humans/hair/s1_hair_005.skin': None}
        part = dict(kind='hair', model='s1_hair_005_dontuse', material='s1_hair_005')
        self.assertEqual(asset_path(index, part, 'model', '.skin'), next(iter(index)))
        with self.assertRaises(ValueError):
            asset_path(index, dict(part, material='different'), 'model', '.skin')
        part = dict(model='bespoke_dress', gender_id='2', armor_archetype_name='BodyClothShirtLong_longSleeves')
        self.assertEqual(garment_layout(part), (2, 2, 1))

    def test_repeated_item_description_is_merged_once_but_conflicts_fail(self):
        row = '<Row><Cell>item</Cell><Cell>Description</Cell><Cell>Description</Cell></Row>'
        merged = merge_missing_strings(b'<Table/>'.replace(b'/>', b'></Table>'), ('<Table>'+row+row+'</Table>').encode())
        self.assertEqual(len(E.fromstring(merged)), 1)
        bad = row.replace('Description', 'Other')
        with self.assertRaisesRegex(ValueError, 'Conflicting incoming'):
            merge_missing_strings(b'<Table></Table>', ('<Table>'+row+bad+'</Table>').encode())

    def test_source_helpers_keep_idle_brain_and_no_faction_or_social_class(self):
        brains = E.fromstring('<database><brains><brain brain_name="npc_default" brain_id="idle"/></brains></database>')
        classes = E.fromstring('<database><social_classes><social_class social_class_name="none" social_class_id="0"/></social_classes></database>')
        for name in ('npc_dummyWait', 'npc_test_base'):
            ai = dict(source_brain=dict(brain_name=name), source_social_class=None,
                      source_factions=[], source_soul=dict(faction='0'))
            self.assertEqual(native_brain_and_class(ai, brains, classes), ('idle', '0'))
            self.assertIsNone(register_factions({}, ai))

    def test_helper_tree_preserves_inbox_and_visibility_with_native_expressions(self):
        source = '''<BehaviorTrees><BehaviorTree name="root"><Variables><Variable name="hit" type="hitReaction"/></Variables>
          <Root OneTimeOnly='"false"'><Behavior><Parallel successMode='"Any"' failureMode='"Any"'>
          <LODLock><Sequence><SetVisibility ItemWUID='"this.id"' Visibility='"false"'/></Sequence></LODLock>
          <ProcessMessage variable='"hit"' inbox='"hitReaction"' timeout='"-1"' condition='""'><Success/></ProcessMessage>
          </Parallel></Behavior></Root></BehaviorTree></BehaviorTrees>'''
        catalog = dict(nodes={name: {'attributes': list(fields)} for name, fields in NODE_CONTRACTS.items()},
                       variable_types=['hitReaction'])
        converted = E.fromstring(upgrade_helper_tree(source, catalog))
        self.assertIsNone(converted.find('.//LODLock'))
        self.assertEqual(converted.find('.//SetVisibility').attrib, {'ItemWUID': '$this.id', 'Visibility': 'false'})
        self.assertEqual(converted.find('.//ProcessMessage').get('inbox'), "'hitReaction'")
        self.assertEqual(converted.find('.//ProcessMessage').get('variable'), '$hit')
        self.assertEqual(converted.find('.//Variable').get('type'), 'hitReaction')

    def test_binding_table_uses_composite_identity(self):
        def table(mailbox):
            return ('<database><brain2mailboxs version="1"><brain2mailbox brain_id="a" mailbox_id="'+mailbox+'" priority="0"/></brain2mailboxs></database>').encode()
        files = {'Libs/Tables/ai/brain2mailbox__a.xml': table('one'), 'Libs/Tables/ai/brain2mailbox__b.xml': table('two')}
        result = managed_patches(files, 'test')
        self.assertEqual(len(E.fromstring(next(iter(result.values())))[0]), 2)
        files['Libs/Tables/ai/brain2mailbox__b.xml'] = table('one')
        with self.assertRaisesRegex(ValueError, 'duplicate primary key'): managed_patches(files, 'test')

    def test_home_shape_uses_compiled_precision_without_hiding_real_changes(self):
        import struct
        x = 3050.123456
        serialized = struct.unpack('<f', struct.pack('<f', x))[0]
        a = TriggerArea(1, 2., ((x, 0., 0.),))
        self.assertTrue(same_area_geometry(a, TriggerArea(1, 2., ((serialized, 0., 0.),))))
        self.assertFalse(same_area_geometry(a, TriggerArea(1, 2., ((x+.1, 0., 0.),))))

    def person(self, instance='one', entity='123456789abcdef0'):
        return dict(name='same', source_level='rataje', soul={'soul_id': 'shared'},
                    actor=E.fromstring(f'<Entity EntityClass="NPC" EntityGuid="{entity}" Pos="1,2,3"/>'),
                    instance=E.fromstring(f'<Soul><Guid>{instance}</Guid><SharedSoulGuid>shared</SharedSoulGuid></Soul>'))

    def test_shared_soul_instances_do_not_overwrite_each_others_appearance(self):
        a, b = self.person(), self.person('two', '223456789abcdef0')
        first, second = placement_person(a, {'same'}), placement_person(b, {'same'})
        self.assertNotEqual(first['soul']['soul_id'], second['soul']['soul_id'])
        self.assertNotEqual(first['name'], second['name'])
        self.assertEqual(placement_person(a, {'same'})['soul'], first['soul'])
        self.assertEqual(a['soul']['soul_id'], 'shared')
        self.assertEqual(first['source_name'], 'same')

    def test_placements_bind_to_native_souls_without_moving_existing_services(self):
        files = {'objects_mission0.xml': b'<Objects><Entity Name="coach" EntityId="7" EntityGuid="0000000a-0000-0000" Pos="4,5,6"/></Objects>',
                 'whdata_0': b'<World><SoulList><Souls><Soul><Guid>existing</Guid><Name>coach</Name></Soul></Souls></SoulList></World>'}
        result = add_people(files, [placement_person(self.person())])
        mission = E.fromstring(result['objects_mission0.xml'])
        souls = E.fromstring(result['whdata_0']).findall('SoulList/Souls/Soul')
        self.assertEqual(mission[0].get('Pos'), '4,5,6')
        self.assertEqual(mission[1].get('Pos'), '1,2,3')
        self.assertEqual(mission[1].get('EntityGuid'), souls[1].findtext('EntityGuid'))
        self.assertEqual(souls[1].findtext('Guid'), 'one')
        with self.assertRaisesRegex(ValueError, 'already registered'):
            add_people(result, [placement_person(self.person())])

    def test_conditional_membership_comes_from_export_container(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / 'Data/Levels/rataje'; folder.mkdir(parents=True)
            actor = '<Entity Name="test" EntityClass="NPC" EntityGuid="{}" Layer="people"/>'
            with zipfile.ZipFile(folder / 'level.pak', 'w') as z:
                z.writestr('objects_mission0.xml', '<Objects>' + actor.format('abc') + '</Objects>')
                z.writestr('layers/battle.xml', '<Objects>' + actor.format('def') + '</Objects>')
            result = source_membership(td, 'rataje')
            self.assertTrue(result['abc']['resident'])
            self.assertFalse(result['def']['resident'])

    def test_population_ids_respect_unloaded_layers_and_reserved_ids(self):
        files = {'objects_mission0.xml': b'<Objects/>',
                 'whdata_0': b'<World><SoulList><Souls/></SoulList></World>',
                 'layers/other.xml': b'<Objects><Entity Name="other" EntityId="71" EntityGuid="0000000a-0000-0000"/></Objects>',
                 'extractedlayerentityids.xml': b'<ReservedEntityIDsFromLayers><ReservedEntityID ID="99"/></ReservedEntityIDsFromLayers>'}
        result = add_people(files, [placement_person(self.person())])
        self.assertEqual(E.fromstring(result['objects_mission0.xml'])[0].get('EntityId'), '100')
        self.assertEqual(result['layers/other.xml'], files['layers/other.xml'])

    def test_source_default_body_is_gender_checked_and_never_replaces_explicit(self):
        class Tables:
            def row(self, *_): return {'gender_id': '2', 'race_id': '0'}
            def get(self, _):
                return {'rows': [{'gender_id': '2', 'race_id': '0', 'character_body_name': 'female_body', 'character_body_id': 'source-female'}]}
        soul, evidence = resolve_default_body({'soul_archetype_id': '1'}, Tables())
        self.assertEqual(soul['character_body_id'], 'source-female')
        self.assertIsNotNone(evidence)
        self.assertEqual(resolve_default_body({'character_body_id': 'special'}, Tables()), ({'character_body_id': 'special'}, None))

    def test_exported_variants_handle_case_and_fixed_shapes_but_not_ambiguous_shapes(self):
        class Chunk: kind = 0x2002
        with patch('character_person_appearance.read_chunks', return_value=[Chunk()]), \
             patch('character_person_appearance.read_morphs', return_value=[{'name': '#V_HEAD'}]):
            self.assertEqual(resolve_exported_variant(b'', '#V_head'), ('#V_HEAD', None))
            variant, receipt = resolve_exported_variant(b'', '#V_OTHER')
            self.assertIsNone(variant)
            self.assertTrue(receipt['missing_source_channel'])
        with patch('character_person_appearance.read_chunks', return_value=[]):
            variant, evidence = resolve_exported_variant(b'', '#V_001')
            self.assertIsNone(variant)
            self.assertEqual(evidence['geometry'], 'fixed source export')

    def test_generated_textures_share_bytes_without_sharing_materials(self):
        with tempfile.TemporaryDirectory() as td:
            store = TextureCache(td)
            references = []
            for person in ('a', 'b'):
                tex = person + '/native_diff.dds'
                data = {tex: b'diffuse-data', tex + '.1': b'mip-data',
                        person + '/body.mtl': f'<Material><Textures><Texture File="{tex}"/></Textures></Material>'.encode()}
                material, shared, excluded = store.externalize({k: lambda v=v: v for k, v in data.items()})
                references.append(E.fromstring(material[person + '/body.mtl']).find('Textures/Texture').get('File'))
                self.assertEqual(len(excluded), 2)
                self.assertEqual(len(shared), 2)
            self.assertEqual(references[0], references[1])
            self.assertEqual(len(list(Path(td).glob('*.zip'))), 1)
            reloaded = TextureCache(td)
            self.assertEqual(reloaded.family(references[0])[references[0]], b'diffuse-data')

    def test_faction_dependency_creation_retains_directed_source_relations(self):
        rows = {'rpg/superfaction': [dict(superfaction_id='3', superfaction_name='Civilians'),
                    dict(superfaction_id='5', superfaction_name='Player'),
                    dict(superfaction_id='35', superfaction_name='quest_custom')],
                'rpg/faction': [dict(faction_id='8', faction_name='town', superfaction_id='3', player_reputation='.5', location_id='town'),
                    dict(faction_id='9', faction_name='quest', superfaction_id='35', player_reputation='-1', location_id='town')],
                'rpg/superfaction2superfaction_relationship': [dict(from_superfaction_id='35', to_superfaction_id='3', relationship='-1')]}
        class Tables:
            def get(self, name): return {'rows': rows[name]}
        native = E.fromstring('<database><FactionTree version="1"><Faction Name="civilians"><Children><Faction Name="retail"/></Children></Faction><Faction Name="player"/></FactionTree></database>')
        result = register_catalog(Tables(), native)
        root = E.fromstring(result[FACTION_PATH])
        self.assertIsNotNone(root.find(".//Faction[@Name='retail']"))
        relation = root.find(".//Faction[@Name='gluemapper_kcd1_superfaction_35']/Relations/Relation")
        self.assertEqual(relation.get('target'), 'gluemapper_kcd1_superfaction_3')
        self.assertEqual(relation.get('reputation'), '-1.0')
        before = result[FACTION_PATH]
        name = register_factions(result, dict(source_superfaction=rows['rpg/superfaction'][2], source_factions=[rows['rpg/faction'][1]]), native)
        self.assertEqual(name, 'gluemapper_kcd1_faction_9')
        self.assertEqual(before, result[FACTION_PATH])


if __name__ == '__main__': unittest.main()
