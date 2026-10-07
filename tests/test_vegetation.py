"""Asset-free checks for the observed vegetation record conversion."""
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from vegetation import convert_instance, read_instances, read_all_instances, verify_target_hlods


def source_record():
    b = bytearray(64)
    struct.pack_into("<I6fHbbI HBB4f4B I", b, 0,
                     2, 1,2,3,4,5,6, 19,0,0, 0x40000c08,
                     0,100,100, 752.5,3427.25,59,1.828125,
                     255,80,10,20,0xdeadbeef)
    return bytes(b)


class VegetationTests(unittest.TestCase):
    def test_target_layout_preserves_transform_and_low_flags(self):
        b = convert_instance(source_record(),0,7)
        self.assertEqual(len(b),64)
        self.assertEqual(struct.unpack_from("<Q",b,32)[0],0x40000c08)
        self.assertEqual(struct.unpack_from("<H",b,28)[0],0)
        self.assertEqual(struct.unpack_from("<H",b,40)[0],7)
        self.assertEqual(struct.unpack_from("<4f4B",b,44),
                         (752.5,3427.25,59,1.828125,255,80,10,20))

    def test_unknown_block_remainder_is_skipped_without_guessing(self):
        payload = source_record()+struct.pack("<I",23)+b"opaque"
        data = struct.pack("<HBB6fI",5,0,0,0,0,0,4096,4096,4096,len(payload))+payload
        terrain = SimpleNamespace(data=data,tree_offset=0,tables={"vegetation":{"paths":["tree.cgf"]}})
        records,skipped = read_instances(terrain)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]["pos"],[752.5,3427.25,59])
        self.assertEqual(skipped,{23:1})

    def test_target_hlod_mixed_record_boundaries(self):
        brush = struct.pack("<I",1)+bytes(100)
        veg = convert_instance(source_record(),0,0)
        data = struct.pack("<II",2,len(brush))+brush+struct.pack("<I",len(veg))+veg
        self.assertEqual(verify_target_hlods(data),{1:1,2:1})
        with self.assertRaises(ValueError):
            verify_target_hlods(data[:-1])

    def test_invalid_instance_is_rejected(self):
        with self.assertRaises(ValueError):
            convert_instance(source_record()[:-1],0,0)

    def test_complete_reader_keeps_vegetation_after_brush(self):
        brush=struct.pack('<I',1)+bytes(96)
        payload=source_record()+brush+source_record()
        data=struct.pack('<HBB6fI',5,0,0,0,0,0,4096,4096,4096,len(payload))+payload
        terrain=SimpleNamespace(data=data,version=28,tree_offset=0,tables={'vegetation':{'paths':['tree.cgf']}})
        records,counts=read_all_instances(terrain)
        self.assertEqual([r['offset'] for r in records],[32,196])
        self.assertEqual(counts,{2:2,1:1})
        terrain.data=data[:-1]
        with self.assertRaises(ValueError):read_all_instances(terrain)


if __name__ == "__main__":
    unittest.main()
