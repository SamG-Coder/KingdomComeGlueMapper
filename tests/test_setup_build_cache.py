import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from contextlib import ExitStack

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from setup_build_cache import Cache, environment, fingerprint, copy_payload
from setup_bake_cache import key, restore


def pak(path, entries):
    path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path,'w') as z:
        for n,b in entries.items(): z.writestr(n,b)


class BuildCacheTests(unittest.TestCase):
    def test_corrupt_or_cancelled_publication_never_replaces_good_entry(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); source=root/'source';source.write_bytes(b'old')
            cache=Cache(root/'cache');identity=fingerprint('input')
            cache.publish(identity,{'output':source},{'tiles':1})
            source.write_bytes(b'new')
            with self.assertRaises(InterruptedError):
                cache.publish(identity,{'output':source},{},lambda:True)
            hit=cache.lookup(identity)
            self.assertEqual((hit[0]/'output').read_bytes(),b'old')
            (hit[0]/'output').write_bytes(b'broken')
            self.assertIsNone(cache.lookup(identity))

    def test_copy_is_independent_and_extra_files_invalidate(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'source';source.write_bytes(b'cache')
            cache=Cache(root/'cache');identity=fingerprint('input')
            payload=cache.publish(identity,{'out':source},{})
            copy_payload(payload,root/'job')
            (root/'job/out').write_bytes(b'changed')
            self.assertEqual((payload/'out').read_bytes(),b'cache')
            (payload/'extra').write_text('unrecorded')
            self.assertIsNone(cache.lookup(identity))

    def test_bake_key_ignores_job_path_quests_and_repacked_zip_timestamps(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);game=root/'game';(game/'Data').mkdir(parents=True)
            def make(job,quest='first',mesh=b'geometry',hlod=b'placements',material=b'<Material/>',texture=b'pixels'):
                level=root/job/'level'
                pak(level/'level.pak',{'terrain/hlods.dat':hlod,'leveldata.xml':b'surfaces',
                    'objects_mission0.xml':quest.encode(),'tables/ai/scheduler.xml':quest.encode()})
                pak(level/'terrain.pak',{'terrain/terrain.dat':b'terrain'})
                override=root/job/'assets.pak'
                # Reports, tables, dialogue, music, animations and skinned NPC files
                # change with every build or NPC edit; the bake never reads them.
                pak(override,{'objects/house.cgf':mesh,'objects/house.mtl':material,'textures/wall.dds.1':texture,
                    'Quests/intro.xml':quest.encode(),'Libs/GlueMapper/PersonImport/'+job+'.json':job.encode(),
                    'Libs/Tables/rpg/role.xml':quest.encode(),'Libs/skald/'+quest+'.xml':quest.encode(),
                    'music/track.sqcb':quest.encode(),'animations/walk.dba':quest.encode(),
                    'objects/characters/body.skin':quest.encode(),'objects/characters/head.chr':quest.encode()})
                return key(level,game,[str(override)],'all')
            a=make('a');self.assertEqual(a,make('b','new quest'))
            self.assertNotEqual(a,make('c',mesh=b'new geometry'))
            self.assertNotEqual(a,make('d',hlod=b'new placements'))
            self.assertNotEqual(a,make('e',material=b'<Material Opacity="0.5"/>'))
            self.assertNotEqual(a,make('f',texture=b'new pixels'))
            self.assertNotEqual(a,key(root/'a/level',game,[str(root/'a/assets.pak')],'buildings'))
            with patch('world_surface_tiles.code_digest',return_value='new baker'):
                self.assertNotEqual(a,key(root/'a/level',game,[str(root/'a/assets.pak')],'all'))

    def test_restoring_voxels_preserves_new_quest_and_schedule_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);level=root/'level';payload=root/'payload';payload.mkdir()
            pak(payload/'svo-part0.pak',{'terrain/svo/vox':b'baked'})
            mission=b'<Mission><Environment><Total_Illumination_v2/></Environment></Mission>'
            current={'mission_mission0.xml':mission,'objects_mission0.xml':b'new NPCs',
                     'tables/ai/scheduler.xml':b'new schedules','waitinglinks.xml':b'new quest links'}
            pak(level/'level.pak',current)
            restore((payload,{'tiles':1,'vegetation_gi_upgrade':None}),level,root/'out')
            with zipfile.ZipFile(root/'out/packages/level.pak') as z:
                for n,b in current.items():
                    if n!='mission_mission0.xml':self.assertEqual(z.read(n),b)
                self.assertIn(b'IntegrationMode="1"',z.read('mission_mission0.xml'))

    def test_shader_reuse_force_and_corruption(self):
        from setup_shaders import build, validate
        from shader_fixture import package_shaders
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            def compile(target,output,*args,**kwargs):
                p=Path(output)/'package';p.mkdir(parents=True);package_shaders(p)
                return validate(p)
            with environment({'workspace':t}),patch('setup_shaders.cache_key',return_value=fingerprint('shaders')),patch('setup_shaders.build_uncached',side_effect=compile) as compiler:
                build('game',root/'first');build('game',root/'second')
                self.assertEqual(compiler.call_count,1)
                with patch.dict(os.environ,{'GLUEMAPPER_REBUILD_CACHE':'1'}):build('game',root/'forced')
                self.assertEqual(compiler.call_count,2)
                cache=Cache(root/'cache/shaders');hit=cache.lookup(fingerprint('shaders'))
                (hit[0]/'Engine/ShadersBin.pak').write_bytes(b'corrupt')
                build('game',root/'recovered');self.assertEqual(compiler.call_count,3)

    def test_shader_key_tracks_engine_seed_and_tool_changes(self):
        from setup_shaders import cache_key, TOOL_FILES
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);sdk=root/'sdk';game=root/'game'
            for name in TOOL_FILES:
                p=sdk/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'tool')
            pak(game/'Engine/Shaders.pak',{'shader':b'source'})
            with patch('setup_shaders.tool_root',return_value=sdk):
                a=cache_key(game)
                seed=sdk/'Tools/modding/USER_ShaderCacheGen/seed.bin';seed.write_bytes(b'seed')
                b=cache_key(game);self.assertNotEqual(a,b)
                (sdk/TOOL_FILES[0]).write_bytes(b'updated tool')
                c=cache_key(game);self.assertNotEqual(b,c)
                pak(game/'Engine/Shaders.pak',{'shader':b'new source'})
                self.assertNotEqual(c,cache_key(game))

    def test_shared_workspace_and_force_option_validation(self):
        from desktop_setup import lighting_cache
        from setup_mods import checked_config
        c=dict(workspace='builds',kcd1='source',kcd2='game',mods=['map'],action='build_only')
        self.assertFalse(checked_config(c)['rebuild_cache'])
        with self.assertRaises(ValueError):checked_config(dict(c,rebuild_cache='yes'))
        with tempfile.TemporaryDirectory() as t, environment(dict(c,workspace=t)):
            a=lighting_cache(Path(t)/'one/map-job/conversion')
            b=lighting_cache(Path(t)/'two/map-job/conversion')
            self.assertEqual(a,b);self.assertTrue(a.is_relative_to(Path(t)/'cache'))

    def test_completed_bake_reused_by_fresh_job_without_old_gameplay(self):
        from setup_baking import bake_package
        from setup_campaign import RECEIPT
        with tempfile.TemporaryDirectory() as t, ExitStack() as stack:
            root=Path(t);payload=root/'payload';payload.mkdir()
            pak(payload/'svo-part0.pak',{'terrain/svo/test':b'lighting'})
            identity=fingerprint('same scene')
            cache=Cache(root/'cache/lighting')
            cache.publish(identity,{'svo-part0.pak':payload/'svo-part0.pak'},
                          dict(tiles=1,limitations=[],vegetation_gi_upgrade=None))
            stack.enter_context(environment({'workspace':t}))
            stack.enter_context(patch('setup_bake_cache.key',return_value=identity))
            planner=stack.enter_context(patch('world_surface_scene.plan'))
            builder=stack.enter_context(patch('world_surface_tiles.build'))
            stack.enter_context(patch('setup_baking.verify_package',side_effect=lambda p:json.loads((Path(p)/RECEIPT).read_text())))
            for job in ('first','new quest build'):
                package=root/job/'package';level=package/'Data/Levels/map'
                pak(level/'level.pak',{'mission_mission0.xml':b'<Mission><Environment><Total_Illumination_v2/></Environment></Mission>',
                                     'objects_mission0.xml':job.encode()})
                pak(level/'terrain.pak',{'terrain/terrain.dat':b'terrain'})
                (package/RECEIPT).write_text(json.dumps(dict(files={},travel_bridge={'destination':'map'})))
                bake_package(package,root/'game',root/job/'baking','all',lambda *a:None,lambda:False)
                with zipfile.ZipFile(level/'level.pak') as z:
                    self.assertEqual(z.read('objects_mission0.xml'),job.encode())
                self.assertTrue((level/'svo-part0.pak').is_file())
                self.assertEqual(json.loads((package/RECEIPT).read_text())['baking']['levels'][0]['tiles'],1)
            planner.assert_not_called();builder.assert_not_called()

    def test_failed_bake_never_publishes_and_force_bypasses_cached_bake(self):
        from setup_baking import bake_package
        with tempfile.TemporaryDirectory() as t, ExitStack() as stack:
            root=Path(t);package=root/'package';level=package/'Data/Levels/map'
            pak(level/'terrain.pak',{'terrain/terrain.dat':b'original'})
            stack.enter_context(environment({'workspace':t,'rebuild_cache':True}))
            stack.enter_context(patch('setup_baking.verify_package',return_value={'files':{}}))
            stack.enter_context(patch('setup_bake_cache.key',return_value=fingerprint('scene')))
            lookup=stack.enter_context(patch.object(Cache,'lookup'))
            publish=stack.enter_context(patch('setup_bake_cache.publish'))
            stack.enter_context(patch('world_surface_scene.plan',side_effect=ValueError('bad geometry')))
            with self.assertRaisesRegex(ValueError,'bad geometry'):
                bake_package(package,root/'game',root/'baking','all',lambda *a:None,lambda:False)
            lookup.assert_not_called();publish.assert_not_called()


if __name__=='__main__':unittest.main()
