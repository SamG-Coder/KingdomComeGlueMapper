import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from native_hlod_export import inspect_tree, export_tree, verify_placements


class NativeExportTests(unittest.TestCase):
    def test_export_cannot_silently_drop_placements(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'Layers').mkdir()
            (root / 'Layers/main.lyr').write_text('<Layer><Objects><Object Type="Brush" Id="abc" Pos="0,0,0" Prefab="objects/tree.cgf"/></Objects></Layer>')
            tree = root / 'hlodtree.xml'
            tree.write_text('<HLodTree><Brush Id="abc" Geometry="objects/tree.cgf"/></HLodTree>')
            self.assertEqual(verify_placements(root, tree)['brushes_verified'], 1)
            tree.write_text('<HLodTree/>')
            with self.assertRaisesRegex(ValueError, 'membership'):
                verify_placements(root, tree)

    def test_native_tree_keeps_proxy_dependencies_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'hlodtree.xml'
            path.write_text('''<HLodTree Name="test"><LayerLod Name="Main" Pos="0,0,0" Radius="8000">
              <ClusterLod Pos="10,0,0" Radius="20" Geometry="levels/test/hlods/group.cgf">
                <Brush Pos="10,0,0" Geometry="objects/rock.cgf"/>
                <Brush Pos="20,0,0" Geometry="objects/rock.cgf"/>
              </ClusterLod></LayerLod></HLodTree>''')
            report = inspect_tree(path)
            self.assertEqual(report['explicit_placements'], 2)
            self.assertEqual(report['proxy_meshes'], ['levels/test/hlods/group.cgf'])
            self.assertFalse(report['package_ready'])

    def test_rejects_invalid_or_wrong_stage_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'hlodtree.xml'
            for xml in ['<HLodTree/>', '<HLod/>',
                '<HLodTree><LayerLod Pos="nan,0,0" Radius="1"/></HLodTree>',
                '<HLodTree><LayerLod Pos="0,0,0" Radius="-1"/></HLodTree>']:
                with self.subTest(xml=xml):
                    path.write_text(xml)
                    with self.assertRaises(ValueError):
                        inspect_tree(path)

    def test_compiled_scene_is_not_native_generator_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            level = root / 'Data/Levels/compiled'
            level.mkdir(parents=True)
            (level / 'level.pak').write_bytes(b'compiled data')
            with self.assertRaisesRegex(ValueError, 'Missing native editor layers'):
                export_tree(root, 'compiled', 'game', root / 'evidence')


if __name__ == '__main__':
    unittest.main()
