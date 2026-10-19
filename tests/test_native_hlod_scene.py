import math
from pathlib import Path
import struct
import sys
import unittest
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from editor_archive import decode, encode, encode_string
from native_hlod_scene import decompose, identity, vegetation_instance


class NativeSceneTests(unittest.TestCase):
    def test_archive_roundtrip_and_rejects_truncation(self):
        root = ET.Element('VegetationMap', Version='3')
        blocks = {'VegetationInstancesArray': bytes(3300)}
        for compress in (False, True):
            data = encode(root, blocks, compress)
            restored, result = decode(data)
            self.assertEqual(restored.attrib, root.attrib)
            self.assertEqual(result, blocks)
            for bad in (data[:-1], data + b'x'):
                with self.assertRaises(ValueError):
                    decode(bad)

    def test_rejects_duplicate_blocks_and_unknown_flags(self):
        prefix = encode_string(b'<VegetationMap/>')
        block = encode_string('same') + struct.pack('<III', 1, 1, 0) + b'x'
        with self.assertRaisesRegex(ValueError, 'metadata'):
            decode(prefix + struct.pack('<I', 2) + block * 2)
        with self.assertRaisesRegex(ValueError, 'metadata'):
            decode(prefix + struct.pack('<I', 1) + encode_string('x') + struct.pack('<III', 1, 1, 8) + b'x')

    def test_placement_identity_is_not_asset_identity(self):
        a = bytes(104); b = a[:-1] + b'\x01'
        self.assertNotEqual(identity(a, 0), identity(b, 0))
        self.assertNotEqual(identity(a, 0), identity(a, 1))
        self.assertEqual(identity(a, 0), identity(a, 0))

    def test_transform_preserves_rotation_reflection_and_scale(self):
        for angle in (0, .2, math.pi, math.pi * 1.5):
            for scales in ((2, 3, 4), (-2, 3, 4)):
                c, s = math.cos(angle), math.sin(angle)
                original = np.column_stack((np.array([[c,-s,0],[s,c,0],[0,0,1]]) @ np.diag(scales), [10,20,30]))
                pos, q, scale = decompose(original)
                w,x,y,z = q
                r = np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                              [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                              [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
                np.testing.assert_allclose(np.column_stack((r @ np.diag(scale), pos)), original, atol=1e-12)
        with self.assertRaisesRegex(ValueError, 'Sheared'):
            decompose([[1,.2,0,0],[0,1,0,0],[0,0,1,0]])

    def test_vegetation_layout_and_dependency_checks(self):
        record = bytearray(64); struct.pack_into('<I', record, 0, 2)
        struct.pack_into('<H', record, 40, 3)
        struct.pack_into('<4f', record, 44, 100, 200, 32, .8)
        record[60:64] = bytes([127, 64, 128, 255])
        packed = vegetation_instance(bytes(record), 4)
        self.assertEqual(len(packed), 33)
        values = struct.unpack('<4fiB3f', packed)
        self.assertEqual(values[:3], (100,200,32)); self.assertEqual(values[4:6], (3,127))
        self.assertAlmostEqual(values[-1], 2 * math.pi, places=6)
        with self.assertRaises(ValueError):
            vegetation_instance(bytes(record), 3)


if __name__ == '__main__':
    unittest.main()
