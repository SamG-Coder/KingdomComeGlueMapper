import math
import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_trigger_areas import TriggerArea, read_areas, write_areas, convert_areas


def source(areas):
    data = bytearray(struct.pack('<IQI', 1, 91, len(areas)))
    for a in areas:
        data.extend(struct.pack('<QQfI', a.source_wuid or 1, a.guid, a.height, len(a.points)))
        for p in a.points:
            data.extend(struct.pack('<fff', *p))
    return bytes(data)


class TriggerAreasTests(unittest.TestCase):
    def setUp(self):
        self.a = TriggerArea(0x4af3e21ff8c7c304, 4., ((801., 3476., 50.), (802., 3476., 50.), (803., 3475., 49.9)), 0x0f00000000000018)
        self.b = TriggerArea(0x43384f002dbc9cdf, 3., ((1., 2., 3.), (4., 5., 6.)), 42)

    def test_round_trip_preserves_world_geometry_and_guid_not_runtime_wuid(self):
        raw = source([self.a, self.b])
        native, report = convert_areas(raw, 123, {self.a.guid})
        out = read_areas(native)
        self.assertEqual(out['version'], 3)
        self.assertEqual(out['export_version'], 123)
        self.assertEqual(out['areas'][0].guid, self.a.guid)
        self.assertIsNone(out['areas'][0].source_wuid)
        self.assertEqual(out['areas'][0].points, read_areas(raw)['areas'][0].points)
        self.assertEqual(report['source_areas'], 2)
        self.assertEqual(report['converted_areas'], 1)
        self.assertEqual(write_areas(out['areas'], 123), native)

    def test_missing_geometry_and_bad_export_version_fail(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            convert_areas(source([self.a]), 123, {self.b.guid})
        with self.assertRaises(ValueError):
            convert_areas(source([self.a]), 0)

    def test_corrupt_record_rejected(self):
        for bad in (source([self.a])[:-1], source([self.a]) + b'junk',
                    source([self.a, self.a]), b'\x02\0\0\0' + b'\0' * 20,
                    source([TriggerArea(3, math.nan, self.a.points)])):
            with self.subTest(data=bad[:20]), self.assertRaises(ValueError):
                read_areas(bad)


if __name__ == '__main__':
    unittest.main()
