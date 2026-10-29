import os
import json
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from campaign_start_policy import POLICY
from build_recipe import VERSION, apply_overlay, reviewed_blend_mesh, run, require_current
from setup_campaign import RECEIPT
from setup_gameplay import STAGE_IDS


class RecipeTests(unittest.TestCase):
    def test_old_or_incomplete_recipe_cannot_be_installed(self):
        report = dict(version=VERSION, status='complete',
                      content_validation={'actors': 11, 'start_policy': POLICY}, streaming_validation={'proxies': 1}, shader_validation={'compiled': True, 'installation': 'mod_private_root'},
                      steps=[{'id': identity} for identity in STAGE_IDS])
        self.assertEqual(require_current({'build_recipe': report}), report)
        for field, value in (('version', VERSION-1), ('status', 'building'),
                             ('content_validation', {}), ('content_validation', {'actors': 11}), ('streaming_validation', {}), ('shader_validation', {}),
                             ('shader_validation', {'compiled': True, 'installation': 'build_only'}),
                             ('steps', report['steps'][:-1]), ('steps', report['steps'][::-1])):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                require_current({'build_recipe': dict(report, **{field: value})})

    def exercise_recipe(self, fail=False, travel=False, background=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory); package = root/'package'; package.mkdir()
            receipt = {'files': {}}
            if travel:
                receipt['travel_bridge'] = {'destination': 'kcd1_travel'}
            (package/RECEIPT).write_text(json.dumps(receipt))
            (package/'chain').write_text('base')
            seen = []
            def verify(path, **kwargs):
                return json.loads((Path(path)/RECEIPT).read_text())
            def stage(identity, output, folder='package'):
                previous = (package/'chain').read_text()
                self.assertEqual(previous, seen[-1] if seen else 'base')
                seen.append(identity)
                dest = output/folder; dest.mkdir(parents=True)
                (dest/'chain').write_text(identity)
            def gates(mod, output, **kwargs):
                # Gate output has Data directly; run() normalizes it.
                stage('gates', output, 'Data')
            stack.enter_context(patch('build_recipe.verify_package', side_effect=verify))
            # Keep the dependency assertion independent of the gates' archive layout.
            stack.enter_context(patch('build_open_test_gates.build', side_effect=gates))
            original_apply = apply_overlay
            def apply(package, overlay, **kwargs):
                result = original_apply(package, overlay, **kwargs)
                if (package/'Data/chain').exists() and len(seen) == 1:
                    (package/'chain').write_text('gates')
                return result
            stack.enter_context(patch('build_recipe.apply_overlay', side_effect=apply))
            for module, identity in [('build_recipe.build_containers','containers'),
                ('build_horse_trader_test.build','horses'),('build_debug_supplies.build','supplies'),
                ('build_rattay_smithery.build','smithery'),('build_smithing_voice.build','voice'),
                ('build_church_prayer.build','prayer'),('build_workshop_test.build','workshop')]:
                stack.enter_context(patch(module, side_effect=lambda *args, identity=identity, **kw: stage(identity,args[-1])))
            def gameplay(source, target, package, work):
                def failed(output):
                    raise ValueError('source dependency unresolved')
                return [(identity, identity, failed if fail and identity=='actors' else
                         lambda output, identity=identity: stage(identity,output)) for identity in STAGE_IDS]
            stack.enter_context(patch('setup_gameplay.steps', side_effect=gameplay))
            content = stack.enter_context(patch('setup_gameplay.validate', return_value={'actors':11, 'start_policy': POLICY}))
            streaming = stack.enter_context(patch('setup_streaming.validate', return_value={'proxies':1}))
            spatial = stack.enter_context(patch('hlod_spatial_metadata.upgrade_package',
                return_value=({}, {'version': 1, 'changed_nodes': 0})))
            ratios = stack.enter_context(patch('lod_ratio_metadata.upgrade_package',
                return_value=({}, {'version': 1})))
            stack.enter_context(patch('setup_shaders.tool_root'))
            compiled = []
            def shaders(target, output, *args, **kwargs):
                from shader_fixture import package_shaders
                compiled.append(Path(output))
                (output/'package').mkdir(parents=True)
                package_shaders(output/'package')
                return {'compiled': True}
            stack.enter_context(patch('setup_shaders.build', side_effect=shaders))
            menu = stack.enter_context(patch('campaign_menu_stage.add_to_package'))
            finalize = stack.enter_context(patch('setup_package_upgrade.finalize'))
            options = {}
            if background:
                from setup_shaders import BackgroundBuild
                options['shaders'] = BackgroundBuild(root/'target', root/'shaders')
                options['shaders'].thread.join()  # compiled before the recipe reaches its shader step
            if fail:
                with self.assertRaisesRegex(ValueError, 'source dependency'):
                    run(root/'source',root/'target',package,root/'work', **options)
                self.assertEqual(verify(package)['build_recipe']['status'], 'building')
                content.assert_not_called(); streaming.assert_not_called(); menu.assert_not_called()
                spatial.assert_not_called()
                ratios.assert_not_called()
            else:
                report = run(root/'source',root/'target',package,root/'work', **options)
                self.assertEqual(compiled, [root/('shaders' if background else 'work/shaders')])
                self.assertEqual(seen[-len(STAGE_IDS):], list(STAGE_IDS))
                if travel:
                    finalize.assert_called_once_with(package, source=root/'source', target=root/'target',
                        notify=unittest.mock.ANY, cancelled=unittest.mock.ANY)
                    # A crash before finalization cannot masquerade as installable.
                    with self.assertRaises(ValueError): require_current(verify(package))
                else:
                    finalize.assert_not_called()
                    require_current(verify(package))
                self.assertEqual(report['status'],'complete')
                self.assertEqual(report['shader_validation']['installation'],'mod_private_root')
                self.assertFalse((package/'Engine').exists())
                self.assertTrue((package/'ShaderCache/Engine/Shaders.pak').exists())
                self.assertFalse((package/'LaunchWithShaders.cmd').exists())
                self.assertIn('ShaderCache/shader-build.json', verify(package)['files'])
                self.assertEqual(report['shader_validation']['launcher'], 'setup')
                self.assertFalse((compiled[0]/'package/Engine/Shaders.pak').exists())  # consumed into the mod
                content.assert_called_once_with(package); streaming.assert_called_once_with(package, converted_data=None)
                spatial.assert_called_once_with(package)
                ratios.assert_called_once_with(package)
                menu.assert_called_once_with(root/'target',package)

    def test_stages_consume_previous_output_before_validating_and_registering_menu(self):
        self.exercise_recipe()

    def test_clean_world_uses_same_finalizer_as_resumed_packages(self):
        self.exercise_recipe(travel=True)

    def test_failed_campaign_stage_never_produces_complete_receipt_or_menu(self):
        self.exercise_recipe(fail=True)

    def test_background_shader_build_is_joined_at_the_shader_step(self):
        self.exercise_recipe(background=True)

    def test_failed_background_shader_build_fails_the_recipe_at_the_next_step(self):
        from setup_shaders import BackgroundBuild
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory); package = root/'package'; package.mkdir()
            (package/RECEIPT).write_text(json.dumps({'files': {}}))
            stack.enter_context(patch('build_recipe.verify_package', side_effect=lambda p, **k: json.loads((Path(p)/RECEIPT).read_text())))
            stack.enter_context(patch('setup_shaders.tool_root'))
            stack.enter_context(patch('setup_shaders.build', side_effect=ValueError('Native shader tool failed (1)')))
            def gates(mod, output, **kwargs):
                (output/'Data').mkdir(parents=True); (output/'Data/gate').write_text('x')
            stack.enter_context(patch('build_open_test_gates.build', side_effect=gates))
            later = stack.enter_context(patch('build_recipe.build_containers'))
            stack.enter_context(patch('setup_gameplay.steps', return_value=[]))
            shaders = BackgroundBuild(root/'target', root/'shaders'); shaders.thread.join()
            with self.assertRaisesRegex(ValueError, 'Native shader tool failed'):
                run(root/'source', root/'target', package, root/'work', shaders=shaders)
            later.assert_not_called()  # the compiler's failure stops the recipe after the first step, not an hour later

    def test_reviewed_models_include_their_lods_only(self):
        root = 'objects/buildings/houses/modular/6x15/'
        self.assertTrue(reviewed_blend_mesh(root+'6x15_roof_p_03_flipped.cgf'))
        self.assertTrue(reviewed_blend_mesh((root+'6x15_roof_p_03_flipped_lod2.cgf').upper().replace('/', '\\')))
        self.assertFalse(reviewed_blend_mesh(root+'6x15_roof_p_04.cgf'))
        self.assertFalse(reviewed_blend_mesh(None))

    def test_overlay_detaches_links_and_can_build_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root/'original.pak'; original.write_bytes(b'old')
            overlay = root/'overlay'; (overlay/'Data').mkdir(parents=True)
            (overlay/'Data/test.pak').write_bytes(b'new')
            for index in range(2):
                package = root/str(index); (package/'Data').mkdir(parents=True)
                os.link(original, package/'Data/test.pak')
                changed = apply_overlay(package, overlay)
                self.assertEqual((package/'Data/test.pak').read_bytes(), b'new')
                self.assertEqual(original.read_bytes(), b'old')
                self.assertEqual(list(changed), ['Data/test.pak'])

    def test_consumed_overlay_does_not_retain_duplicate_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); overlay = root/'overlay'; overlay.mkdir()
            (overlay/'asset.pak').write_bytes(b'converted')
            apply_overlay(root/'package', overlay, consume=True)
            self.assertFalse((overlay/'asset.pak').exists())
            self.assertEqual((root/'package/asset.pak').read_bytes(), b'converted')

    def test_cancel_and_completed_recipe_do_not_run_builders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('build_recipe.verify_package', return_value={'files': {}}), patch('build_open_test_gates.build') as gates, patch('setup_shaders.tool_root'):
                with self.assertRaises(InterruptedError):
                    run(root/'source', root/'target', root/'package', root/'work', cancelled=lambda: True)
                gates.assert_not_called()
            with patch('build_recipe.verify_package', return_value={'build_recipe': {'version': 1}}):
                with self.assertRaisesRegex(ValueError, 'already applied'):
                    run(root/'source', root/'target', root/'package', root/'other')
                self.assertFalse((root/'other').exists())

    def test_failed_navigation_finalization_cannot_be_installed_as_current(self):
        from setup_gameplay import STATIC_WORLD_PROFILES
        from setup_package_upgrade import eligible
        receipt = dict(travel_bridge={'destination':'kcd1_travel'}, single_playable_world=True,
            build_recipe=dict(version=VERSION, status='complete',
                content_validation={'start_policy':POLICY}, streaming_validation={'ok':True},
                shader_validation={'installation':'mod_private_root'}, steps=[{'id':s} for s in STAGE_IDS]))
        with self.assertRaisesRegex(ValueError,'source clothing conversion'): require_current(receipt)
        eligible(receipt)
        self.assertNotIn('source_appearance_upgrade',receipt)
        from character_person_appearance import APPEARANCE_POLICY_VERSION
        receipt['source_appearance_upgrade'] = dict(version=APPEARANCE_POLICY_VERSION,
            mode='source_converter',equipment_validation=True)
        with self.assertRaisesRegex(ValueError, 'navigation conversion'): require_current(receipt)
        eligible(receipt)  # recoverable by resume, but it must run the adapter
        self.assertNotIn('static_navigation_profiles', receipt)
        receipt['static_navigation_profiles'] = dict(version=1, mode='materialized_static_profiles',
            profiles=[dict(profile=p) for p in STATIC_WORLD_PROFILES])
        with self.assertRaisesRegex(ValueError, 'animal scheduler conversion'): require_current(receipt)
        eligible(receipt)
        self.assertNotIn('animal_population', receipt)
        from animal_population import VERSION as ANIMAL_VERSION
        from setup_animal_population import MODE
        animals = dict(version=ANIMAL_VERSION, mode=MODE,
            profiles=list(STATIC_WORLD_PROFILES), scheduler_validation=True)
        for field, value in (('version', ANIMAL_VERSION-1), ('mode', 'unverified'),
                             ('profiles', []), ('scheduler_validation', False)):
            receipt['animal_population'] = dict(animals, **{field:value})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'animal scheduler conversion'):
                require_current(receipt)
        receipt['animal_population'] = animals
        with self.assertRaisesRegex(ValueError, 'population conversion'): require_current(receipt)
        eligible(receipt)
        self.assertNotIn('profile_population', receipt)
        from setup_profile_population import VERSION as POPULATION_VERSION, MODE as POPULATION_MODE
        population = dict(version=POPULATION_VERSION, mode=POPULATION_MODE,
            profiles=list(STATIC_WORLD_PROFILES), scheduler_validation=True)
        for field, value in (('version', 0), ('mode', 'unverified'),
                             ('profiles', []), ('scheduler_validation', False)):
            receipt['profile_population'] = dict(population, **{field:value})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'population conversion'):
                require_current(receipt)
        receipt['profile_population'] = population
        with self.assertRaisesRegex(ValueError, 'surface placement'): require_current(receipt)
        eligible(receipt)
        self.assertNotIn('surface_placement', receipt)
        from terrain_object_tree import VERSION as PLACEMENT_VERSION
        receipt['surface_placement'] = dict(version=PLACEMENT_VERSION)
        with self.assertRaisesRegex(ValueError,'crime and punishment'):require_current(receipt)
        eligible(receipt)
        self.assertNotIn('crime_services',receipt)
        from setup_crime_services import VERSION as CRIME_VERSION
        receipt['crime_services']=dict(version=CRIME_VERSION,status='bound_native_services')
        require_current(receipt)
        receipt['static_navigation_profiles']['profiles'] = []
        with self.assertRaisesRegex(ValueError, 'navigation conversion'): require_current(receipt)


if __name__ == '__main__': unittest.main()
