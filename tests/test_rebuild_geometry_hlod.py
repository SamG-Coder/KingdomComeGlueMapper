import math
from pathlib import Path
import struct
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from rebuild_geometry_hlod import extract, placed_bounds, rebuild
from spatial_hlod import Group, serialize, world_hierarchy


def record(x, kind=1):
    data = bytearray(104 if kind == 1 else 64)
    struct.pack_into('<I6f', data, 0, kind, x, 0, 10, x + 4, 6, 30)
    return bytes(data)


class GeometryHlodTests(unittest.TestCase):
    def source(self, radius):
        leaves = [Group('tree', 'Vegetation', (999, 999, -20), radius,
                        [record(30, 2)], record(29)),
                  Group('roof', 'Cluster', (-500, -500, 0), radius,
                        [record(80)], record(81))]
        return serialize(world_hierarchy(leaves))[:2]

    def test_rebuild_ignores_legacy_switching_bounds_and_preserves_membership(self):
        a, data_a, report = rebuild(*self.source(100))
        b, data_b, _ = rebuild(*self.source(4000))
        self.assertEqual(ET.tostring(a), ET.tostring(b))
        self.assertEqual(data_a, data_b)
        self.assertTrue(report['membership_preserved'])
        self.assertEqual(report['proxies'], 2)
        self.assertEqual(report['detail_records'], 2)
        self.assertEqual(sorted(len(d) for _, d, _ in extract(a, data_a)), [1, 1])

    def test_rebuilt_containers_enclose_children_and_keep_vegetation_tiers(self):
        tree, _, _ = rebuild(*self.source(500))
        depths = []
        def visit(node, depth):
            pos = tuple(map(float, node.get('Pos').split(',')))
            for child in node:
                child_pos = tuple(map(float, child.get('Pos').split(',')))
                self.assertLessEqual(math.dist(pos, child_pos) + float(child.get('Radius')),
                                     float(node.get('Radius')) + 1e-5)
                visit(child, depth + 1)
            if node.get('Type') == 'Vegetation':
                depths.append(depth)
        visit(tree, 0)
        self.assertEqual(sorted(depths), [1, 2, 3, 4])

    def test_bad_bounds_and_proxy_links_rejected(self):
        bad = bytearray(record(0))
        struct.pack_into('<f', bad, 4, float('nan'))
        with self.assertRaises(ValueError):
            placed_bounds([bad])
        tree, data = self.source(100)
        next(n for n in tree.iter('HLod') if n.get('ProxyIndex') != '-1').set('ProxyIndex', '999')
        with self.assertRaises(ValueError):
            rebuild(tree, data)

    def test_rebuild_is_deterministic_on_its_own_output(self):
        leaves = [Group(str(i), 'Cluster', (0, 0, 0), 5000,
                        [record(i * 10)], record(i * 10 + 1)) for i in reversed(range(100))]
        original, payload, _ = serialize(world_hierarchy(leaves))
        tree, data, _ = rebuild(original, payload)
        again, payload, _ = rebuild(tree, data)
        self.assertEqual(ET.tostring(tree), ET.tostring(again))
        self.assertEqual(data, payload)


if __name__ == '__main__':
    unittest.main()
