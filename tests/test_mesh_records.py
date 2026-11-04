"""Scene mesh records are read through one index that follows the mesh folder."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from world_surface_tiles import _TriangleEstimate, mesh_records, mesh_triangles


def record(scene, key, triangles):
    (scene / 'meshes' / (key + '.json')).write_text(json.dumps(
        {'key': key, 'source': key + '.cgf', 'bindings': [[key, 0]], 'triangles': triangles}))


def reads(fn):
    real = Path.read_text
    with patch.object(Path, 'read_text', autospec=True, side_effect=real) as counted:
        return fn(), counted.call_count


class MeshRecordTests(unittest.TestCase):
    def test_index_follows_the_mesh_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            scene = Path(tmp); (scene / 'meshes').mkdir()
            record(scene, 'house', 10); record(scene, 'church', 20)
            first, opened = reads(lambda: mesh_records(scene))
            self.assertEqual({k: r['triangles'] for k, r in first.items()}, {'house': 10, 'church': 20})
            self.assertTrue((scene / 'meshes.json').is_file())
            self.assertGreaterEqual(opened, 2)
            self.assertEqual(reads(lambda: mesh_records(scene)), (first, 1))  # the index alone
            record(scene, 'barn', 5)
            self.assertEqual(mesh_triangles(scene), {'house': 10, 'church': 20, 'barn': 5})
            (scene / 'meshes.json').write_text('{broken')
            self.assertEqual(mesh_triangles(scene), {'house': 10, 'church': 20, 'barn': 5})
            self.assertEqual(mesh_records(scene / 'missing'), {})

    def test_rebuild_reports_progress_and_stops_when_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            scene = Path(tmp); (scene / 'meshes').mkdir()
            for number in range(3): record(scene, f'mesh{number}', number)
            reported = []
            records = mesh_records(scene, progress=lambda *a: reported.append(a))
            self.assertEqual(len(records), 3)
            self.assertEqual(reported[0], ('Index scene meshes', 0, 3, '0 of 3 mesh records'))
            self.assertEqual(reported[-1], ('Index scene meshes', 3, 3, '3 mesh records indexed'))
            self.assertEqual(mesh_records(scene, progress=lambda *a: self.fail('index hit must not report')), records)
            record(scene, 'late', 1)
            with self.assertRaises(InterruptedError):
                mesh_records(scene, cancelled=lambda: True)
            self.assertNotIn('late', json.loads((scene / 'meshes.json').read_text()))  # a cancelled rebuild keeps the old index
            self.assertIn('late', mesh_records(scene))

    def test_triangle_estimate_needs_no_mesh_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            scene = Path(tmp); (scene / 'meshes').mkdir()
            record(scene, 'house', 25_000); record(scene, 'church', 1_200_000)
            (scene / 'placements.json').write_text(json.dumps({'1': {'cache_key': 'house'}, '2': {'cache_key': 'house'}, '3': {'cache_key': 'church'}}))
            estimate = _TriangleEstimate(scene, {})
            self.assertEqual(estimate.estimated_triangles({'placements': ['1', '2']}), 50_000)
            self.assertEqual(estimate.estimated_triangles({'placements': ['1', '3']}), 1_225_000)


if __name__ == '__main__':
    unittest.main()
