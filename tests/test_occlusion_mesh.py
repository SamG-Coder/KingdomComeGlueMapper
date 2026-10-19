import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from occlusion_mesh import KCD1, KCD2, parse, to_kcd2


def fixture(version=KCD1):
    geometry = struct.pack('<4I', 3, 0, 0, 0)
    geometry += struct.pack('<12f', 0, 0, 0, 1, 1, 0, 0, 1, 0, 1, 0, 1)
    matrix = struct.pack('<12f', 1, 0, 0, 75, 0, 1, 0, 125, 0, 0, 1, 30)
    instance = (matrix + struct.pack('<3fI', 1, 1, 0, 16) if version == KCD1
                else struct.pack('<I', 16) + matrix)
    # Multiple placements share one geometry block, as in native files.
    return version + struct.pack('<3I', 2, 2, 16 + len(geometry)) + geometry + instance * 2


class OcclusionMeshTests(unittest.TestCase):
    def test_native_fixture_and_conversion_agree_byte_for_byte(self):
        source = fixture()
        target = to_kcd2(source)
        self.assertEqual(target, fixture(KCD2))
        self.assertEqual(len(target), len(source) - 24)
        self.assertEqual(to_kcd2(target), target)
        old, new = parse(source), parse(target)
        self.assertEqual(old.geometry[4:], new.geometry[4:])
        self.assertEqual([i.transform for i in old.instances], [i.transform for i in new.instances])
        self.assertEqual(len(new.mesh_vertices), 1)
        self.assertEqual(new.declared_mesh_count, 2)

    def test_rejects_invalid_layouts_and_references(self):
        data = fixture()
        for damaged in (data[:15], b'bad!' + data[4:], data[:-1], data + b'\0'):
            with self.assertRaises(ValueError):
                parse(damaged)
        for offset, value in ((12, 17), (16, 4), (20, 1), (80 + 60, 32)):
            damaged = bytearray(data)
            struct.pack_into('<I', damaged, offset, value)
            with self.assertRaises(ValueError):
                parse(damaged)

    def test_rejects_nonfinite_vertices_transforms_and_bad_extents(self):
        for offset, value in ((32, float('nan')), (44, 0), (80, float('inf')), (128, -1)):
            damaged = bytearray(fixture())
            struct.pack_into('<f', damaged, offset, value)
            with self.assertRaises(ValueError):
                parse(damaged)


if __name__ == '__main__':
    unittest.main()
