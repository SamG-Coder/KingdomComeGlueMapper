from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from compiled_terrain import legacy_height_offset


class TerrainOffsetTests(unittest.TestCase):
    def test_observed_sectors_match_independent_source_road_heights(self):
        # Source compiled road meshes provide the global-grid reference.
        self.assertEqual(legacy_height_offset(47.14442825317383), 47.1)
        self.assertEqual(legacy_height_offset(51.437400817871094), 51.4)

    def test_floor_preserves_grid_and_does_not_round_to_nearest(self):
        self.assertEqual(legacy_height_offset(10.0), 10.0)
        self.assertEqual(legacy_height_offset(10.049), 10.0)
        self.assertEqual(legacy_height_offset(-0.001), -0.05)

    def test_invalid_offset_rejected(self):
        with self.assertRaises(ValueError):
            legacy_height_offset(float('nan'))


if __name__ == '__main__':
    unittest.main()
