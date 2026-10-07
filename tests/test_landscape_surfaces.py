from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from landscape_surfaces import convert_surface, expand_tangents
from building_brushes import is_landscape_designer
from build_native_mud_probe import native_mud_material


class LandscapeSurfaceTests(unittest.TestCase):
    def test_native_mud_mapping_keeps_track_dry_and_red_variants_distinct(self):
        prefix = 'materials/terrain/roads/'
        for source, native in [('road_mud_12', 'road_country_big'),
                               ('road_mud_rails_single', 'road_track_4x1'),
                               ('road_mud_cracks', 'road_soil_b_2m_dry'),
                               ('road_mud_12_red', 'road_soil_b_2m_reddish')]:
            self.assertEqual(native_mud_material(prefix+source), prefix+native)

    def test_native_mud_mapping_does_not_guess_other_surfaces(self):
        for name in ['materials/terrain/roads/road_soil_01',
                     'materials/terrain/roads/road_mud_unknown',
                     'materials/decals/road_mud_12']:
            self.assertIsNone(native_mud_material(name))

    def test_designer_selection_requires_audited_visible_material(self):
        path = '%level%/Brush/designer_12.cgf'
        self.assertTrue(is_landscape_designer(path, 'Objects/nature/rocks/rocks_modular/rock_modular_05'))
        self.assertFalse(is_landscape_designer(path, 'materials/special/collision_proxy_material'))
        self.assertFalse(is_landscape_designer(path, 'EngineAssets/TextureMsg/DefaultSolids'))
        self.assertFalse(is_landscape_designer('objects/default/box.cgf', 'objects/nature/rocks/rocks_modular/rock_modular_05'))

    def test_planar_basis_keeps_z_zero_and_handedness(self):
        # The binormal Y low bit explicitly suppresses reconstruction error.
        packed = struct.pack('<4h', 32766, 1, 0, 32766)
        self.assertEqual(struct.unpack('<8h', expand_tangents(packed)),
                         (32766, 0, 0, -32767, 0, 32766, 0, -32767))

    def test_sloped_basis_preserves_z_sign(self):
        packed = struct.pack('<4h', 1, 0, 32766, 1)
        result = struct.unpack('<8h', expand_tangents(packed))
        self.assertEqual(result[:4], (0, 0, -32767, 32767))
        self.assertGreater(result[6], 0)

    def test_decal_preserves_projection_and_transform(self):
        record = bytearray(116)
        struct.pack_into('<I', record, 0, 7)
        record[40:108] = bytes(range(68))
        struct.pack_into('<ii', record, 108, 23, 19)
        result = convert_surface(record, 8)
        self.assertEqual(len(result), 120)
        self.assertEqual(result[44:112], record[40:108])
        self.assertEqual(struct.unpack_from('<ii', result, 112), (8, 19))

    def test_road_buffers_and_physics_survive_tangent_expansion(self):
        record = bytearray(108)
        struct.pack_into('<I', record, 0, 11)
        struct.pack_into('<5I', record, 80, 3, 3, 3, 1, 4)
        vertices = bytes(range(36))
        indices = struct.pack('<3H', 0, 1, 2)
        tangent = struct.pack('<4h', 32766, 1, 0, 32766)
        tail = bytes(range(88))  # physics + authored source points
        record.extend(vertices+indices+tangent*3+tail)
        record.extend(bytes(-len(record)%4))
        result = convert_surface(record, 21)
        self.assertEqual(struct.unpack_from('<i', result, 108)[0], 21)
        self.assertEqual(result[112:154], vertices+indices)
        self.assertEqual(result[154:156], b'\0\0')
        self.assertEqual(result[156:172], expand_tangents(tangent))
        self.assertEqual(result[204:292], tail)
        struct.pack_into('<H', record, 144, 3)
        with self.assertRaises(ValueError):
            convert_surface(record, 21)


if __name__ == '__main__':
    unittest.main()
