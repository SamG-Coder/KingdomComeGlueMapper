import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from game_paths import GameLibrary
from steam_installations import discover, read_vdf
from desktop_setup import Cancelled, CONVERTERS, EventLog, conversion_plan, install_transaction, preflight, worker
from setup_campaign import MOD_ID, RECEIPT, verify_package
from campaign_start_policy import POLICY
from build_recipe import VERSION as RECIPE_VERSION
from setup_gameplay import STAGE_IDS, STATIC_WORLD_PROFILES


class DesktopSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_steam_finds_separate_libraries_and_ignores_missing_games(self):
        steam, second = self.root / 'steam', self.root / 'second'
        for path in (steam, second): (path / 'steamapps/common').mkdir(parents=True)
        escaped = str(second).replace('\\', '\\\\')
        (steam / 'steamapps/libraryfolders.vdf').write_text(f'// comment\n"libraryfolders" {{ "0" {{ "path" "{escaped}" "empty" "" }} }}')
        game = second / 'steamapps/common/My Game'; game.mkdir()
        (second / 'steamapps/appmanifest_379430.acf').write_text('"AppState" { "appid" "379430" "installdir" "My Game" }')
        found, warnings = discover([steam])
        self.assertEqual(found['kcd1'], [str(game.resolve())]); self.assertEqual(found['kcd2'], [])
        self.assertEqual(warnings, [])
        self.assertEqual(read_vdf('"x" "" "y" "C:\\\\Games"'), {'x': '', 'y': 'C:\\Games'})

    def test_game_paths_route_writes_away_from_both_installs(self):
        config = self.root / 'paths.json'
        config.write_text(json.dumps(dict(schema=1, kcd1=str(self.root / 'a'), kcd2=str(self.root / 'b'), build=str(self.root / 'build'))))
        library = GameLibrary(config)
        self.assertEqual(library / 'KCD2Mod/Data/Levels', (self.root / 'build/Data/Levels').resolve())
        self.assertEqual(library / 'KingdomComeDeliverance' / 'Data', (self.root / 'a/Data').resolve())
        with self.assertRaises(ValueError): _ = library / 'KCD2Mod/../a'
        config.write_text(json.dumps(dict(schema=1, kcd1=str(self.root / 'a'), kcd2=str(self.root / 'b'), build=str(self.root / 'b/build'))))
        with self.assertRaises(ValueError): GameLibrary(config)

    def test_conversion_plan_has_no_external_editor_or_npc_dependency(self):
        # These finalizers must also be hidden imports and part of the frozen
        # setup self-test, not only available from a source checkout.
        self.assertIn('hlod_spatial_metadata', CONVERTERS)
        self.assertIn('lod_ratio_metadata', CONVERTERS)
        self.assertIn('lod_ratio_repair', CONVERTERS)
        self.assertIn('setup_section_repair', CONVERTERS)
        self.assertIn('setup_static_gate_repair', CONVERTERS)
        self.assertIn('training_handoff_repair', CONVERTERS)
        self.assertIn('campaign_training_entry', CONVERTERS)
        self.assertIn('training_entry_repair', CONVERTERS)
        self.assertIn('campaign_intro_preparation', CONVERTERS)
        for name in ('character_pub_cleaning', 'character_pub_rest', 'setup_pub_cleaning', 'campaign_work_speech',
                     'source_work_coverage', 'setup_work_coverage', 'character_dice_resources',
                     'character_dice_policy', 'character_gambler_work', 'setup_gambler_work', 'campaign_work_metaroles',
                     'campaign_market_speech', 'setup_work_speech', 'gambler_dialogue_registration',
                     'gambler_schedule_activation'):
            self.assertIn(name, CONVERTERS)
        for module in ('setup_animal_population', 'build_animal_population_upgrade',
                       'animal_population', 'animal_population_sources', 'animal_native_templates',
                       'setup_profile_population', 'build_profile_population_upgrade',
                       'profile_population_upgrade', 'character_market_jobs', 'character_seated_work',
                       'character_shop_guard_work', 'character_city_benches', 'character_fence_work', 'character_home_rest',
                       'character_chopping_work', 'character_chopping_source', 'character_chopping_native',
                       'character_household_work', 'character_work_pool',
                       'character_water_source', 'character_water_native', 'character_water_work',
                       'character_sleep_work', 'character_sleep_clock', 'character_sleep_beds', 'character_sleep_native'):
            self.assertIn(module, CONVERTERS)
        plan = conversion_plan(self.root / 'paths.json', self.root / 'build')
        self.assertEqual(len(plan), 15)
        self.assertIn('--merged', plan[1][2]); self.assertIn('--character-visuals', plan[-4][2])
        self.assertEqual(plan[-5][1], 'build_buildings_probe')
        self.assertIn('--designer-collision', plan[-5][2])
        self.assertIn('setup_landscape', plan[-5][2])
        self.assertEqual(plan[-3][1], 'repair_tree_materials')
        self.assertEqual(plan[-2][1], 'prepare_spatial_hlod')
        self.assertEqual(plan[-1][1], 'world_ledges')
        self.assertIn('world_ledges', CONVERTERS)
        self.assertTrue(all('--library' not in args or str(self.root / 'paths.json') in args for _, _, args in plan))

    def package(self, path, marker, playable=False):
        files = {'mod.manifest': f'<kcd_mod><info><modid>{MOD_ID}</modid></info></kcd_mod>'.encode(),
                 'campaign-audit.json': b'{}', 'campaign.json': b'{"world":{"asset_archives":[]}}',
                 f'Data/{MOD_ID}.pak': marker, f'Data/{MOD_ID}_sources.pak': b'sources',
                 'Data/Levels/kcd1_rataje/level.pak': b'level', 'Data/Levels/kcd1_rataje/terrain.pak': b'terrain',
                 'Data/Levels/kcd1_rataje/levelinfo.xml': b'<LevelInfo/>'}
        if playable:files={n.replace('kcd1_rataje','kcd1_travel'):d for n,d in files.items()}
        for name, data in files.items():
            dest = path / name; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
        (path / RECEIPT).write_text(json.dumps(dict(schema=1, owner=MOD_ID, kind='retail-campaign-package', target_version='1.5.6',
                                                build_recipe=dict(version=RECIPE_VERSION, status='complete',
                                                    steps=[{'id': identity} for identity in STAGE_IDS],
                                                    content_validation={'actors': 11, 'start_policy': POLICY}, streaming_validation={'proxies': 1}, shader_validation={'compiled': True, 'installation': 'mod_private_root'}),
                                                surface_placement={'version':1},
                                                static_navigation_profiles={'version':1, 'mode':'materialized_static_profiles',
                                                    'profiles':[{'profile':p} for p in STATIC_WORLD_PROFILES]},
                                                animal_population={'version':2, 'mode':'supported_native_ambient',
                                                    'profiles':list(STATIC_WORLD_PROFILES), 'scheduler_validation':True},
                                                profile_population={'version':1, 'mode':'selected_static_profiles',
                                                    'profiles':list(STATIC_WORLD_PROFILES), 'scheduler_validation':True},
                                                files={n: hashlib.sha256(d).hexdigest() for n, d in files.items()})))
        return path

    def target(self):
        target = self.root / 'target'; target.mkdir()
        (target / 'system.cfg').write_text('wh_sys_version="1.5.6"')
        return target

    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=100 * 1024 ** 3)
    def test_install_changes_only_owned_mod_and_order_without_retained_backups(self, *_):
        target = self.target()
        native = {'Engine/Shaders.pak': b'retail shaders', 'Data/Tables.pak': b'retail tables',
                  'Bin/Win64/KingdomCome.exe': b'retail executable'}
        for name, blob in native.items():
            path=target/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(blob)
        old = self.package(target / 'Mods' / MOD_ID, b'old')
        other = target / 'Mods/other_mod'; other.mkdir(); (other / 'keep').write_text('untouched')
        source = self.package(self.root / 'package', b'new')
        result = install_transaction(source, target)
        self.assertIsNone(result['backup'])
        self.assertFalse((target/'GlueMapper Backups').exists())
        for name, blob in native.items(): self.assertEqual((target/name).read_bytes(),blob)
        self.assertEqual((old / f'Data/{MOD_ID}.pak').read_bytes(), b'new')
        self.assertEqual((other / 'keep').read_text(), 'untouched'); verify_package(old)

    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=100 * 1024 ** 3)
    def test_failed_swap_restores_old_install(self, *_):
        target = self.target(); dest = self.package(target / 'Mods' / MOD_ID, b'old')
        source = self.package(self.root / 'package', b'new')
        original = Path.rename
        def fail_stage(path, to):
            if path.name == MOD_ID and path.parent.name.startswith('.gluemapper-install-'): raise OSError('simulated swap failure')
            return original(path, to)
        with patch.object(Path, 'rename', fail_stage):
            with self.assertRaisesRegex(OSError, 'simulated'): install_transaction(source, target)
        self.assertEqual((dest / f'Data/{MOD_ID}.pak').read_bytes(), b'old'); verify_package(dest)

    @patch('desktop_setup.game_running', return_value=False)
    def test_upgrade_reuses_unchanged_files_with_space_only_for_replacements(self, *_):
        target=self.target();dest=self.package(target/'Mods'/MOD_ID,b'old')
        source=self.package(self.root/'package',b'new')
        # A large unchanged file makes a full-copy install exceed the budget.
        name='Data/shared.pak'
        for package in (dest,source):
            (package/name).write_bytes(b'x'*16384)
            receipt=json.loads((package/RECEIPT).read_text())
            receipt['files'][name]=hashlib.sha256((package/name).read_bytes()).hexdigest()
            (package/RECEIPT).write_text(json.dumps(receipt))
        inode=(dest/name).stat().st_ino
        space=1024**3+(source/RECEIPT).stat().st_size+3
        with patch('desktop_setup.free_space',return_value=space):
            install_transaction(source,target)
        self.assertEqual((dest/name).stat().st_ino,inode)
        self.assertEqual((dest/f'Data/{MOD_ID}.pak').read_bytes(),b'new')
        verify_package(dest)

    @patch('desktop_setup.game_running', return_value=False)
    def test_reuse_failure_or_tampered_shared_source_keeps_install(self, *_):
        target=self.target();dest=self.package(target/'Mods'/MOD_ID,b'old')
        source=self.package(self.root/'package',b'new')
        with patch('desktop_setup.free_space',return_value=1024**3+(source/RECEIPT).stat().st_size+3), \
                patch('desktop_setup.os.link',side_effect=OSError('links unavailable')):
            with self.assertRaisesRegex(ValueError,'Cannot reuse'):
                install_transaction(source,target)
        verify_package(dest)
        (source/'campaign-audit.json').write_bytes(b'tampered')
        with patch('desktop_setup.free_space',return_value=100*1024**3):
            with self.assertRaisesRegex(ValueError,'Package changed'):
                install_transaction(source,target)
        verify_package(dest)

    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=100 * 1024 ** 3)
    def test_cancel_and_tampering_leave_old_install_intact(self, *_):
        target = self.target(); dest = self.package(target / 'Mods' / MOD_ID, b'old')
        source = self.package(self.root / 'package', b'new')
        with self.assertRaises(Cancelled): install_transaction(source, target, cancelled=lambda: True)
        (source / f'Data/{MOD_ID}.pak').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'changed'): install_transaction(source, target)
        self.assertEqual((dest / f'Data/{MOD_ID}.pak').read_bytes(), b'old')

    @patch('desktop_setup.validate_games', return_value='1.5.6')
    @patch('setup_shaders.tool_root')
    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=1024 ** 3)
    def test_low_space_is_reported_before_build(self, *_):
        config = dict(kcd1=str(self.root / 'a'), kcd2=str(self.root / 'b'), workspace=str(self.root / 'work'), action='build_only')
        with self.assertRaisesRegex(ValueError, '200.0 GiB'): preflight(config)

    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=100 * 1024 ** 3)
    def test_combined_install_migrates_legacy_and_preserves_other_mod_order(self, *_):
        target = self.target()
        self.package(target/'Mods'/MOD_ID, b'old')
        source = self.package(self.root/'package', b'new', playable=True)
        receipt = json.loads((source/RECEIPT).read_text())
        receipt['travel_bridge'] = {'files': [], 'destination':'kcd1_travel'}
        receipt['single_playable_world'] = True
        (source/RECEIPT).write_text(json.dumps(receipt))
        legacy = target/'Mods/gluemappertravel'; legacy.mkdir()
        (legacy/'mod.manifest').write_text('<kcd_mod><info><modid>gluemappertravel</modid></info></kcd_mod>')
        (legacy/'keep.pak').write_bytes(b'legacy')
        order = target/'Mods/mod_order.txt'
        original = b'# keep this comment\nother_mod\ngluemappertravel\n'
        order.write_bytes(original)
        result = install_transaction(source, target)
        self.assertFalse(legacy.exists())
        self.assertIsNone(result['legacy_backup'])
        self.assertIsNone(result['mod_order_backup'])
        self.assertEqual(order.read_text(), '# keep this comment\nother_mod\n'+MOD_ID+'\n')

    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=100 * 1024 ** 3)
    def test_failed_combined_swap_restores_both_mods_and_order(self, *_):
        target = self.target(); dest = self.package(target/'Mods'/MOD_ID, b'old')
        source = self.package(self.root/'package', b'new', playable=True)
        receipt = json.loads((source/RECEIPT).read_text()); receipt['travel_bridge'] = {'files': [], 'destination':'kcd1_travel'}
        receipt['single_playable_world'] = True
        (source/RECEIPT).write_text(json.dumps(receipt))
        legacy = target/'Mods/gluemappertravel'; legacy.mkdir()
        (legacy/'mod.manifest').write_text('<kcd_mod><info><modid>gluemappertravel</modid></info></kcd_mod>')
        order = target/'Mods/mod_order.txt'; order.write_text('gluemappertravel\n')
        original = Path.rename
        def fail_stage(path, to):
            if path.name == MOD_ID and path.parent.name.startswith('.gluemapper-install-'): raise OSError('simulated swap failure')
            return original(path, to)
        with patch.object(Path, 'rename', fail_stage):
            with self.assertRaisesRegex(OSError, 'simulated'): install_transaction(source, target)
        self.assertTrue(legacy.exists())
        self.assertEqual(order.read_text(), 'gluemappertravel\n')
        self.assertEqual((dest/f'Data/{MOD_ID}.pak').read_bytes(), b'old')

    @patch('desktop_setup.preflight', return_value='ready')
    def test_fresh_build_includes_travel_without_installed_world(self, *_):
        for action in ('build_only', 'build_install'):
            job = (self.root/action).resolve(); job.mkdir()
            config = job/'job.json'
            config.write_text(json.dumps(dict(kcd1='source', kcd2='target', workspace=str(job), action=action)))
            with patch('desktop_setup.conversion_plan', return_value=[]), \
                 patch('desktop_setup.build_campaign') as campaign, \
                 patch('region_travel.build') as bridge, \
                 patch('travel_bridge_package.combine', return_value=job/'package') as combine, \
                 patch('build_recipe.run') as recipe, \
                 patch('setup_shaders.BackgroundBuild') as shaders, \
                 patch('desktop_setup.install_transaction', return_value={'installed': 'target'}) as install:
                self.assertEqual(worker(config), 0)
                campaign.assert_called_once_with('source', 'target', job/'conversion/Data', 'setup_world', job/'world-package', start_probe=False)
                bridge.assert_called_once_with('source', 'target', job/'world-package/Data/Levels/kcd1_rataje', job/'region-travel', modid=MOD_ID)
                combine.assert_called_once_with(job/'world-package', job/'region-travel'/MOD_ID, 'target', job/'package')
                recipe.assert_called_once()
                self.assertEqual(recipe.call_args.args[:4], ('source', 'target', job/'package', job/'gameplay-recipe'))
                # The shader compiler starts with the conversion and the recipe joins it.
                shaders.assert_called_once_with('target', job/'shaders', unittest.mock.ANY)
                self.assertIs(recipe.call_args.kwargs['shaders'], shaders.return_value)
                shaders.return_value.stop.assert_called_once_with()
                self.assertEqual(install.call_count, int(action=='build_install'))
            events = [json.loads(line) for line in (job/'events.jsonl').read_text().splitlines()]
            self.assertEqual(events[-1]['kind'], 'done')
            self.assertIn('Trosky', events[-1]['message'])

    @patch('desktop_setup.preflight', return_value='ready')
    def test_recipe_failure_blocks_installation(self, *_):
        job = self.root/'failed-recipe';job.mkdir()
        config = job/'job.json'
        config.write_text(json.dumps(dict(kcd1='source', kcd2='target', workspace=str(job), action='build_install')))
        with patch('desktop_setup.conversion_plan', return_value=[]), patch('desktop_setup.build_campaign'), \
             patch('region_travel.build'), patch('travel_bridge_package.combine', return_value=job/'package'), \
             patch('setup_shaders.BackgroundBuild') as shaders, \
             patch('build_recipe.run', side_effect=ValueError('Recipe failed')), patch('desktop_setup.install_transaction') as install:
            self.assertEqual(worker(config), 1)
            install.assert_not_called()
            shaders.return_value.stop.assert_called_once_with()  # no compiler outlives a failed job

    def test_incomplete_recipe_cannot_be_installed(self):
        with patch('desktop_setup.verify_package', return_value={'build_recipe': {'status': 'building'}}):
            with self.assertRaisesRegex(ValueError, 'incomplete or older'):
                install_transaction(self.root/'package', self.root/'target')

    @patch('desktop_setup.validate_games', return_value='1.5.6')
    @patch('setup_shaders.tool_root')
    @patch('desktop_setup.free_space', return_value=100 * 1024 ** 3)
    @patch('desktop_setup.game_running', return_value=False)
    def test_travel_preflight_requires_owned_world_and_idle_game(self, *_):
        config = dict(kcd1=str(self.root / 'a'), kcd2=str(self.root / 'b'), workspace=str(self.root / 'work'), action='build_travel')
        with self.assertRaisesRegex(ValueError, 'installed converted world'): preflight(config)
        world = self.root / 'b/Mods' / MOD_ID / 'Data/Levels/kcd1_rataje'
        world.mkdir(parents=True)
        for path in (world / 'level.pak', world / 'terrain.pak', self.root / 'a/Data/Levels/rataje/recast.pak', self.root / 'b/Data/Levels/trosecko/level.pak'):
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
        with patch('desktop_setup.verify_package', return_value={}), patch('setup_shaders.tool_root'):
            self.assertIn('Build and install region travel', preflight(config))
            with patch('desktop_setup.game_running', return_value=True):
                with self.assertRaisesRegex(ValueError, 'Close Kingdom Come'): preflight(config)

    @patch('desktop_setup.preflight', return_value='ready')
    def test_travel_worker_combines_and_installs_one_mod(self, *_):
        config = self.root / 'config.json'
        config.write_text(json.dumps(dict(kcd1=str(self.root / 'a'), kcd2=str(self.root / 'b'), workspace=str(self.root), action='build_travel')))
        with patch('region_travel.build') as build, patch('desktop_setup.install_transaction', return_value={'installed': 'destination'}) as install, patch('desktop_setup.build_campaign') as campaign, patch('travel_bridge_package.combine', return_value=self.root/'package') as combine, patch('build_recipe.run') as recipe:
            self.assertEqual(worker(config), 0)
            build.assert_called_once_with(str(self.root / 'a'), str(self.root / 'b'), (self.root / 'b/Mods' / MOD_ID / 'Data/Levels/kcd1_rataje').resolve(), self.root.resolve() / 'region-travel', modid=MOD_ID)
            combine.assert_called_once()
            recipe.assert_called_once()
            install.assert_called_once(); campaign.assert_not_called()
        events = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(events[-1]['kind'], 'done')
        self.assertIn('Region travel installed', events[-1]['message'])


    def test_failed_stage_is_not_reported_as_finished(self):
        log=EventLog(self.root/'events.jsonl')
        log.emit('stage',label='Forest bake',index=0,total=1)
        log.emit('error',message='Geometry error')
        log.stream.close();log.text.close()
        text=(self.root/'setup.log').read_text()
        self.assertIn('Failed Forest bake after',text)
        self.assertNotIn('Finished Forest bake',text)
        self.assertEqual(json.loads((self.root/'stage-times.json').read_text())[0]['status'],'failed')

    def run_stages(self, profile):
        log = EventLog(self.root / 'events.jsonl', profile=profile)
        log.emit('stage', index=0, total=2, label='Terrain and original materials')
        sorted(range(10000), key=lambda n: -n)
        log.emit('stage', index=1, total=2, label='Package converted world')
        log.emit('done', message='Complete')
        log.stream.close(); log.text.close()
        return [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]

    def test_every_stage_is_timed_before_the_final_event(self):
        events = self.run_stages(profile=False)
        self.assertEqual(events[-1]['kind'], 'done')
        timed = [e['label'] for e in events if e['kind'] == 'log' and 'seconds' in e]
        self.assertEqual(timed, ['Terrain and original materials', 'Package converted world'])
        times = json.loads((self.root / 'stage-times.json').read_text())
        self.assertEqual([t['label'] for t in times], timed)
        log = (self.root / 'setup.log').read_text()
        self.assertIn('Finished Terrain and original materials in', log)
        self.assertIn('Stage times:', log)
        self.assertFalse((self.root / 'profiles').exists())

    def test_profiling_saves_a_report_per_stage(self):
        self.run_stages(profile=True)
        self.assertEqual(sorted(p.name for p in (self.root / 'profiles').iterdir()),
                         ['00-terrain-and-original-materials.prof', '00-terrain-and-original-materials.txt',
                          '01-package-converted-world.prof', '01-package-converted-world.txt'])
        self.assertIn('function calls', (self.root / 'profiles/00-terrain-and-original-materials.txt').read_text())


if __name__ == '__main__': unittest.main()
