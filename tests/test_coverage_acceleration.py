"""The spatial filter must preserve the original exhaustive coverage result."""
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from clothing_coverage import covered_points


def exhaustive(points, normals, triangles, inward=.06, outward=.12, accept_hit=None):
    a = triangles[:, 0]
    edge1, edge2 = triangles[:, 1]-a, triangles[:, 2]-a
    h = np.cross(normals[:, None, :], edge2)
    det = np.einsum('tj,ntj->nt', edge1, h)
    valid = np.abs(det) > 1e-10
    inv = np.divide(1., det, out=np.zeros_like(det), where=valid)
    delta = points[:, None, :]-a
    u = np.einsum('ntj,ntj->nt', delta, h)*inv
    q = np.cross(delta, edge1)
    v = np.einsum('nj,ntj->nt', normals, q)*inv
    distance = np.einsum('tj,ntj->nt', edge2, q)*inv
    hits = valid & (u >= -1e-7) & (v >= -1e-7) & (u+v <= 1+1e-7)
    hits &= (distance >= -inward) & (distance <= outward)
    if accept_hit is not None:
        p, t = np.nonzero(hits)
        hits[p, t] &= accept_hit(t, u[p, t], v[p, t])
    return hits.any(axis=1)


class CoverageAccelerationTests(unittest.TestCase):
    def test_scattered_segments_with_different_lengths_keep_exact_hits(self):
        rng = np.random.default_rng(619)
        triangles = rng.normal(size=(170,3,3)) * .15
        triangles += rng.uniform(-5,5,(170,1,3))
        directions = rng.normal(size=(131,3))
        directions *= rng.choice([0., .01, 1., 3.], (131,1))
        points = triangles[:131].mean(axis=1) - .04*directions
        for inward, outward in ((0., .12), (.06,.12), (.2,.02)):
            np.testing.assert_array_equal(
                covered_points(points, directions, triangles, inward, outward),
                exhaustive(points, directions, triangles, inward, outward))

    def test_random_regions_and_alpha_match_exhaustive_across_batches(self):
        rng = np.random.default_rng(817)
        triangles = rng.normal(size=(300, 3, 3))*.1 + np.repeat(rng.uniform(-2,2,(300,1,3)),3,axis=1)
        normals = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
        normals /= np.linalg.norm(normals,axis=1)[:,None]
        points = triangles.mean(axis=1) - normals*rng.uniform(-.08,.16,(300,1))
        normals[::13] = 0
        triangles[::17,2] = triangles[::17,1]  # Degenerate faces.
        def accept(t,u,v):return (t % 3 != 0) & (u < .7) & (v > .1)
        for callback in (None, accept):
            expected = exhaustive(points,normals,triangles,accept_hit=callback)
            self.assertTrue(expected.any()); self.assertFalse(expected.all())
            np.testing.assert_array_equal(covered_points(points,normals,triangles,accept_hit=callback),expected)

    def test_tolerant_edge_and_inward_endpoint_are_not_rejected(self):
        triangles=np.array([[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]])
        points=np.array([[-.5e-7,.3,.06],[1.+.5e-7,0.,-.12],[-2e-7,.3,0.]])
        np.testing.assert_array_equal(covered_points(points,[[0,0,1]]*3,triangles),[True,True,False])

    def test_alpha_callback_keeps_global_triangle_indices_after_culling(self):
        triangles=np.array([[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]]*4)
        triangles[[0,2]] += 100
        visited=[]
        def accept(t,u,v):
            visited.extend(t.tolist())
            return t == 3
        self.assertTrue(covered_points([[.2,.2,0.]],[[0,0,1]],triangles,accept_hit=accept)[0])
        self.assertEqual(visited,[1,3])

    def test_empty_or_distant_regions_skip_alpha_callback(self):
        def reject(*args):raise AssertionError('No possible intersections')
        for triangles in (np.empty((0,3,3)),np.ones((3,3,3))*100):
            np.testing.assert_array_equal(covered_points([[0,0,0]],[[0,0,1]],triangles,accept_hit=reject),[False])
        self.assertEqual(len(covered_points([],[],np.ones((1,3,3)))),0)


if __name__ == '__main__':unittest.main()
