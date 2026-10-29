import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from setup_campaign import RECEIPT, digest
from setup_profile_population import upgrade, MODE


class SetupPopulationTests(unittest.TestCase):
    def test_shared_builder_publishes_only_changes_and_preserves_receipt_and_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package = root/'package'; (package/'Data').mkdir(parents=True)
            original = root/'baseline.pak'; original.write_bytes(b'old archive')
            os.link(original, package/'Data/change.pak')
            (package/'Data/world.pak').write_bytes(b'large unchanged world')
            receipt = json.dumps({'files': {'owned': 'hash'}}).encode()
            (package/RECEIPT).write_bytes(receipt)
            def build(source, target, baseline, output, profiles, **kwargs):
                from build_profile_population_upgrade import clone_missing
                from filesystem_paths import staging_self_test
                output.mkdir(parents=True)
                self.assertGreater(staging_self_test(output)['path_length'], 260)
                self.assertEqual(profiles, ['any_source_profile'])
                self.assertFalse(kwargs['update_receipt'])
                kwargs['progress']('Convert source character appearances', 3, 8, 'example: Checking clothing coverage')
                staged = output/'package'; clone_missing(baseline, staged)
                replacement = staged/'Data/replacement'; replacement.write_bytes(b'new archive')
                replacement.replace(staged/'Data/change.pak')
                (staged/'Data/actor.pak').write_bytes(b'new actor')
                return dict(version=1, level='kcd1_travel', profiles=profiles, people=2,
                    service_validation=[{}], work={'pending': [{'person':'unsupported_job'}]})
            with patch('build_profile_population_upgrade.build', side_effect=build):
                events = []
                changed, report = upgrade(package, root/'source', root/'target', ('any_source_profile',),
                    notify=lambda *event: events.append(event))
            self.assertEqual(events[0][1:3], (0, 0))
            self.assertIn(('Convert source character appearances', 3, 8, 'example: Checking clothing coverage'), events)
            self.assertEqual(changed, {n: digest(package/n) for n in ('Data/change.pak','Data/actor.pak')})
            self.assertEqual(original.read_bytes(), b'old archive')
            self.assertEqual((package/RECEIPT).read_bytes(), receipt)
            self.assertEqual((package/'Data/world.pak').read_bytes(), b'large unchanged world')
            self.assertEqual(report['mode'], MODE)
            self.assertEqual(report['shops'], 1)
            self.assertEqual(report['work']['pending'][0]['person'], 'unsupported_job')
            self.assertFalse(report['runtime_verified'])
            self.assertEqual(list(root.glob('profile-population-*')), [])

    def test_missing_games_and_cancellation_do_not_build(self):
        with patch('build_profile_population_upgrade.build') as build:
            with self.assertRaisesRegex(ValueError, 'Both source games'):
                upgrade(Path('unused'), None, None, ['profile'])
            with self.assertRaises(InterruptedError):
                upgrade(Path('unused'), 'source', 'target', ['profile'], cancelled=lambda: True)
            build.assert_not_called()

    def test_wrong_profile_output_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package=root/'package'; package.mkdir()
            (package/'keep').write_bytes(b'original')
            with patch('build_profile_population_upgrade.build', return_value=dict(
                    version=1, profiles=['wrong'], level='kcd1_travel', people=1, service_validation=[])):
                with self.assertRaisesRegex(ValueError, 'selected profile'):
                    upgrade(package, root/'source', root/'target', ['requested'])
            self.assertEqual(list(package.iterdir()), [package/'keep'])
            self.assertEqual((package/'keep').read_bytes(), b'original')


if __name__ == '__main__': unittest.main()
