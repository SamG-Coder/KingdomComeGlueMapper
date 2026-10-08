import sys
import struct
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_clothing_regions import sample_skin
from clothing_regions import Chunk, Skin, read_chunks, write_chunks, assign_body_regions
from audit_npc_effects import audit_material
from clothing_assembly import assemble_region_skins, convert_fixed_outfit
from clothing_appearance import apply_armor_colorization, bake_clothing_variant


def garment_skin(morph=False):
    blob, _ = sample_skin()
    chunks = read_chunks(blob)
    mesh = bytearray(chunks[0].data)
    replacements = {32: struct.pack('<I', 0)}
    bones = bytearray(32+584)
    bones[344:349] = b'Spine'
    chunks.append(Chunk(0x2000, 0x800, 39, bytes(bones)))
    chunks.append(Chunk(0x1014, 0x802, 49, b'test'.ljust(128, b'\0')+struct.pack('<II', 0, 0)))
    positions = [(float(i), 0., 1.) for i in range(6)]
    for kind, width, payload in (
        (0, 12, b''.join(struct.pack('<3f', *p) for p in positions)),
        (1, 12, struct.pack('<3f', 0, 0, 1)*6),
        (2, 8, bytes(8*6)), (6, 16, bytes(16*6)),
        (9, 12, struct.pack('<4H4B', 0, 0, 0, 0, 255, 0, 0, 0)*6)):
        struct.pack_into('<I', mesh, 28+kind*4, 50+kind)
        chunks.append(Chunk(0x1016, 0x800, 50+kind,
                            struct.pack('<6I', 0, kind, 6, width, 0, 0)+payload))
    internal = bytearray(32+64*6)
    for i, mapped in enumerate(Skin(blob).remap):
        struct.pack_into('<3f', internal, 32+64*mapped+12, *positions[i])
    replacements[33] = bytes(internal)
    replacements[10] = bytes(mesh)
    if morph:
        name = b'#V_001\0'
        header = struct.pack('<4I6fIf', 0xffffffff, len(name), 6, 6,
                             0, 0, 0, 0, 0, 2, 1, 2)
        replacements[32] = struct.pack('<I', 1)+header+name+struct.pack('<2H', 0, 6)+bytes([0, 0, 255])*6
    return write_chunks(chunks, replacements)


