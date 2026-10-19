from pathlib import Path
import sys
import struct
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from setup_streaming import family_gaps, texture_families, mesh_families
from texture_streaming import split_generated
from test_texture_streaming import texture


class StreamingSetupTests(unittest.TestCase):
    def test_setup_rejects_stripped_geometry_needed_by_resident_physics(self):
        from test_mesh_streaming import fixture
        from clothing_regions import read_chunks, write_chunks
        from mesh_streaming import split
        chunks = read_chunks(fixture())
        mesh = next(c for c in chunks if c.kind == 0x1000)
        raw = bytearray(mesh.data)
        struct.pack_into('<I', raw, 4, 8)
        struct.pack_into('<I', raw, 92, 6)
        header, payload = split(write_chunks(chunks, {mesh.id:bytes(raw)}))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'models.pak'
            name = 'gluebuild/physics-dependent.cgf'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr(name, header)
                archive.writestr(name+'m', payload)
            with zipfile.ZipFile(path) as archive:
                index = {n:[(archive,n)] for n in archive.namelist()}
                with self.assertRaisesRegex(ValueError, 'physics-dependent.*physics requires resident'):
                    mesh_families(index)

    def test_selected_bases_require_all_generated_siblings_but_unused_families_do_not(self):
        selected={'gluebuild/house.cgf','gluebuild/wall.dds','gluebuild/wall.dds.1'}
        generated=selected|{'gluebuild/house_lod1.cgf','gluebuild/wall.dds.2',
            'gluebuild/wall.dds.a','gluebuild/wall.dds.1a',
            'gluebuild/unselected_lod2.cgf','gluebuild/unselected.dds.1'}
        self.assertEqual(family_gaps(selected,generated),{
            'gluebuild/house_lod1.cgf','gluebuild/wall.dds.2',
            'gluebuild/wall.dds.a','gluebuild/wall.dds.1a'})

    def test_final_family_is_checked_across_archive_shards(self):
        parts=split_generated(texture(512,512))
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); base='gluebuild/wall.dds'
            with zipfile.ZipFile(root/'base.pak','w') as z: z.writestr(base,parts[''])
            with zipfile.ZipFile(root/'mips.pak','w') as z:
                for suffix,blob in parts.items():
                    if suffix: z.writestr(base+suffix,blob)
            with zipfile.ZipFile(root/'base.pak') as a,zipfile.ZipFile(root/'mips.pak') as b:
                index={base:[(a,base)]}|{name:[(b,name)] for name in b.namelist()}
                self.assertEqual(texture_families(index),1)
                del index[base+'.1']
                with self.assertRaisesRegex(ValueError,'Missing texture mip'): texture_families(index)


if __name__=='__main__': unittest.main()
