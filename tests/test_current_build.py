import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from current_build import paths, promote, remove_generated, file_manifest, write_json, working_environment


class CurrentBuildTests(unittest.TestCase):
    def prepare(self, root):
        layout = paths(root / 'outputs')
        for key in ('current', 'next'):
            directory = layout[key]
            directory.mkdir(parents=True)
            (directory / 'asset.pak').write_bytes(key.encode())
            write_json(directory / 'build.json', dict(generated=True, files=file_manifest(directory)))
        return layout

    def test_replace_generated_build_without_retaining_duplicate_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = self.prepare(Path(tmp))
            promote(layout)
            self.assertEqual((layout['current'] / 'asset.pak').read_bytes(), b'next')
            self.assertFalse(layout['previous'].exists())
            self.assertFalse(layout['backups'].exists())

    def test_preserve_only_manual_changes_in_named_backup_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = self.prepare(Path(tmp))
            (layout['current'] / 'notes.txt').write_text('Keep my edits')
            promote(layout)
            backup, = layout['backups'].iterdir()
            self.assertEqual((backup / 'notes.txt').read_text(), 'Keep my edits')
            self.assertFalse((backup / 'asset.pak').exists())

    def test_failed_publish_restores_original_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = self.prepare(Path(tmp))
            original = Path.rename
            def fail_pending(path, destination):
                if path == layout['next']: raise OSError('Simulated locked destination')
                return original(path, destination)
            with patch.object(Path, 'rename', fail_pending), self.assertRaises(OSError):
                promote(layout)
            self.assertEqual((layout['current'] / 'asset.pak').read_bytes(), b'current')

    def test_cleanup_cannot_escape_and_unmanaged_current_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = self.prepare(Path(tmp))
            with self.assertRaises(ValueError): remove_generated(layout['current'], layout['work'])
            (layout['current'] / 'build.json').unlink()
            with self.assertRaises(ValueError): promote(layout)
            self.assertTrue((layout['current'] / 'asset.pak').exists())

    def test_temporaries_stay_in_workspace_and_concurrent_build_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = paths(Path(tmp) / 'outputs')
            with working_environment(layout):
                with tempfile.TemporaryDirectory() as generated:
                    self.assertTrue(Path(generated).is_relative_to(layout['work']))
                with self.assertRaises(FileExistsError):
                    with working_environment(layout): pass
            self.assertFalse((layout['work'] / 'build.lock').exists())


if __name__ == '__main__': unittest.main()
