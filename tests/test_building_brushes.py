"""Asset-free layout and reference checks for static structures."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from building_brushes import read_brush, convert_brush, resource_table


class BuildingTests(unittest.TestCase):
    def record(self):
        b=bytearray(100)
        struct.pack_into("<I",b,0,1)
        struct.pack_into("<H",b,28,718)
        struct.pack_into("<I",b,32,0x40000108)
        struct.pack_into("<H",b,36,1)
        struct.pack_into("<12f",b,40,0,-2,0,734, 2,0,0,3421, 0,0,2,60)
        struct.pack_into("<HHiI",b,88,13,3,2,17)
        return bytes(b)

    def test_transform_scale_flags_and_material_override(self):
        source=self.record()
        result=convert_brush(source,0,7,11)
        self.assertEqual(len(result),104)
        self.assertEqual(result[44:92],source[40:88])
        self.assertEqual(struct.unpack_from("<Q",result,32)[0],0x40000008)
        self.assertEqual(struct.unpack_from("<H",result,28)[0],0)
        self.assertEqual(struct.unpack_from("<H",result,40)[0],7)
        self.assertEqual(struct.unpack_from("<HHiI",result,92),(0,3,11,17))

    def test_selection_reader_and_invalid_references(self):
        record=read_brush(self.record(),0,["a","Objects/Buildings/house.cgf"],["a","b","c"])
        self.assertEqual(record["position"],[734,3421,60])
        self.assertEqual(record["layer"],718)
        with self.assertRaises(ValueError):read_brush(self.record(),0,["a"],["a","b","c"])
        with self.assertRaises(ValueError):convert_brush(self.record()[:-1],0,0,-1)

    def test_fixed_width_resource_paths(self):
        data=resource_table(["gluebuild/test/mesh0.cgf","gluebuild/test/mesh1.cgf"])
        self.assertEqual(len(data),516)
        self.assertEqual(struct.unpack_from("<I",data)[0],2)
        self.assertEqual(data[4:260].split(b"\0")[0],b"gluebuild/test/mesh0.cgf")
        with self.assertRaises(ValueError):resource_table(["a"*256])


if __name__=="__main__":unittest.main()
