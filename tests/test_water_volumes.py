"""Asset-free layout checks for water and surrounding object records."""
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from water_volumes import water_record, read_water, convert_water, append_water
from build_water_probe import translate_features, global_environment_probe
from build_water_surface_probe import suppress_surfaces


def source_water():
    header = bytearray(144)
    struct.pack_into("<I6f", header, 0, 9, 10, 20, 25, 12, 22, 31)
    struct.pack_into("<H", header, 28, 17)
    struct.pack_into("<I", header, 32, 0x40000000)
    struct.pack_into("<I", header, 40, (16 << 24) | 2)
    struct.pack_into("<Q", header, 44, 0x123456789abcdef0)
    struct.pack_into("<i", header, 52, 54)
    struct.pack_into("<IffI", header, 124, 4, 5.0, -2.0, 4)
    aux = bytearray(64)
    struct.pack_into("<I", aux, 48, 0xffffffff)  # observed auxiliary NaN sentinel
    vertices = struct.pack("<12f", 10,20,30, 12,20,30, 12,22,30, 10,22,30)
    return bytes(header + aux) + vertices + vertices


def tree(payload, version):
    data = struct.pack("<HBB6fI", 5 if version == 28 else 8, 0, 0,
                       0,0,0,4096,4096,4096,len(payload)) + payload
    return SimpleNamespace(data=data, version=version, tree_offset=0)


class WaterTests(unittest.TestCase):
    def test_surface_workaround_only_changes_requested_material(self):
        a=convert_water(source_water(), {"offset":0,"size":len(source_water())}, 3, struct.pack('<2f',1000,1000))
        b=bytearray(a)
        struct.pack_into('<Q',b,48,123)
        terrain=tree(a+bytes(b),29)
        out,changes=suppress_surfaces(terrain,{0x123456789abcdef0})
        self.assertEqual(len(changes),1)
        self.assertEqual(out[32+len(a):],bytes(b))
        self.assertEqual(out[:88],terrain.data[:88])
        self.assertEqual(out[92:],terrain.data[92:])
        self.assertEqual(struct.unpack_from('<i',out,88)[0],-1)
        with self.assertRaises(ValueError):suppress_surfaces(terrain,{999})

    def test_global_probe_preserves_properties_without_legacy_identity(self):
        objects = ET.fromstring('<Objects><Entity EntityClass="EnvironmentLight" Name="level_global_probe" Pos="1,2,3" EntityId="99" EntityGuid="old"><Properties BoxSizeX="99999" _nVersion="0"><OptionsAdvanced bDynamic="1" texture_deferred_cubemap="" /></Properties></Entity><Entity EntityClass="Light" Name="unrelated" /></Objects>')
        output = global_environment_probe(objects)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0].get("Pos"), "1,2,3")
        self.assertIsNone(output[0].get("EntityGuid"))
        self.assertIsNone(output[0].get("EntityId"))
        self.assertEqual(output[0].find("Properties").get("BoxSizeX"), "99999")
        self.assertEqual(objects[0].find("Properties").get("_nVersion"), "0")
        explicit = global_environment_probe(objects, "textures/native_probe.dds")
        advanced = explicit[0].find("Properties/OptionsAdvanced")
        self.assertEqual(advanced.get("bDynamic"), "0")
        self.assertEqual(advanced.get("texture_deferred_cubemap"), "textures/native_probe.dds")
        with self.assertRaises(ValueError):
            global_environment_probe(ET.Element("Objects"))

    def test_material_features_use_target_masks_and_preserve_reflections(self):
        doc = ET.Element("Material", GenMask="80001003", StringGenMask="")
        source = {"%SSREFL":1, "%FLOW":2, "%OLD_LIGHTING":0x1000,
                  "%WATER_TESSELLATION_DX11":0x80000000}
        target = {"%SSREFL":4, "%FLOW":8, "%WATER_TESSELLATION_DX11":0x80000000}
        retained, removed = translate_features(doc, source, target)
        self.assertEqual(doc.get("GenMask"), "c")
        self.assertEqual(retained, {"%SSREFL", "%FLOW"})
        self.assertIn("%OLD_LIGHTING", removed)
        self.assertIn("%SSREFL", doc.get("StringGenMask"))

    def test_conversion_preserves_geometry_physics_id_and_auxiliary_sentinels(self):
        data = source_water()
        record = water_record(data, 0, len(data), 28)
        tail = struct.pack("<2f", 1000, 1000)
        converted = convert_water(data, record, 3, tail)
        target = water_record(converted, 0, len(converted), 29)
        self.assertEqual(target["material"], 3)
        self.assertEqual(target["auxiliary"], 18)
        self.assertEqual(converted[148:212], data[144:208])
        self.assertEqual(converted[212:220], tail)
        self.assertEqual(converted[220:], data[208:])
        self.assertEqual(struct.unpack_from("<Q", converted, 48)[0], 0x123456789abcdef0)
        self.assertEqual(struct.unpack_from("<Q", converted, 32)[0], 0x40000000)
        self.assertEqual(struct.unpack_from("<H", converted, 28)[0], 0)
        self.assertEqual(struct.unpack_from("<2f", converted, 132), (5, -2))

    def test_road_alignment_and_version_specific_tangents_reach_water(self):
        source = source_water()
        for version in (28, 29):
            extra = 4 if version == 29 else 0
            road = bytearray(108 + extra)
            struct.pack_into("<I", road, 0, 11)
            struct.pack_into("<5I", road, 80 + extra, 3, 3, 3, 1, 4)
            road.extend(bytes(3*12 + 3*2 + 3*(16 if extra else 8) + 40 + 4*12))
            road.extend(bytes(-len(road) % 4))
            water = source if not extra else convert_water(source, {"offset": 0, "size": len(source)}, -1, struct.pack("<2f",1000,1000))
            records, counts = read_water(tree(bytes(road) + water, version))
            self.assertEqual(counts, {11: 1, 9: 1})
            self.assertEqual(records[0]["offset"], 32 + len(road))

    def test_unknown_records_and_invalid_water_fail_closed(self):
        for data in (source_water()[:-1], bytes(144)):
            with self.assertRaises(ValueError):
                water_record(data, 0, len(data), 28)
        with self.assertRaises(ValueError):
            read_water(tree(struct.pack("<I", 99) + source_water(), 28))
        invalid = bytearray(source_water())
        struct.pack_into("<f", invalid, 208, float("inf"))
        with self.assertRaises(ValueError):
            water_record(invalid, 0, len(invalid), 28)

    def test_append_keeps_base_object_payload_and_materialless_reference(self):
        merged = struct.pack("<I", 23) + bytes(68)
        base_tree = tree(merged, 29)
        base = SimpleNamespace(data=bytes(44) + base_tree.data, tree_offset=44,
                               tables={"materials": {"offset": 44, "paths": []}})
        source = source_water()
        water = convert_water(source, {"offset":0, "size":len(source)}, -1, struct.pack("<2f",1000,1000))
        output = append_water(base, [water], ["gluewater/test/m0"])
        self.assertEqual(struct.unpack_from("<I", output, 4)[0], len(output))
        restored = SimpleNamespace(data=output, tree_offset=300, version=29)
        volumes, counts = read_water(restored)
        self.assertEqual(counts, {23:1, 9:1})
        self.assertEqual(volumes[0]["material"], -1)
        self.assertEqual(output[332:404], merged)


if __name__ == "__main__":
    unittest.main()
