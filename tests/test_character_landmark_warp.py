import sys
from pathlib import Path
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_landmark_warp import LandmarkWarp
from character_skin_upgrade import CompiledSkin, upgrade_skin, read_morphs
from test_character_upgrade import sample_skin, target_skin


class AnatomicalConversionTests(unittest.TestCase):
    def test_shared_field_aligns_landmarks_and_preserves_a_rigid_surface(self):
        source = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1.]])
        rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1.]])
        field = LandmarkWarp(source, source @ rotation.T + [2, 3, 4])
        points = np.random.default_rng(8).normal(size=(40, 3))
        result, jacobian = field.transform(points)
        np.testing.assert_allclose(result, points @ rotation.T + [2, 3, 4], atol=1e-10)
        np.testing.assert_allclose(jacobian, np.tile(rotation, (40, 1, 1)), atol=1e-10)

    def test_shared_surface_ignores_different_clothing_weights_and_transforms_morphs(self):
        anchors = np.array([[0, 0, 0], [3, 0, 0], [0, 3, 0], [0, 0, 3.]])
        scale = np.array([1.1, .95, 1.05])
        field = LandmarkWarp(anchors, anchors*scale + [0, .03, 0])
        meshes = [sample_skin(morph=True), sample_skin(extra=True, morph=True)]
        results = []
        for source in meshes:
            output, report = upgrade_skin(source, target_skin(source, child_translation=.2), geometry_field=field)
            before, after = CompiledSkin(source), CompiledSkin(output)
            np.testing.assert_allclose(after.vertices, before.vertices*scale + [0, .03, 0], atol=1e-6)
            np.testing.assert_allclose(read_morphs(after.one(0x2002))[0]['delta'],
                                       read_morphs(before.one(0x2002))[0]['delta']*scale, atol=1e-6)
            self.assertEqual(before.streams[9].data, after.streams[9].data)
            self.assertEqual(report['geometry_mode'], 'anatomical_landmarks')
            results.append(after.vertices)
        np.testing.assert_array_equal(*results)

    def test_reflection_and_degenerate_landmarks_fail_closed(self):
        points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1.]])
        with self.assertRaisesRegex(ValueError, 'folds'):
            LandmarkWarp(points, points*[-1, 1, 1])
        with self.assertRaisesRegex(ValueError, 'span'):
            LandmarkWarp(points[:3], points[:3])


if __name__ == '__main__':
    unittest.main()
