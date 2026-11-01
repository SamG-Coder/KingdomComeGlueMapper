"""Opt-in step caches key on all converter code and the game installs, and stay bounded."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import setup_build_cache
from setup_build_cache import Cache, fingerprint, install_fingerprint, converter_identity


class StepCacheTests(unittest.TestCase):
    def test_install_fingerprint_tracks_archive_changes_only(self):
        with tempfile.TemporaryDirectory() as t:
            game = Path(t); (game/'Data').mkdir(); (game/'Localization').mkdir()
            (game/'Data/a.pak').write_bytes(b'one'); (game/'Localization/English.pak').write_bytes(b'en')
            first = install_fingerprint(game)
            (game/'Data/a.pak').write_bytes(b'two!')
            self.assertEqual(install_fingerprint(game), first)  # remembered for this process
            setup_build_cache._INSTALLS.clear()
            (game/'Data/readme.txt').write_text('not an archive')
            changed = install_fingerprint(game)
            self.assertNotEqual(changed, first)
            setup_build_cache._INSTALLS.clear()
            (game/'Data/readme.txt').write_text('still not an archive')
            self.assertEqual(install_fingerprint(game), changed)

    def test_converter_identity_ignores_worker_counts_but_not_settings(self):
        with patch.dict(os.environ, {'GLUEMAPPER_BAKE_WORKERS': '3'}):
            base = converter_identity()
        with patch.dict(os.environ, {'GLUEMAPPER_BAKE_WORKERS': '9', 'GLUEMAPPER_FULL_VERIFY': '1'}):
            self.assertEqual(converter_identity(), base)
        with patch.dict(os.environ, {'GLUEMAPPER_TEXTURE_FORMAT': 'bc7'}):
            self.assertNotEqual(converter_identity(), base)

    def test_tools_digest_changes_with_any_tools_file(self):
        with tempfile.TemporaryDirectory() as t:
            tools = Path(t)
            (tools/'a.py').write_text('x = 1'); (tools/'__pycache__').mkdir()
            with patch.object(setup_build_cache, 'TOOLS', tools), patch.object(setup_build_cache, '_TOOLS_DIGEST', None):
                first = setup_build_cache.tools_digest()
            (tools/'__pycache__/a.pyc').write_bytes(b'ignored')
            with patch.object(setup_build_cache, 'TOOLS', tools), patch.object(setup_build_cache, '_TOOLS_DIGEST', None):
                self.assertEqual(setup_build_cache.tools_digest(), first)
            (tools/'shader.lua').write_text('changed data')
            with patch.object(setup_build_cache, 'TOOLS', tools), patch.object(setup_build_cache, '_TOOLS_DIGEST', None):
                self.assertNotEqual(setup_build_cache.tools_digest(), first)

    def test_prune_keeps_newest_keys_and_young_unnamed_attempts(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); source = root/'source'; source.write_bytes(b'data')
            cache = Cache(root/'cache')
            keys = [fingerprint(i) for i in range(3)]
            for index, key in enumerate(keys):
                cache.publish(key, {'out': source}, {})
                pointer = cache.folder/(key+'.json')
                old = time.time() - 7200 + index
                os.utime(pointer, (old, old))
                entry = cache.folder/json.loads(pointer.read_text())['entry']
                os.utime(entry, (old, old))
            (cache.folder/'abandoned-attempt').mkdir()
            cache.prune(keep=2)
            self.assertIsNone(cache.lookup(keys[0]))
            self.assertIsNotNone(cache.lookup(keys[1]))
            self.assertIsNotNone(cache.lookup(keys[2]))
            self.assertTrue((cache.folder/'abandoned-attempt').exists())

    def test_container_cache_is_opt_in(self):
        from build_recipe import container_cache
        with tempfile.TemporaryDirectory() as t, patch.dict(os.environ, {'GLUEMAPPER_BUILD_CACHE': t}):
            os.environ.pop('GLUEMAPPER_STEP_CACHE', None)
            self.assertEqual(container_cache(Path(t), Path(t), Path(t), set()), (None, None, None))


if __name__ == '__main__':
    unittest.main()