class ClothingAssemblyTests(unittest.TestCase):
    def test_assembly_offsets_both_topologies_and_materials(self):
        source = garment_skin()
        before = Skin(source)
        after = Skin(assemble_region_skins([source, source], [0, 10], 'outfit'))
        self.assertEqual(after.vertex_count, 12)
        self.assertEqual(after.faces, before.faces+[tuple(v+6 for v in f) for f in before.faces])
        self.assertEqual(after.remap, before.remap+[v+6 for v in before.remap])
        self.assertEqual([struct.unpack_from('<I', r, 16)[0] for r in after.subset_records], [4, 9, 14, 19])
        self.assertEqual(after.streams[9].data[24:], before.streams[9].data[24:]*2)
        self.assertEqual(struct.unpack_from('<20i', after.one(0x1014).data, 132), (-1,)*20)

    def test_rejects_unbaked_variants_and_conflicting_bind_pose(self):
        with self.assertRaisesRegex(ValueError, 'Bake garment'):
            assemble_region_skins([garment_skin(True)], [0], 'outfit')
        blob = garment_skin()
        chunks = read_chunks(blob)
        bones = bytearray(next(c.data for c in chunks if c.kind == 0x2000))
        struct.pack_into('<f', bones, 32+216, 1)
        other = write_chunks(chunks, {39: bytes(bones)})
        with self.assertRaisesRegex(ValueError, 'different bind pose'):
            assemble_region_skins([blob, other], [0, 10], 'outfit')

    def test_assembly_resolves_explicit_mesh_material_with_unused_chunk(self):
        chunks = read_chunks(garment_skin())
        unused = b'unused'.ljust(128, b'\0')+struct.pack('<II', 0, 0)
        chunks.append(Chunk(0x1014, 0x802, 70, unused))
        node = bytearray(80)
        struct.pack_into('<I', node, 64, 10)
        struct.pack_into('<I', node, 76, 49)
        chunks.append(Chunk(0x100b, 0x824, 71, bytes(node)))
        output = read_chunks(assemble_region_skins([write_chunks(chunks, {})], [0], 'outfit'))
        self.assertTrue(next(c for c in output if c.id == 49).data.startswith(b'outfit\0'))
        self.assertEqual(next(c for c in output if c.id == 70).data, unused)
        node = bytearray(node)
        struct.pack_into('<I', node, 76, 999)
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            assemble_region_skins([write_chunks(chunks, {71: bytes(node)})], [0], 'outfit')

    def test_baking_changes_render_and_internal_positions_once(self):
        blob = bake_clothing_variant(garment_skin(True), '#V_001')
        skin = Skin(blob)
        self.assertEqual(skin.positions(), [(float(i), 0., 3.) for i in range(6)])
        for i, mapped in enumerate(skin.remap):
            self.assertEqual(struct.unpack_from('<3f', skin.one(0x2005).data, 32+mapped*64+12), skin.positions()[i])
        self.assertEqual(skin.one(0x2002).data, bytes(4))
        with self.assertRaises(ValueError):
            bake_clothing_variant(blob, '#V_001')

    def test_fixed_outfit_preserves_materials_and_face_coverage(self):
        material = ET.fromstring('<Material><SubMaterials>'+''.join(
            f'<Material Name="slot{i}" Shader="Illum"/>' for i in range(10))+'</SubMaterials></Material>')
        source = dict(name='shirt', skin=garment_skin(True), material=material,
                      morph='#V_001', face_regions=['arms', 'torso', 'torso', 'arms'])
        outputs = convert_fixed_outfit([source, dict(source, name='coat')], 'outfit_')
        self.assertEqual(sum(o['triangles'] for o in outputs.values()), 8)
        for output in outputs.values():
            self.assertEqual(len(output['material'].find('SubMaterials')), 20)
            self.assertEqual(output['sources'], ['shirt', 'coat'])
        self.assertEqual(len(material.find('SubMaterials')), 10)

    def test_tint_is_per_item_and_preserves_other_parameters(self):
        root = ET.fromstring('<Material Shader="Illum"><PublicParams Other="7" MaskHue2="13"/></Material>')
        tinted = apply_armor_colorization(root, dict(zone1_hue='.25', zone1_saturation='.5', brightness='.7'))
        self.assertEqual(tinted.find('PublicParams').get('MaskHue1'), '90')
        self.assertEqual(tinted.find('PublicParams').get('MaskHue2'), '13')
        self.assertEqual(tinted.find('PublicParams').get('Other'), '7')
        self.assertIsNone(root.find('PublicParams').get('MaskHue1'))
        with self.assertRaises(ValueError):
            apply_armor_colorization(root, dict(brightness='nan'))

    def test_body_partition_uses_named_bones_and_covers_all_faces(self):
        blob = garment_skin()
        self.assertEqual(assign_body_regions(blob), ['torso']*4)
        chunks = read_chunks(blob)
        bones = bytearray(next(c.data for c in chunks if c.kind == 0x2000))
        bones[344:600] = b'LeftHand'.ljust(256, b'\0')
        self.assertEqual(assign_body_regions(write_chunks(chunks, {39: bytes(bones)})), ['hands']*4)

    def test_effect_audit_does_not_mistake_legacy_maps_for_native_support(self):
        m = ET.fromstring('<Material Shader="Illum" StringGenMask="%DIRTLAYER"><Textures><Texture Map="Custom" File="scratches.dds"/></Textures></Material>')
        row = audit_material(m)[0]
        self.assertFalse(row['clothing_shader_enabled'])
        self.assertFalse(row['runtime_effects_verified'])
        skin = ET.fromstring('<Material Shader="Humanskin"><Textures><Texture Map="Decal" File="dirtblood.dds"/></Textures></Material>')
        self.assertFalse(audit_material(skin)[0]['native_mask_bound'])


if __name__ == '__main__':
    unittest.main()
