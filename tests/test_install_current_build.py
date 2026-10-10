import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from current_build import paths, file_manifest, file_hash, write_json
from install_current_build import install, safe_path


class InstallCurrentBuildTests(unittest.TestCase):
    def prepare(self, root):
        layout = paths(root / 'outputs')
        target = root / 'retail'
        relative = 'Mods/example/Data/example.pak'
        for directory, value in ((layout['current'], b'new'), (target, b'old')):
            destination = directory / relative
            destination.parent.mkdir(parents=True)
            destination.write_bytes(value)
        write_json(layout['current'] / 'build.json', dict(
            generated=True, checked_at='checkpoint', mods=['example'],
            validation={'native_tables_verified': True}, files=file_manifest(layout['current'])))
        return layout, target, relative

    def test_installs_verified_build_and_preserves_unmanaged_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout, target, relative = self.prepare(Path(tmp))
            note = target / 'Mods/example/notes.txt'
            note.write_text('manual')
            result = install(layout, target, check_closed=lambda: None)
            self.assertEqual((target / relative).read_bytes(), b'new')
            self.assertEqual(note.read_text(), 'manual')
            self.assertEqual(result['changed_files'], [relative])
            self.assertTrue(json.loads((layout['current'] / 'build.json').read_text())['installed'])
            self.assertFalse((layout['work'] / 'install').exists())
            self.assertEqual(install(layout, target, check_closed=lambda: None)['changed_files'], [])

    def test_failed_replacement_restores_previous_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout, target, relative = self.prepare(Path(tmp))
            original = Path.replace
            def fail_staged(path, destination):
                if path.is_relative_to(layout['work'] / 'install/new'):
                    raise OSError('Simulated destination lock')
                return original(path, destination)
            with patch.object(Path, 'replace', fail_staged), self.assertRaises(OSError):
                install(layout, target, check_closed=lambda: None)
            self.assertEqual((target / relative).read_bytes(), b'old')

    def test_rejects_modified_build_and_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout, target, relative = self.prepare(Path(tmp))
            (layout['current'] / relative).write_bytes(b'unvalidated')
            with self.assertRaises(ValueError): install(layout, target, check_closed=lambda: None)
            self.assertEqual((target / relative).read_bytes(), b'old')
            with self.assertRaises(ValueError): safe_path(target, '../outside.pak')

    def test_obsolete_managed_shard_removed_and_restored_on_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                layout, target, relative = self.prepare(Path(tmp))
                old = 'Mods/example/Data/example_population_002.pak'
                (target / old).write_bytes(b'obsolete')
                write_json(layout['logs'] / 'current-build-installation.json',
                           dict(target=str(target), files={old: file_hash(target / old)}))
                original = Path.replace
                def fail_staged(path, destination):
                    if failure and path.is_relative_to(layout['work'] / 'install/new'):
                        raise OSError('Simulated destination lock')
                    return original(path, destination)
                with patch.object(Path, 'replace', fail_staged):
                    if failure:
                        with self.assertRaises(OSError): install(layout, target, check_closed=lambda: None)
                        self.assertEqual((target / old).read_bytes(), b'obsolete')
                    else:
                        result = install(layout, target, check_closed=lambda: None)
                        self.assertEqual(result['removed_obsolete_files'], [old])
                        self.assertFalse((target / old).exists())

    def test_modified_obsolete_shard_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout, target, relative = self.prepare(Path(tmp))
            old = 'Mods/example/Data/example_population_002.pak'
            (target / old).write_bytes(b'old')
            write_json(layout['logs'] / 'current-build-installation.json',
                       dict(target=str(target), files={old: file_hash(target / old)}))
            (target / old).write_bytes(b'manual change')
            with self.assertRaises(ValueError): install(layout, target, check_closed=lambda: None)
            self.assertEqual((target / old).read_bytes(), b'manual change')


if __name__ == '__main__': unittest.main()
