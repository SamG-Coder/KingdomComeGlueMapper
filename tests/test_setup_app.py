"""Verify the frozen self-test reports the runtime files it actually checks."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from setup_app import RUNTIME_FILES
from setup_campaign import runtime_directory


class SetupAppTests(unittest.TestCase):
    def test_reported_runtime_count_matches_the_shipped_runtime_directory(self):
        runtime = Path(runtime_directory())
        shipped = sorted(p.name for p in runtime.iterdir() if p.is_file())
        self.assertEqual(sorted(RUNTIME_FILES), shipped)
        self.assertEqual(len(RUNTIME_FILES), len(shipped))

    def test_self_test_reports_the_verified_count_instead_of_a_literal(self):
        source = (Path(__file__).resolve().parents[1] / 'tools/setup_app.py').read_text(encoding='utf-8')
        report = next(line for line in source.splitlines() if 'runtime_files' in line)
        self.assertIn('len(RUNTIME_FILES)', report)
        self.assertNotIn("'runtime_files': 3", source)


if __name__ == '__main__':
    unittest.main()
