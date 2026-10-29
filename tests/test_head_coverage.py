"""Neckline visibility regressions, independent of NPC names or authored outfits."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from clothing_coverage import covered_points, head_covered_points, exterior_coverage


def plane(z, half_width=1):
    w = half_width
    return np.array([[[-w,-w,z], [w,-w,z], [w,w,z]],
                     [[-w,-w,z], [w,w,z], [-w,w,z]]])


class HeadCoverageTests(unittest.TestCase):
    def test_inner_collar_does_not_hide_exposed_neck(self):
        points, directions = [[0,0,0]], [[0,0,1]]
        self.assertTrue(covered_points(points, directions, plane(-.02))[0])
        self.assertFalse(head_covered_points(points, directions, plane(-.02))[0])

    def test_garment_lip_is_not_full_neck_coverage(self):
        points, directions = [[0,0,0]], [[0,0,1]]
        lip = plane(.02, .005)
        self.assertTrue(covered_points(points, directions, lip, inward=0)[0])
        self.assertFalse(head_covered_points(points, directions, lip)[0])

    def test_opaque_exterior_still_hides_covered_skin(self):
        self.assertTrue(head_covered_points([[0,0,0]], [[0,0,1]], plane(.02))[0])
        self.assertFalse(head_covered_points([[0,0,0]], [[0,0,1]], plane(.5))[0])

    def test_alpha_cutout_on_surrounding_ray_preserves_skin(self):
        # A normal-only alpha test passes; an opening beside that hit must not.
        def opaque(indices, u, v):
            vertices = plane(.02)[indices]
            points = vertices[:,0]*(1-u-v)[:,None] + vertices[:,1]*u[:,None] + vertices[:,2]*v[:,None]
            return points[:,0] < .01
        self.assertTrue(covered_points([[0,0,0]], [[0,0,1]], plane(.02), accept_hit=opaque)[0])
        self.assertFalse(head_covered_points([[0,0,0]], [[0,0,1]], plane(.02), opaque)[0])

    def test_coverage_is_independent_of_body_orientation_and_normal_length(self):
        for axis in range(3):
            rotation = np.roll(np.eye(3), axis, axis=0)
            np.testing.assert_array_equal(head_covered_points(
                np.array([[0,0,0], [0,0,.04]]) @ rotation,
                np.array([[0,0,7]]*2) @ rotation, plane(.02) @ rotation), [True, False])

    def test_invalid_normals_cannot_supply_coverage_and_candidates_are_pruned(self):
        calls = []
        def opaque(points, directions):
            calls.append(len(points))
            np.testing.assert_allclose(np.linalg.norm(directions, axis=1), 1)
            return np.ones(len(points), bool)
        points = np.zeros((4,3))
        directions = [[0,0,1], [0,0,0], [float('nan'),0,1], [0,0,1]]
        np.testing.assert_array_equal(exterior_coverage(points, directions, opaque, [1,1,1,0]), [1,0,0,0])
        self.assertEqual(calls, [1]*5)
        np.testing.assert_array_equal(exterior_coverage(np.empty((0,3)), np.empty((0,3)), opaque), [])


if __name__ == '__main__':
    unittest.main()
