from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from test_character_upgrade import sample_skin
from test_clothing_lab import FakeCatalog
from character_skin_upgrade import CompiledSkin, upgrade_skin
from clothing_coverage import apply_membership
from upgrade_source_bodies import retain_body_registration, body_coverage


class SourceBodyUpgradeTests(unittest.TestCase):
    def test_shape_restoration_retains_topology_and_clears_stale_coverage(self):
        source = sample_skin(morph=True)
        source, _ = upgrade_skin(source, source)
        shrunk, _ = upgrade_skin(source, source, morph_weights={'#V_001': 1})
        shrunk = apply_membership(shrunk, {4: np.ones(CompiledSkin(shrunk).n, bool)})
        restored = CompiledSkin(retain_body_registration(source, shrunk))
        np.testing.assert_array_equal(restored.vertices, CompiledSkin(source).vertices)
        np.testing.assert_array_equal(restored.faces, CompiledSkin(shrunk).faces)
        self.assertTrue(np.all(np.frombuffer(restored.streams[3].data[27::4], np.uint8) == 255))
        self.assertTrue(np.all(np.frombuffer(restored.internal.data[95::64], np.uint8) == 255))

    def test_unequipped_body_has_no_active_garment_mask(self):
        catalog = FakeCatalog()
        source = sample_skin()
        source, _ = upgrade_skin(source, source)
        result, membership = body_coverage(catalog, ['body'], {'EquipmentPart': 'torso'}, source, 0)
        self.assertEqual(membership, {})
        np.testing.assert_array_equal(CompiledSkin(result).vertices, CompiledSkin(source).vertices)
        self.assertTrue(np.all(np.frombuffer(CompiledSkin(result).streams[3].data[27::4], np.uint8) == 255))


if __name__ == '__main__': unittest.main()
