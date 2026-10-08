import struct
import sys
from pathlib import Path
import unittest
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from clothing_regions import (Chunk, Skin, read_chunks, split_skin_regions,
                              write_chunks, assign_upper_garment_regions)


def sample_skin():
    # Two materials, four faces; internal faces are deliberately reordered.
    faces = [(0, 1, 2), (2, 1, 3), (2, 3, 4), (4, 3, 5)]
    remap = [3, 1, 4, 0, 5, 2]
    mesh = bytearray(264)
    struct.pack_into('<6I', mesh, 0, 2, 0, 6, 12, 2, 20)
    struct.pack_into('<I', mesh, 28+5*4, 21)
    subsets = struct.pack('<4I', 0, 2, 0, 0)
    subsets += struct.pack('<5I4f', 0, 6, 0, 4, 4, 1, 0, 0, 0)
    subsets += struct.pack('<5I4f', 6, 6, 2, 4, 9, 1, 0, 0, 0)
    indices = struct.pack('<6I', 0, 5, 12, 2, 0, 0)
    indices += b''.join(struct.pack('<3H', *f) for f in faces)
    internal = b''.join(struct.pack('<3H', *(remap[i] for i in f)) for f in reversed(faces))
    chunks = [Chunk(0x1000, 0x801, 10, bytes(mesh)),
              Chunk(0x1017, 0x800, 20, subsets), Chunk(0x1016, 0x800, 21, indices),
              Chunk(0x2004, 0x800, 30, internal),
              Chunk(0x2006, 0x800, 31, struct.pack('<6H', *remap)),
              Chunk(0x2002, 0x802, 32, b'opaque compressed morph data'),
              Chunk(0x2005, 0x800, 33, b'opaque skinning vertices')]
    return write_chunks(chunks, {}), faces


class ClothingRegionsTests(unittest.TestCase):
    def test_partition_is_disjoint_and_preserves_materials_and_opaque_data(self):
        blob, faces = sample_skin()
        outputs = split_skin_regions(blob, ['arms', 'torso', 'waist', 'arms'])
        self.assertEqual(set(outputs), {'arms', 'torso', 'waist'})
        all_faces = []
        before = {c.id: c.data for c in read_chunks(blob)}
        expected = {'arms': ([faces[0], faces[3]], [4, 9]),
                    'torso': ([faces[1]], [4]), 'waist': ([faces[2]], [9])}
        for region, output in outputs.items():
            skin = Skin(output)
            self.assertEqual(skin.vertex_count, 6)
            self.assertEqual(skin.faces, expected[region][0])
            self.assertEqual([struct.unpack_from('<I', r, 16)[0] for r in skin.subset_records],
                             expected[region][1])
            all_faces.extend(skin.faces)
            for c in skin.chunks:
                if c.id not in (10, 20, 21, 30):
                    self.assertEqual(c.data, before[c.id])
        self.assertEqual(Counter(all_faces), Counter(faces))

    def test_rejects_partial_or_empty_assignments(self):
        blob, _ = sample_skin()
        for labels in [['arms'], ['arms', '', 'torso', 'waist']]:
            with self.assertRaises(ValueError):
                split_skin_regions(blob, labels)

    def test_rejects_corrupt_internal_face_mapping(self):
        blob, _ = sample_skin()
        chunks = read_chunks(blob)
        corrupt = write_chunks(chunks, {30: bytes(24)})
        with self.assertRaisesRegex(ValueError, 'triangles disagree'):
            split_skin_regions(corrupt, ['torso'] * 4)

    def test_single_region_keeps_every_source_triangle(self):
        blob, faces = sample_skin()
        output = split_skin_regions(blob, ['torso'] * 4)
        self.assertEqual(Skin(output['torso']).faces, faces)

    def test_rejects_chunk_overlap(self):
        blob, _ = sample_skin()
        corrupt = bytearray(blob)
        struct.pack_into('<I', corrupt, 16+12, 16)
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            read_chunks(corrupt)

    def test_preview_classifier_uses_named_weights_and_explicit_height(self):
        blob, _ = sample_skin()
        chunks = read_chunks(blob)
        mesh = bytearray(chunks[0].data)
        struct.pack_into('<I', mesh, 28, 40)
        struct.pack_into('<I', mesh, 28+9*4, 41)
        bones = bytearray(32+2*584)
        bones[32+312:32+312+7] = b'LeftArm'
        bones[32+584+312:32+584+312+5] = b'Spine'
        positions = struct.pack('<6I', 0, 0, 6, 12, 0, 0) + struct.pack('<18f', *([0, 0, 1] * 6))
        def weighted(bone):
            weights = struct.pack('<6I', 0, 9, 6, 12, 0, 0)
            weights += struct.pack('<4H4B', bone, 0, 0, 0, 255, 0, 0, 0) * 6
            extra = [Chunk(0x2000, 0x800, 39, bytes(bones)),
                     Chunk(0x1016, 0x800, 40, positions), Chunk(0x1016, 0x800, 41, weights)]
            return write_chunks(chunks+extra, {10: bytes(mesh)})
        self.assertEqual(assign_upper_garment_regions(weighted(0), waist_z=0.5), ['arms']*4)
        self.assertEqual(assign_upper_garment_regions(weighted(1), waist_z=0.5), ['torso']*4)
        self.assertEqual(assign_upper_garment_regions(weighted(1), waist_z=1.5), ['waist']*4)

    def test_preview_classifier_rejects_invalid_thresholds(self):
        blob, _ = sample_skin()
        for height, threshold in [(float('nan'), 0.5), (1, 0), (1, 1.1)]:
            with self.assertRaises(ValueError):
                assign_upper_garment_regions(blob, waist_z=height, arm_weight_threshold=threshold)


if __name__ == '__main__':
    unittest.main()
