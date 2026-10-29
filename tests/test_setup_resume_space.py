import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from setup_resume_space import lighting, installation, GIB
from setup_campaign import RECEIPT, MOD_ID
from setup_build_cache import Cache, fingerprint
from test_setup_build_cache import pak


class ResumeSpaceTests(unittest.TestCase):
    def fixture(self, root):
        config=dict(resume_from=str(root/'job'),workspace=str(root),kcd1=str(root/'source'),
                    kcd2=str(root/'game'),mods=['map'],action='build_install',baking='all',cutscenes='none')
        package=root/'job/package/map';level=package/'Data/Levels/map';level.mkdir(parents=True)
        (level/'terrain.pak').write_bytes(b'terrain');(level/'level.pak').write_bytes(b'world')
        receipt=dict(files={'Data/Levels/map/'+name:name for name in ('level.pak','terrain.pak')},
                     travel_bridge={'destination':'map'})
        (package/RECEIPT).write_text(json.dumps(receipt))
        return config,package,receipt

    def cache(self,root):
        work=root/'checkpoint';scene=work/'map/plan-test/scene';scene.mkdir(parents=True)
        (work/'map/ready.json').write_text(json.dumps(dict(attempt='plan-test')))
        (scene/'plan.json').write_text(json.dumps(dict(level='map',errors=[],pending_categories={},tiles=[dict(origin=[0,0,0])])) )
        source=root/'shard.pak'
        pak(source,{'terrain/svo/0128_0128_0128_0256.idx':b'index','terrain/svo/0128_0128_0128_0256.vox':b'voxels'})
        Cache(root/'cache/lighting').publish(fingerprint('legacy'),{'svo-part0.pak':source},dict(tiles=1))
        return work

    def test_current_job_uses_complete_shared_cache_for_space_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config,package,receipt=self.fixture(root);work=self.cache(root)
            with patch('setup_baking.checkpoint_workspace',return_value=work):
                result=lighting(config,receipt)
            self.assertTrue(result['pending'])
            self.assertLess(result['bytes'],GIB)
            self.assertGreater(result['install_bytes'],0)
            self.assertEqual(result['replaced'],set(receipt['files']))
            self.assertIn('1 reusable lighting tiles',result['messages'][0])

    def test_forced_rebuild_or_incompatible_checkpoint_keeps_bake_allowance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config,package,receipt=self.fixture(root);work=self.cache(root)
            with patch('setup_baking.checkpoint_workspace',return_value=root/'different-inputs'):
                self.assertEqual(lighting(config,receipt)['bytes'],60*GIB)
            with patch('setup_baking.checkpoint_workspace',return_value=work):
                self.assertEqual(lighting(dict(config,rebuild_cache=True),receipt)['bytes'],60*GIB)
                (work/'map/ready.json').write_text('{"attempt":"../other-job"}')
                self.assertEqual(lighting(config,receipt)['bytes'],60*GIB)

    def test_install_estimate_excludes_reused_files_and_counts_future_lighting_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config,package,receipt=self.fixture(root)
            (root/'game/Mods'/MOD_ID).mkdir(parents=True)
            plan=dict(install_bytes=123,replaced={'Data/Levels/map/level.pak'})
            with patch('setup_campaign.verify_package',return_value=receipt):
                result=installation(package,root/'game',receipt,plan)
            self.assertEqual(result,GIB+123+(package/RECEIPT).stat().st_size)

    def run_preflight(self, config, receipt, space, pending, work=2*GIB, install=100*GIB):
        import setup_resume
        from contextlib import ExitStack
        config=dict(config,workspace=str(Path(config['workspace'])/'builds'))
        with ExitStack() as stack:
            stack.enter_context(patch('setup_resume.map_receipt',return_value=receipt))
            stack.enter_context(patch('desktop_setup.validate_games',return_value='1.5.6'))
            stack.enter_context(patch('setup_shaders.tool_root'))
            stack.enter_context(patch('desktop_setup.game_running',return_value=False))
            stack.enter_context(patch('desktop_setup.free_space',return_value=space))
            stack.enter_context(patch('setup_resume_space.lighting',return_value=dict(bytes=work,pending=pending,messages=[])))
            stack.enter_context(patch('setup_resume_space.installation',return_value=install))
            return setup_resume.preflight(config)

    def test_install_shortage_does_not_block_finishing_saved_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            config,package,receipt=self.fixture(Path(tmp))
            result=self.run_preflight(config,receipt,space=20*GIB,pending=True)
            self.assertTrue(result['ok'])
            self.assertTrue(any('Build work can resume' in s for s in result['messages']))

    def test_install_only_retry_still_requires_space_and_build_shortage_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config,package,receipt=self.fixture(Path(tmp))
            with self.assertRaisesRegex(ValueError,'Installation estimate'):
                self.run_preflight(config,receipt,space=20*GIB,pending=False)
            with self.assertRaisesRegex(ValueError,'additional build space'):
                self.run_preflight(config,receipt,space=GIB,pending=True)


if __name__=='__main__':unittest.main()
