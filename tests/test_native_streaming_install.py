import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from clothing_regions import read_chunks, write_chunks
from install_native_streaming_test import digest, install, plan, rollback
from mesh_streaming import split as unsafe_split
from stage_native_mesh_streaming import stage
from test_mesh_streaming import fixture


class StreamingInstallTests(unittest.TestCase):
    def prepare(self, root):
        source = root/'package'
        (source/'Data').mkdir(parents=True)
        (source/'mod.manifest').write_text('<kcd_mod/>')
        archive = source/'Data/kingdomcomegluemapper_world_000.pak'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('gluebuild/house.cgf', fixture())
        return source, archive, root/'stage'

    def test_install_checks_source_and_preserves_it_on_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            source, original, output = self.prepare(Path(directory))
            expected = original.read_bytes()
            report = stage(source, output)
            self.assertEqual(report['schema'], 2)
            receipt = install(output, [source])
            target = Path(receipt['links'][0]['destination'])
            self.assertTrue(target.is_file())
            with self.assertRaisesRegex(ValueError, 'Roll back'):
                stage(source, output)
            rollback(output)
            self.assertFalse(target.exists())
            self.assertEqual(original.read_bytes(), expected)
            with zipfile.ZipFile(original, 'a') as z:
                z.writestr('changed.txt', 'new build')
            with self.assertRaisesRegex(ValueError, 'source package changed'):
                plan(output, [source])

    def test_old_stage_cannot_be_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output/'staging.json').write_text(json.dumps({'converted':1,'errors':[]}))
            with self.assertRaisesRegex(ValueError, 'physics dependency'):
                plan(output, [])

    def test_valid_hashes_do_not_hide_unsafe_physics_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            source, original, output = self.prepare(Path(directory))
            report = stage(source, output)
            chunks = read_chunks(fixture())
            mesh = next(c for c in chunks if c.kind == 0x1000)
            raw = bytearray(mesh.data)
            struct.pack_into('<I', raw, 4, 8)
            struct.pack_into('<I', raw, 92, 6)
            header, payload = unsafe_split(write_chunks(chunks, {mesh.id:bytes(raw)}))
            archive = Path(report['archives'][0]['path'])
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('gluebuild/house.cgf', header)
                z.writestr('gluebuild/house.cgfm', payload)
            report['archives'][0]['sha256'] = digest(archive)
            (output/'staging.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'physics requires resident'):
                install(output, [source])
            self.assertFalse(list((source/'Data').glob('zz_gm_*.pak')))


if __name__ == '__main__':
    unittest.main()
