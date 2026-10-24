import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from setup_package_upgrade import eligible, finalize
from setup_campaign import MOD_ID, RECEIPT, digest, verify_package
from setup_gameplay import PROJECT, OPENING, STAGE_IDS
from campaign_scene import attach_scene
from campaign_start_policy import POLICY, validate_start_policy
from region_travel_companions import travel_player_resources


class UpgradeTests(unittest.TestCase):
    def test_recipe36_resolves_quest_start_layout_through_shared_finalization(self):
        self.source_recipe = 36
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_recipe35_adds_population_through_shared_finalization(self):
        self.source_recipe = 35
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_recipe34_adds_animals_through_shared_finalization(self):
        self.source_recipe = 34
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_recipe32_retires_obsolete_source_ambience_on_resume(self):
        self.source_recipe = 32
        self.retire_ambience = True
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_current_recipe_can_resume_without_reencoding_lod(self):
        self.repeat_resume = True
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_saved_job_string_game_paths_reach_upgraders_as_paths(self):
        # setup_mods passes the job.json game folders to finalize as strings.
        self.string_paths = True
        self.retail_initializers = True
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_rewrite_preserves_terrain_above_zip_directory_limit(self):
        from setup_package_upgrade import rewrite
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root/'terrain.pak', 'w') as z:
                z.writestr('terrain/terrain.dat', b'old metadata')
                for index in range(65535):
                    z.writestr(f'terrain/tile{index}', b'unchanged')
            rewrite(root, 'terrain.pak', {'terrain/terrain.dat': b'new metadata'})
            with zipfile.ZipFile(root/'terrain.pak') as z:
                self.assertEqual(len(z.infolist()), 65536)
                self.assertEqual(z.read('terrain/terrain.dat'), b'new metadata')
                self.assertTrue(all(z.read(f'terrain/tile{i}') == b'unchanged'
                                    for i in range(65535)))
                self.assertIsNone(z.testzip())

    def test_recipe31_migrates_spatial_metadata(self):
        self.source_recipe = 31
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_recipe30_migrates_lod_metadata(self):
        self.source_recipe = 30
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_recipe29_migrates_debug_initializer_with_native_abi(self):
        self.source_recipe = 29
        self.retail_initializers = True
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_shopkeepers_gain_haggle_roles_during_migration(self):
        self.shopkeeper = True
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_baked_package_regains_native_exposure_during_migration(self):
        self.source_recipe = 29
        self.retail_initializers = True
        self.baked = True
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_recipe28_migrates_to_current_merchant_recipe(self):
        self.source_recipe = 28
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_recipe27_migrates_to_current_icon_recipe(self):
        self.source_recipe = 27
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_recipe26_migrates_to_current_dialogue_recipe(self):
        self.source_recipe = 26
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_recipe24_migrates_to_current_holder_recipe(self):
        self.source_recipe = 24
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_migration_restores_registered_person_objective_marker(self):
        self.restore_markers = True
        self.test_migrates_legacy_world_without_changing_shared_assets()
    def test_rewrite_adds_dependency_payloads_and_removes_obsolete_wrapper(self):
        from setup_package_upgrade import rewrite
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root/'graphs.pak', 'w') as z:
                z.writestr('entry.xml', b'old')
                z.writestr('obsolete.xml', b'obsolete')
                z.writestr('keep.xml', b'keep')
            rewrite(root, 'graphs.pak', {'entry.xml': b'new', 'native/child.xml': b'child'},
                    lambda name: name == 'obsolete.xml')
            with zipfile.ZipFile(root/'graphs.pak') as z:
                self.assertEqual(set(z.namelist()), {'entry.xml', 'native/child.xml', 'keep.xml'})
                self.assertEqual(z.read('native/child.xml'), b'child')
                self.assertEqual(z.read('entry.xml'), b'new')
                self.assertEqual(z.read('keep.xml'), b'keep')

    def test_recipe9_split_is_restored_during_resume(self):
        self.restore_split = True
        self.test_migrates_legacy_world_without_changing_shared_assets()

    def test_migrates_legacy_world_without_changing_shared_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            def archive(name, entries):
                path=p/name;path.parent.mkdir(parents=True,exist_ok=True)
                with zipfile.ZipFile(path,'w') as z:
                    for key,value in entries.items():z.writestr(key,value)
            host=b'<Database><Skald><Project><Nodes/><Definitions/><Types/><ObjectiveValueTypes/></Project></Skald></Database>'
            source=f'<Database><Skald><Project><Nodes><Quest Name="q"/><Dialogue Name="{OPENING}"/></Nodes><Definitions/><Types/><ObjectiveValueTypes/></Project></Skald></Database>'.encode()
            graph=ET.fromstring(attach_scene(host,source,OPENING,'q','level.arrived','done','player'))
            from test_campaign_start_policy import old_scene
            graph=ET.fromstring(old_scene(ET.tostring(graph),OPENING,guarded=True))
            if getattr(self, 'retail_initializers', False):
                node = ET.SubElement(graph.find('./Skald/Project/Nodes'), 'Function',
                    Name='gm_source_baseline_stat_strength', MethodName='wh::rpgmodule::SetStatLevelDebug')
                ET.SubElement(node, 'Constant', Name='Stat', Value='strength')
                ET.SubElement(node, 'Constant', Name='Value', Value='1')
                ET.SubElement(node, 'Edge', From=OPENING+'_initialize.B', To='Exec')
                archive('native/Data/IPL_GameData.pak', {'Libs/concept/definitions.xml': b'''<Definitions><Definition TypeName="MemberFunction"><Properties><Property Name="MethodName" Value="AdvanceToStatLevel"/><Property Name="DeclaringType" Value="wh::rpgmodule::I_Soul"/></Properties><Ports><RunTimePort Name="Level" Type="uint"/><RunTimePort Name="OnExec" Type="trigger"/></Ports></Definition></Definitions>'''})
            player=travel_player_resources(b'function Player:OnInit() end\nfunction Player:OnLoadAI() end\n')['Scripts/Entities/actor/player.lua']
            player+=Path('tools/region_travel_horse_recovery.lua').read_bytes()
            travel_files = {PROJECT:ET.tostring(graph),'Scripts/Entities/actor/player.lua':player,'keep':b'unchanged'}
            if getattr(self, 'shopkeeper', False):
                from test_shop_roles import rule
                travel_files['Libs/Storm/roles/gm_person_collier.xml'] = rule('collier', 'gm_person_collier_role', 'gm_person_collier_roles')
            archive('Data/gluemappertravel.pak', travel_files)
            archive('Data/Levels/kcd1_travel/level.pak',{'objects_mission0.xml':b'<Objects><Entity EntityClass="SmartAreaShape" EntityGuid="0000000a-0000-0000"/></Objects>', 'whdata_0':b'<Root><AI><SmartAreaManager version="2"><SmartAreas/></SmartAreaManager></AI></Root>'})
            if getattr(self, 'retire_ambience', False):
                from test_environment_audio_upgrade import fixture as audio_fixture
                from setup_package_upgrade import rewrite
                mission, sources = audio_fixture()
                root = ET.fromstring(mission)
                ET.SubElement(root, 'Entity', EntityClass='SmartAreaShape', EntityGuid='0000000a-0000-0000')
                rewrite(p, 'Data/Levels/kcd1_travel/level.pak', {'objects_mission0.xml': ET.tostring(root)})
                archive('Data/Levels/rataje/level.pak', {'objects_mission0.xml':
                    b'<Objects>' + b''.join(ET.tostring(e) for e in sources.values()) + b'</Objects>'})
            from test_campaign_quest_people import model, QUEST as marker_quest, SID
            from setup_gameplay import QUEST_PATH, QUEST as quest_name
            marker_model = model(); marker_model['quest'] = quest_name
            if getattr(self, 'restore_markers', False):
                with zipfile.ZipFile(p/'Data/gluemappertravel.pak', 'a') as z:
                    z.writestr(QUEST_PATH, marker_quest.replace(b'Name="test"', ('Name="'+quest_name+'"').encode()))
                from setup_package_upgrade import rewrite
                wh = f'<Root><AI><SmartAreaManager version="2"><SmartAreas/></SmartAreaManager></AI><SoulList><Souls><Soul><SharedSoulGuid>{SID}</SharedSoulGuid></Soul></Souls></SoulList></Root>'.encode()
                rewrite(p, 'Data/Levels/kcd1_travel/level.pak', {'whdata_0': wh})
            from campaign_trigger_areas import write_areas
            with zipfile.ZipFile(p/'Data/Levels/kcd1_travel/level.pak','a') as z:
                z.writestr('triggerareas.fubar',write_areas([],1))
                from test_skalitz_outer_gates import fixture
                for name,data in fixture().items():z.writestr(name,data)
                if getattr(self, 'baked', False):
                    from interior_exposure import convert as cap_exposure
                    native_mission = b'<Mission><TimeOfDay><Variable Name="EV Min" Value="7"><Spline Keys="0:7:36,"/></Variable></TimeOfDay></Mission>'
                    z.writestr('mission_mission0.xml', cap_exposure(native_mission)[0])
                    archive('native/Data/Levels/trosecko/level.pak', {'mission_mission0.xml': native_mission})
            from test_lod_ratio_metadata import fixture as lod_fixture
            archive('Data/Levels/kcd1_travel/terrain.pak', {'terrain/terrain.dat': lod_fixture()[0]})
            table=f'Libs/Tables/level__{MOD_ID}.xml'
            archive(f'Data/{MOD_ID}.pak',{table:b'<Root><Levels><LevelData LevelId="2" LevelName="trosecko"/><LevelData LevelId="1000" LevelName="kcd1_rataje"/><LevelData LevelId="1001" LevelName="kcd1_travel"/></Levels></Root>'})
            (p/'mod.manifest').write_text(f'<Mod><info><modid>{MOD_ID}</modid></info></Mod>')
            (p/'campaign-audit.json').write_text('{}')
            base=p/'Data/Levels/kcd1_rataje';base.mkdir();(base/'terrain.pak').write_bytes(b'base')
            archive('Data/shared.pak',{'shared.bin':b'shared'})
            shared=p/'Data/shared.pak';shared_before=shared.read_bytes()
            r=dict(schema=1,owner=MOD_ID,travel_bridge={'destination':'kcd1_travel'},build_recipe=dict(version=getattr(self,'source_recipe',3),status='complete',content_validation={'actors':11},streaming_validation={'ok':True},shader_validation={'installation':'mod_private_root'},steps=[{'id':s} for s in STAGE_IDS]))
            if getattr(self, 'baked', False):
                r['baking'] = dict(preset='buildings', levels=[dict(level='kcd1_travel', tiles=1)])
            if getattr(self,'restore_split',False):
                from test_mesh_streaming import fixture as mesh_fixture
                from mesh_streaming import upgrade_package
                from mesh_lod_metadata import normalize
                archive('Data/kingdomcomegluemapper_world_000.pak',{'glueveg/tree.cgf':normalize(mesh_fixture())})
                upgrade_package(p)
                r['build_recipe']['version']=9
            # Exercise the real start-layout migration as part of finalization,
            # including a person and owned idle anchor in a previously built mod.
            from test_campaign_quest_start_placement import source_entities, mission as start_mission
            from setup_package_upgrade import rewrite
            level_path = 'Data/Levels/kcd1_travel/level.pak'
            with zipfile.ZipFile(p/level_path) as z:
                combined = ET.fromstring(z.read('objects_mission0.xml'))
            combined.extend(ET.fromstring(start_mission()))
            if getattr(self, 'shopkeeper', False):
                from test_shop_roles import MISSION as shop_mission
                combined.extend(e for e in ET.fromstring(shop_mission) if e.get('Name') in ('shop_a', 'collier'))
            rewrite(p, level_path, {'objects_mission0.xml': ET.tostring(combined)})
            source_path = p/'Data/Levels/rataje/level.pak'
            if source_path.exists():
                with zipfile.ZipFile(source_path) as z:
                    source_world = ET.fromstring(z.read('objects_mission0.xml'))
                source_world.extend(source_entities())
                rewrite(p, 'Data/Levels/rataje/level.pak', {'objects_mission0.xml': ET.tostring(source_world)})
            else:
                source_world = ET.Element('Objects'); source_world.extend(source_entities())
                archive('Data/Levels/rataje/level.pak', {'objects_mission0.xml': ET.tostring(source_world)})
            r['files']={f.relative_to(p).as_posix():digest(f) for f in p.rglob('*') if f.is_file()}
            (p/RECEIPT).write_text(json.dumps(r))
            eligible(r)
            animal_report = {'version': 2, 'scheduler_validation': True, 'pending': []}
            population_report = {'version': 1, 'scheduler_validation': True, 'work': {'pending': []}}
            from forge_loft import VERSION as LOFT_VERSION
            loft_report = {'version': LOFT_VERSION, 'runtime_verified': False}
            loft_upgrade = self.enterContext(patch('forge_loft.upgrade', return_value=({}, loft_report)))
            lights_report = {'version': 1, 'runtime_verified': False}
            lights_upgrade = self.enterContext(patch('procedural_lights.upgrade', return_value=({}, lights_report)))
            inventory_upgrade = self.enterContext(patch('setup_starting_inventory.upgrade', return_value=({}, {'new_game_only': True})))
            self.enterContext(patch('setup_opening_clock.upgrade', return_value=({}, {'new_game_only': True})))
            self.enterContext(patch('setup_training_upgrade.upgrade', return_value=({}, {'runtime_verified': False})))
            self.enterContext(patch('setup_training_upgrade.validate', return_value={'runtime_verified': False}))
            self.enterContext(patch('training_presentation_repair.upgrade', return_value=({}, {'version': 1})))
            self.enterContext(patch('designer_collision_repair.upgrade', return_value=({}, {'version': 1})))
            self.enterContext(patch('well_collision_repair.upgrade', return_value=({}, {'repaired': []})))
            self.enterContext(patch('market_visual_repair.upgrade', return_value=({}, {'restored': 0})))
            self.enterContext(patch('setup_static_gate_repair.upgrade', return_value=({}, {'opened': 0})))
            self.enterContext(patch('training_handoff_repair.upgrade', return_value=({}, {'version': 1})))
            self.enterContext(patch('training_entry_repair.upgrade', return_value=({}, {'version': 1})))
            self.enterContext(patch('setup_pub_cleaning.upgrade', return_value=({}, {'runtime_verified': False})))
            self.enterContext(patch('setup_work_coverage.upgrade', return_value=({}, {'runtime_verified': False})))
            self.enterContext(patch('setup_gambler_work.upgrade', return_value=({}, {'runtime_verified': False})))
            self.enterContext(patch('setup_work_speech.upgrade', return_value=({}, {'runtime_verified': False})))
            encounters = self.enterContext(patch('setup_world_encounters.upgrade', return_value=({}, {'runtime_verified': False})))
            self.enterContext(patch('setup_quest_invitation.validate', return_value={'runtime_verified': False}))
            invitation_upgrade = self.enterContext(patch('setup_quest_invitation.upgrade', return_value=({}, {'runtime_verified': False})))
            self.enterContext(patch('character_water_work.upgrade_well_bodies', return_value=({}, {'repaired': []})))
            with patch('setup_profile_population.upgrade', return_value=({}, population_report)) as population_upgrade, patch('setup_animal_population.upgrade', return_value=({}, animal_report)) as animal_upgrade, patch('static_navigation_profiles.upgrade', return_value=({}, {'version': 1})) as navigation_upgrade, patch('setup_merchant_upgrade.upgrade',return_value=({},{})), patch('setup_tavern_upgrade.upgrade',return_value=({},{})), patch('setup_gameplay.validate',return_value={'start_policy':POLICY}), patch('quest_import.import_quest', return_value=(marker_model, {})):
                with patch('setup_quest_introductions.upgrade', return_value=({}, {'runtime_verified': False})) as intro_upgrade, patch('campaign_menu_stage.add_to_package'), patch(
                        'setup_quest_starts.SELECTIONS', (('different_quest','teacher','intro'),)):
                    progress=[]  # the setup passes EventLog.progress(label, current, total, detail='')
                    as_job = str if getattr(self, 'string_paths', False) else (lambda path: path)
                    result=finalize(p, source=as_job(p), target=as_job(p/'native') if getattr(self, 'retail_initializers', False) else None,
                                    notify=lambda *args: progress.append(args))
                    self.assertTrue(progress and all(3 <= len(args) <= 4 for args in progress), progress)
                    intro_upgrade.assert_called_once_with(p, p, p/'native' if getattr(self, 'retail_initializers', False) else None)
                    inventory_upgrade.assert_called_once_with(p, p/'native' if getattr(self, 'retail_initializers', False) else None)
                    invitation_upgrade.assert_called_once_with(p, p, p/'native' if getattr(self, 'retail_initializers', False) else None)
                    loft_upgrade.assert_called_once_with(p, p/'native' if getattr(self, 'retail_initializers', False) else None)
                    self.assertEqual(result['forge_loft'], loft_report)
                    lights_upgrade.assert_called_once_with(p, p, p/'native' if getattr(self, 'retail_initializers', False) else None)
                    self.assertEqual(result['procedural_lights'], lights_report)
                    encounters.assert_called_once()
                    if getattr(self, 'repeat_resume', False):
                        with zipfile.ZipFile(p/'Data/Levels/kcd1_travel/level.pak') as z:
                            before_lod = z.read('terrain/hlods.dat')
                        result = finalize(p, source=p)
                        with zipfile.ZipFile(p/'Data/Levels/kcd1_travel/level.pak') as z:
                            self.assertEqual(z.read('terrain/hlods.dat'), before_lod)
                    if getattr(self, 'shopkeeper', False):
                        self.assertEqual(result['shop_roles']['added'], ['collier'])
                        # The next upgrade of the same package still passes and adds nothing.
                        self.assertEqual(finalize(p, source=p)['shop_roles']['added'], [])
                from setup_gameplay import STATIC_WORLD_PROFILES
                self.assertEqual(navigation_upgrade.call_args.args[3], STATIC_WORLD_PROFILES)
                self.assertEqual(result['static_navigation_profiles']['version'], 1)
                self.assertEqual(animal_upgrade.call_args.args[:4], navigation_upgrade.call_args.args[:4])
                self.assertEqual(result['animal_population'], animal_report)
                self.assertEqual(population_upgrade.call_args.args[:4], navigation_upgrade.call_args.args[:4])
                self.assertEqual(result['profile_population'], population_report)
                self.assertEqual(result['quest_start_placements']['placements'][0]['placement']['Pos'], '5,6,7')
                self.assertIn('quest-start-placements.json', result['files'])
            if getattr(self, 'retail_initializers', False):
                self.assertEqual(result['retail_initialization_upgrade']['nodes'], ['gm_source_baseline_stat_strength'])
                with zipfile.ZipFile(p/'Data/gluemappertravel.pak') as z:
                    upgraded = ET.fromstring(z.read(PROJECT))
                node = upgraded.find(".//MemberFunction[@Name='gm_source_baseline_stat_strength']")
                self.assertEqual(node.get('MethodName'), 'AdvanceToStatLevel')
                self.assertEqual(node.find('Edge').get('From'), OPENING+'_initialize.B')
            if getattr(self, 'restore_markers', False):
                self.assertEqual(len(result['quest_markers']['converted']), 1)
                with zipfile.ZipFile(p/'Data/gluemappertravel.pak') as z:
                    self.assertIn(b'Marker=', z.read(QUEST_PATH))
            verify_package(p)
            if getattr(self, 'shopkeeper', False):
                self.assertEqual(result['shop_roles']['keepers'], 1)
                with zipfile.ZipFile(p/'Data/gluemappertravel.pak') as z:
                    roles = z.read('Libs/Storm/roles/gm_person_collier.xml')
                self.assertEqual((roles.count(b'SMLOUVANI_MUZ'), roles.count(b'addShopRoles_ending_man')), (1, 1))
            if getattr(self, 'baked', False):
                self.assertEqual(result['baking']['levels'][0]['exposure'], {'restored_exposure_profiles': 1})
                with zipfile.ZipFile(p/'Data/Levels/kcd1_travel/level.pak') as z:
                    self.assertIn(b'Value="7"', z.read('mission_mission0.xml'))
            if getattr(self, 'retire_ambience', False):
                self.assertEqual(result['ambient_audio_upgrade']['removed_emitters'], ['gm_environment_1'])
                with zipfile.ZipFile(p/'Data/Levels/kcd1_travel/level.pak') as z:
                    upgraded = ET.fromstring(z.read('objects_mission0.xml'))
                self.assertIsNone(upgraded.find("Entity[@Name='gm_environment_1']"))
                self.assertIsNotNone(upgraded.find("Entity[@Name='gm_environment_2']"))
            self.assertEqual(result['build_recipe']['streaming_validation']['vegetation_grid_version'], 1)
            self.assertEqual(result['build_recipe']['streaming_validation']['lod_ratio_version'], 1)
            # The fixture has no converted global probe; the step must still run and report it.
            self.assertEqual(result['global_probe_fog']['probes'], [])
            if getattr(self,'restore_split',False):
                self.assertFalse(list((p/'Data').glob('*_meshes.pak')))
                self.assertFalse(any(n.endswith('_meshes.pak') for n in result['files']))
                with zipfile.ZipFile(p/'Data/kingdomcomegluemapper_world_000.pak') as z:
                    self.assertEqual(z.read('glueveg/tree.cgf'),normalize(mesh_fixture()))
            self.assertTrue(result['single_playable_world']);self.assertFalse(base.exists())
            self.assertFalse(result['world_encounters']['runtime_verified'])
            self.assertEqual(result['package_upgrade']['skalitz_outer_gates']['opened_leaves'],
                             0 if getattr(self, 'repeat_resume', False) else 6)
            self.assertEqual(shared.read_bytes(),shared_before)
            with zipfile.ZipFile(p/'Data/gluemappertravel.pak') as z:
                validate_start_policy(z.read(PROJECT),OPENING)
                self.assertNotIn(b'GlueTravelHorseRecovery',z.read('Scripts/Entities/actor/player.lua'))
                self.assertEqual(z.read('keep'),b'unchanged')
            with zipfile.ZipFile(p/f'Data/{MOD_ID}.pak') as z:
                self.assertEqual([e.get('LevelId') for e in ET.fromstring(z.read(table)).find('Levels')],['2','1001'])
            with zipfile.ZipFile(p/'Data/Levels/kcd1_travel/level.pak') as z:
                self.assertEqual(ET.fromstring(z.read('whdata_0')).findtext('.//Guid'),'10')
            broken=json.loads(json.dumps(r));broken['build_recipe']['steps']=[]
            with self.assertRaises(ValueError):eligible(broken)
