"""Ordered, bounded worker pools keep serial output and limit queued inputs."""
import operator
import os
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from bounded_pool import ordered_map, worker_count  # noqa: E402

KCD1 = Path(os.environ.get('GLUEMAPPER_KCD1', r'C:\Program Files (x86)\Steam\steamapps\common\KingdomComeDeliverance'))


class BoundedPoolTests(unittest.TestCase):
    def test_parallel_results_match_serial_order(self):
        items = list(range(-40, 40))
        self.assertEqual(list(ordered_map(operator.neg, items, 4)), [operator.neg(i) for i in items])
        self.assertEqual(list(ordered_map(operator.neg, items, 1)), [operator.neg(i) for i in items])

    def test_inputs_are_pulled_only_as_slots_free(self):
        pulled = []
        def items():
            for i in range(50):
                pulled.append(i)
                yield i
        for done, _ in enumerate(ordered_map(operator.neg, items(), 3, window=2), 1):
            self.assertLessEqual(len(pulled) - done, 3 * 2)

    def test_worker_count_respects_memory_jobs_and_override(self):
        self.assertEqual(worker_count(1, 1), 1)
        self.assertEqual(worker_count(64, 1 << 60), 1)
        os.environ['GLUEMAPPER_TEST_WORKERS'] = '3'
        try:
            self.assertEqual(worker_count(64, 1 << 60, 'GLUEMAPPER_TEST_WORKERS'), 3)
        finally:
            del os.environ['GLUEMAPPER_TEST_WORKERS']

    def test_setup_pool_self_test_runs_real_workers(self):
        from setup_app import pool_self_test
        self.assertEqual(pool_self_test()['status'], 'passed')

    @unittest.skipUnless((KCD1 / 'Data/Levels/rataje/svo.pak').is_file(), 'KCD1 voxel source not installed')
    def test_voxel_tiles_serial_and_parallel_bytes_match(self):
        import re
        import xml.etree.ElementTree as ET
        from entity_visuals import initial_layer
        from upgrade_map import read
        from world_voxels import _tile
        with zipfile.ZipFile(KCD1 / 'Data/Levels/rataje/level.pak') as world:
            layers = ET.fromstring(read(world, 'leveldata.xml')).find('Layers')
            active = {0} | {int(e.get('Id')) for e in layers if initial_layer(e.get('Name', ''))}
        with zipfile.ZipFile(KCD1 / 'Data/Levels/rataje/svo.pak') as src:
            names = sorted(n for n in src.namelist() if n.endswith('.dat'))[:6]
            def jobs():
                for name in names:
                    *center, extent = map(int, re.fullmatch(r'(\d+)_(\d+)_(\d+)_(\d+)\.dat', Path(name).name).groups())
                    yield read(src, name), center, extent, active
            serial = list(ordered_map(_tile, jobs(), 1))
            parallel = list(ordered_map(_tile, jobs(), 3))
        self.assertEqual(serial, parallel)


if __name__ == '__main__':
    unittest.main()
