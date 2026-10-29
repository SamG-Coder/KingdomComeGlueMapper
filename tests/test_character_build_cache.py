from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import character_build_cache as cache


class CharacterBuildCacheTests(unittest.TestCase):
    def test_one_batch_reuses_inputs_but_next_build_reads_afresh(self):
        with patch.object(cache, 'RetailSourceReader') as reader, patch.object(cache, 'Assets') as assets, \
             patch.object(cache, 'SourceTables') as tables:
            with cache.appearance_batch():
                for _ in range(3):
                    with ExitStack() as actor:
                        cache.character_inputs('source', 'target', actor)
                self.assertEqual(reader.call_count, 1)
                self.assertEqual(assets.call_count, 1)
                reader.return_value.__exit__.assert_not_called()
            self.assertEqual(reader.return_value.__exit__.call_count, 1)
            with cache.appearance_batch(), ExitStack() as actor:
                cache.character_inputs('source', 'target', actor)
            self.assertEqual(reader.call_count, 2)
            self.assertEqual(tables.call_count, 2)

    def test_failure_closes_scope_and_different_installs_do_not_share(self):
        with patch.object(cache, 'RetailSourceReader') as reader, patch.object(cache, 'Assets'), \
             patch.object(cache, 'SourceTables'):
            with self.assertRaisesRegex(ValueError, 'stop'):
                with cache.appearance_batch(), ExitStack() as actor:
                    cache.character_inputs('source', 'one', actor)
                    cache.character_inputs('source', 'two', actor)
                    raise ValueError('stop')
            self.assertEqual(reader.call_count, 2)
            self.assertEqual(reader.return_value.__exit__.call_count, 2)
            self.assertIsNone(cache._ACTIVE.get())
