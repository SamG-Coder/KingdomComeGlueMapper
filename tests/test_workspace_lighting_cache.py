import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from setup_build_cache import Cache, fingerprint, environment
from setup_bake_cache import matching_workspace_cache, scene_identity
from test_setup_build_cache import pak


class WorkspaceLightingCacheTests(unittest.TestCase):
    def scene(self,root):
        scene=root/'scene';scene.mkdir()
        (scene/'meshes').mkdir()
        (scene/'plan.json').write_text(json.dumps(dict(level=str(root/'map'),tiles=[dict(origin=[0,0,0])],
            pak_root=str(root/'game/Data'),overrides=[],source_packages={'obsolete job/path': 'hash'},
            asset_catalog_digest='unrelated assets',reuse_base_scene='obsolete job/base')))
        (scene/'placements.json').write_text('[]')
        return scene

    def publish(self,root,cache,name='old',*,meta=None,tile='0128_0128_0128_0256'):
        source=root/(name+'.pak')
        pak(source,{'terrain/svo/'+tile+'.idx':b'index','terrain/svo/'+tile+'.vox':b'voxel'})
        return cache.publish(fingerprint(name),{'svo-part0.pak':source},meta or dict(tiles=1,limitations=[],vegetation_gi_upgrade=None))

    def test_completed_legacy_cache_needs_no_old_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scene=self.scene(root);cache=Cache(root/'cache/lighting')
            self.publish(root,cache)
            hit=matching_workspace_cache(cache,scene,'all','new scene')
            self.assertIsNotNone(hit)
            self.assertEqual(hit[1]['input_compatibility'],'not_recorded')
            self.assertEqual(hit[1]['cache_validation'],'legacy_output_and_tile_layout')
            self.assertFalse(list(root.rglob('job.json')))

    def test_changed_bytes_wrong_layout_and_modern_mismatch_are_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scene=self.scene(root)
            for case in ('corrupt','layout','modern','preset'):
                cache=Cache(root/case)
                meta=dict(tiles=1,preset='buildings' if case=='preset' else 'all',level='map')
                if case=='modern':meta['scene_identity']='different static scene'
                payload=self.publish(root,cache,case,meta=meta,tile='0384_0128_0128_0256' if case=='layout' else '0128_0128_0128_0256')
                if case=='corrupt':(payload/'svo-part0.pak').write_bytes(b'changed')
                self.assertIsNone(matching_workspace_cache(cache,scene,'all','current scene'))

    def test_input_verified_cache_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scene=self.scene(root);cache=Cache(root/'cache')
            self.publish(root,cache,'legacy')
            self.publish(root,cache,'verified',meta=dict(tiles=1,level='map',preset='all',scene_identity='current'))
            hit=matching_workspace_cache(cache,scene,'all','current')
            self.assertEqual(hit[1]['cache_key'],fingerprint('verified'))
            self.assertEqual(hit[1]['input_compatibility'],'verified')

    def test_failed_or_incomplete_scene_cannot_reuse_legacy_lighting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scene=self.scene(root);cache=Cache(root/'cache')
            self.publish(root,cache)
            original=json.loads((scene/'plan.json').read_text())
            for invalid in ({'errors':['missing mesh']},{'pending_categories':{'vegetation':True}},{'tiles':[]}):
                (scene/'plan.json').write_text(json.dumps(dict(original,**invalid)))
                self.assertIsNone(matching_workspace_cache(cache,scene,'all','current'))

    def test_scene_key_tracks_used_assets_and_geometry_but_not_npcs_or_job_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scene=self.scene(root)
            (scene/'meshes/house.json').write_text(json.dumps(dict(bindings=[['house',0]],source='house.cgf')))
            archive=root/'game/Data/assets.pak'
            def assets(texture=b'wall',npc=b'person'):
                pak(archive,{'house.mtl':b'<Material><Textures><Texture File="wall.dds"/></Textures></Material>',
                    'wall.dds':texture,'objects/characters/body.dds':npc})
            assets();first=scene_identity(scene,'all')
            assets(npc=b'new clothes');self.assertEqual(first,scene_identity(scene,'all'))
            plan=json.loads((scene/'plan.json').read_text());plan.update(source_packages={'different/job': 'new hash'},asset_catalog_digest='changed')
            (scene/'plan.json').write_text(json.dumps(plan));self.assertEqual(first,scene_identity(scene,'all'))
            plan['vegetation']={'policy':'fixed','sha256':'old archive wrapper'}
            (scene/'plan.json').write_text(json.dumps(plan));vegetation_key=scene_identity(scene,'all')
            plan['vegetation']['sha256']='new archive wrapper'
            (scene/'plan.json').write_text(json.dumps(plan));self.assertEqual(vegetation_key,scene_identity(scene,'all'))
            del plan['vegetation'];(scene/'plan.json').write_text(json.dumps(plan))
            assets(texture=b'new wall');self.assertNotEqual(first,scene_identity(scene,'all'))
            assets();(scene/'placements.json').write_text('[{"transform":"moved"}]')
            self.assertNotEqual(first,scene_identity(scene,'all'))

    def test_setup_reuses_directory_cache_without_running_tile_builder(self):
        from contextlib import ExitStack
        from setup_baking import bake_package
        from setup_campaign import RECEIPT
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp);package=root/'package';level=package/'Data/Levels/map'
            pak(level/'level.pak',{'mission_mission0.xml':b'<Mission><Environment><Total_Illumination_v2/></Environment></Mission>',
                                 'objects_mission0.xml':b'current quests and people'})
            pak(level/'terrain.pak',{'terrain/terrain.dat':b'current terrain'})
            (package/RECEIPT).write_text(json.dumps(dict(files={},travel_bridge={'destination':'map'})))
            self.publish(root,Cache(root/'cache/lighting'))
            def coverage(base,scene,**kwargs):
                scene.mkdir(parents=True)
                (scene/'plan.json').write_text(json.dumps(dict(level=str(level),tiles=[dict(origin=[0,0,0])],errors=[])))
            stack.enter_context(environment({'workspace':tmp}))
            stack.enter_context(patch('setup_baking.verify_package',side_effect=lambda p:json.loads((Path(p)/RECEIPT).read_text())))
            stack.enter_context(patch('setup_bake_cache.key',return_value=fingerprint('new broad inputs')))
            stack.enter_context(patch('setup_bake_cache.scene_identity',return_value='current geometry'))
            stack.enter_context(patch('world_surface_scene.plan'))
            stack.enter_context(patch('world_surface_landscape.augment'))
            stack.enter_context(patch('world_surface_coverage.augment',side_effect=coverage))
            builder=stack.enter_context(patch('world_surface_tiles.build'))
            bake_package(package,root/'game',root/'work','all',lambda *a:None,lambda:False)
            builder.assert_not_called()
            with zipfile.ZipFile(level/'level.pak') as archive:
                self.assertEqual(archive.read('objects_mission0.xml'),b'current quests and people')
            receipt=json.loads((package/RECEIPT).read_text())
            self.assertEqual(receipt['baking']['levels'][0]['input_compatibility'],'not_recorded')


if __name__=='__main__':unittest.main()
