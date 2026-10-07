"""Synthetic checks for compact merged-sector conversion; no game assets."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from merged_vegetation import MAGIC, decode_sector, convert_cell, inventory, build_tree


class MergedVegetationTests(unittest.TestCase):
    def test_parts_combine_and_descriptor_ids_match_unchanged_samples(self):
        cell = (46, 214, 4)
        sample_a = struct.pack("<3HB4bB", 120, 300, 450, 64, 0, 0, 0, 127, 43)
        sample_b = struct.pack("<3HB4bB", 900, 200, 600, 32, 0, 0, 127, 0, 0)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        container = Path(temporary.name) / "sectors.zip"
        with zipfile.ZipFile(container, "w") as archive:
            for part, sample in enumerate((sample_a, sample_b)):
                archive.writestr(f"terrain/merged_meshes_sectors/sector_46_214_4_{part}.dat",
                                 struct.pack("<6I", MAGIC, *cell, 3, 1) + sample)
        with zipfile.ZipFile(container) as archive:
            cells, groups, count = inventory(archive, 4)
            self.assertEqual((groups, count), ({3}, 2))
            descriptor, stream = convert_cell(archive, cell, cells[cell], {3: 0})
        self.assertEqual(len(descriptor), 84)
        self.assertEqual(struct.unpack_from("<I", descriptor, 44)[0], 1)
        group, count, identity = struct.unpack_from("<III", descriptor, 72)
        self.assertEqual((group, count), (0, 2))
        self.assertEqual(decode_sector(stream, cell), [(identity, sample_a + sample_b)])

    def test_sector_rejects_truncation_coordinates_magic_and_group(self):
        valid = struct.pack("<6I", MAGIC, 0, 1, 2, 3, 1) + bytes(12)
        for invalid in (valid[:-1], valid + b"x", bytes(4) + valid[4:]):
            with self.assertRaises(ValueError):
                decode_sector(invalid)
        with self.assertRaises(ValueError):
            decode_sector(valid, (1, 1, 2))
        with self.assertRaises(ValueError):
            decode_sector(valid, group_limit=3)

    def test_octree_spatial_leaves_and_payload_boundaries(self):
        records = {(0, 0, 0): b"aaaa", (255, 255, 29): b"bbbb"}
        data = build_tree(records)
        cursor = 0
        leaves = []
        def visit():
            nonlocal cursor
            header = struct.unpack_from("<HBB6fI", data, cursor)
            cursor += 32
            size = header[-1]
            if size:
                leaves.append((header[3:9], data[cursor:cursor+size]))
            cursor += size
            for i in range(8):
                if header[1] & (1 << i):
                    visit()
        visit()
        self.assertEqual(cursor, len(data))
        self.assertEqual(len(leaves), 2)
        for cell, record in records.items():
            bounds = next(b for b, payload in leaves if payload == record)
            self.assertTrue(all(bounds[a] <= cell[a]*16+8 < bounds[a+3] for a in range(3)))

    def test_octree_uses_native_x_y_z_child_bit_order(self):
        # Native root child 4 is positive X; child 2 positive Y.
        for cell, child in (((255, 0, 0), 4), ((0, 255, 0), 2)):
            data = build_tree({cell: b"test"})
            self.assertEqual(data[2], 1 << child)


if __name__ == "__main__":
    unittest.main()
