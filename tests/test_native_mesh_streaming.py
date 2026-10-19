import sys
from pathlib import Path
import struct
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from clothing_regions import read_chunks, write_chunks
from native_mesh_streaming import split, validate_pair, resident_reason
from mesh_streaming import split as unsafe_split
from test_mesh_streaming import fixture


class NativeStreamingTests(unittest.TestCase):
    def test_physics_vertex_dependency_stays_resident(self):
        chunks = read_chunks(fixture())
        mesh = next(c for c in chunks if c.kind == 0x1000)
        raw = bytearray(mesh.data)
        struct.pack_into('<I', raw, 4, 8)  # NoBreaking, not MeshNotNeeded
        struct.pack_into('<I', raw, 92, 6)
        source = write_chunks(chunks, {mesh.id:bytes(raw)})
        self.assertEqual(resident_reason(source), 'physics_requires_render_geometry')
        self.assertEqual(split(source), (source, None))
        header, payload = unsafe_split(source)
        with self.assertRaisesRegex(ValueError, 'physics requires resident'):
            validate_pair(header, payload)

    def test_independent_compiled_physics_can_stream(self):
        chunks = read_chunks(fixture())
        mesh = next(c for c in chunks if c.kind == 0x1000)
        raw = bytearray(mesh.data)
        struct.pack_into('<I', raw, 4, 12)  # MeshNotNeeded and NoBreaking
        struct.pack_into('<I', raw, 92, 6)
        source = write_chunks(chunks, {mesh.id:bytes(raw)})
        self.assertIsNone(resident_reason(source))
        header, payload = split(source)
        validate_pair(header, payload)

    def test_dangling_controller_is_cleared_only_in_payload(self):
        chunks = read_chunks(fixture())
        node = next(c for c in chunks if c.kind == 0x100b)
        raw = bytearray(node.data)
        struct.pack_into('<i', raw, 188, 12345)
        header, payload = split(write_chunks(chunks, {node.id:bytes(raw)}))
        resident = next(c for c in read_chunks(header) if c.id == node.id)
        streamed = next(c for c in read_chunks(payload) if c.id == node.id)
        self.assertEqual(struct.unpack_from('<i', resident.data, 188)[0], 12345)
        self.assertEqual(struct.unpack_from('<i', streamed.data, 188)[0], -1)

    def test_physics_stays_resident_and_geometry_is_exact(self):
        source = fixture()
        header, payload = split(source)
        original = {c.id:c for c in read_chunks(source)}
        self.assertTrue(any(c.kind == 0x1018 for c in read_chunks(header)))
        self.assertFalse(any(c.kind == 0x1018 for c in read_chunks(payload)))
        for c in read_chunks(payload):
            if c.kind in (0x1016, 0x1017):
                self.assertEqual(c.data, original[c.id].data)
        validate_pair(header, payload)

    def test_resident_parent_is_not_silently_dropped(self):
        source = fixture(); chunks = read_chunks(source)
        node = next(c for c in chunks if c.kind == 0x100b)
        raw = bytearray(node.data); struct.pack_into('<i', raw, 68, 99)
        with self.assertRaisesRegex(ValueError, 'resident-only parent'):
            split(write_chunks(chunks, {node.id:bytes(raw)}))
