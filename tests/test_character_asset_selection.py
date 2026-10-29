from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from character_asset_selection import runtime_paths


class CharacterAssetSelectionTests(unittest.TestCase):
    def stage(self, root, model='body.skin', texture='selected.dds'):
        prefix = 'objects/characters/gluetravel/actor/'
        files = {
            'Libs/Tables/Character/CharacterComponent__actor.xml':
                f'<database><CharacterComponents><Component FilePath="gluetravel/actor/"><DerivedComponents><Body><Elements>'
                f'<SkinElement Model="{model}" Material="body.mtl"/></Elements></Body></DerivedComponents></Component></CharacterComponents></database>',
            prefix+'body.skin': 'base', prefix+'body_lod1.skin': 'lod1', prefix+'body_lod2.skin': 'lod2',
            prefix+'body.mtl': f'<Material><Textures><Texture File="{prefix+texture}"/></Textures></Material>',
            prefix+'selected.dds': 'base pixels', prefix+'selected.dds.1': 'mip',
            prefix+'selected.dds.a': 'alpha', prefix+'selected.dds.1a': 'alpha mip',
            prefix+'intermediate.mtl': '<Material/>', prefix+'unused.dds': 'unused pixels',
            'objects/gluecampaign/actor_weapons/sword.cgf': 'sword',
            'objects/characters/another_actor/body.skin': 'other actor',
        }
        for key, value in files.items():
            p = root/key; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(value)
        return sorted(p for p in root.rglob('*') if p.is_file())

    def test_selects_all_lods_and_texture_slices_without_filtering_other_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); paths = self.stage(root)
            kept = {p.relative_to(root).as_posix() for p in runtime_paths(root, paths, 'actor')}
            removed = {p.relative_to(root).as_posix() for p in paths} - kept
            self.assertEqual(removed, {'objects/characters/gluetravel/actor/intermediate.mtl',
                                       'objects/characters/gluetravel/actor/unused.dds'})
            self.assertTrue(all(p.is_file() for p in paths))  # selection never deletes staging

    def test_rejects_missing_model_or_material_texture(self):
        for args in ({'model': 'absent.skin'}, {'texture': 'absent.dds'}, {'model': '../body.skin'}):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); paths = self.stage(root, **args)
                with self.assertRaisesRegex(ValueError, 'Missing generated|Unsafe character'):
                    runtime_paths(root, paths, 'actor')

    def test_keeps_additive_stages_without_generated_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); p = root/'texture.dds'; p.write_bytes(b'pixels')
            self.assertEqual(runtime_paths(root, [p], 'actor'), [p])
