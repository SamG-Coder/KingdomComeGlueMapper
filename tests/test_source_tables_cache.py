"""SourceTables shares parsed rows across instances without sharing row objects."""
import hashlib
import copy
import gc
import pickle
from pathlib import Path
import sys
import unittest
import weakref
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import campaign_dependency_adapters as adapters
from campaign_dependency_adapters import SourceTables


def table(*rows):
    body = ''.join('<row ' + ' '.join(f'{k}="{v}"' for k, v in row.items()) + '/>' for row in rows)
    return f'<database><table name="t"><rows>{body}</rows></table></database>'.encode()


class Reader:
    """Stands in for RetailSourceReader: the selected candidate carries the data's hash."""
    def __init__(self, data):
        self.data, self.calls = data, 0

    def __call__(self, source, files):
        self.calls += 1
        data = self.data
        return {'table': dict(data=data, candidates=[dict(archive='Data/Tables.pak', revision='base',
                                                          sha256=hashlib.sha256(data).hexdigest())])}


class SourceTablesCacheTests(unittest.TestCase):
    def setUp(self):
        adapters._ROWS.clear()

    def test_rows_are_shared_by_content_but_not_by_object(self):
        reader = Reader(table({'soul_name': 'Ada', 'id': '1'}, {'soul_name': 'bob', 'id': '2'}))
        first, second = SourceTables('game', reader), SourceTables('game', reader)
        self.assertEqual(first.get('rpg/soul')['rows'], second.get('rpg/soul')['rows'])
        first.get('rpg/soul')['rows'][0]['id'] = 'changed'
        self.assertEqual(second.get('rpg/soul')['rows'][0]['id'], '1')
        self.assertEqual(SourceTables('game', reader).get('rpg/soul')['rows'][0]['id'], '1')
        self.assertIs(first.get('rpg/soul'), first.get('rpg/soul'))   # one table per instance, read once
        self.assertEqual(reader.calls, 3)

    def test_changed_table_bytes_are_parsed_again(self):
        reader = Reader(table({'soul_name': 'ada'}))
        self.assertEqual(SourceTables('game', reader).get('rpg/soul')['rows'], [{'soul_name': 'ada'}])
        reader.data = table({'soul_name': 'eve'})
        self.assertEqual(SourceTables('game', reader).get('rpg/soul')['rows'], [{'soul_name': 'eve'}])

    def test_lookup_matches_the_linear_scan(self):
        tables = SourceTables('game', Reader(table({'soul_name': 'Ada', 'id': '1'}, {'soul_name': 'twin', 'id': '2'},
                                                   {'soul_name': 'TWIN', 'id': '3'}, {'id': '4'})))
        self.assertEqual(tables.row('rpg/soul', 'soul_name', 'ADA')['id'], '1')
        self.assertEqual(tables.row('rpg/soul', 'soul_name', '')['id'], '4')   # a missing field matches ''
        self.assertEqual(tables.row('rpg/soul', 'soul_name', 'nobody', optional=True), {})
        for value in ('twin', 'nobody'):
            with self.assertRaisesRegex(ValueError, 'Missing/ambiguous'):
                tables.row('rpg/soul', 'soul_name', value)
        self.assertIs(tables.row('rpg/soul', 'id', '1'), tables.get('rpg/soul')['rows'][0])

    def test_cache_stays_within_its_byte_bound(self):
        with patch.object(adapters, 'ROWS_CACHE_BYTES', 300):
            for name in 'abcdefgh':
                SourceTables('game', Reader(table({'soul_name': name * 40}))).get('rpg/' + name)
            self.assertLessEqual(sum(size for _, size in adapters._ROWS.values()), 300)
            self.assertIn('rpg/h', {key[0] for key in adapters._ROWS})

    def test_edits_to_returned_rows_invalidate_lookup_indexes(self):
        tables = SourceTables('game', Reader(table({'name': 'Ada', 'id': '1'}, {'name': 'Bob', 'id': '2'})))
        row = tables.row('rpg/soul', 'name', 'ada')
        row['name'] = 'Eve'
        self.assertEqual(tables.row('rpg/soul', 'name', 'ada', optional=True), {})
        self.assertIs(tables.row('rpg/soul', 'name', 'eve'), row)
        row.update(name='Bob')
        with self.assertRaisesRegex(ValueError, 'Missing/ambiguous'):
            tables.row('rpg/soul', 'name', 'bob')
        del row['name']
        self.assertIs(tables.row('rpg/soul', 'name', ''), row)
        row.setdefault('name', 'Carol')
        self.assertIs(tables.row('rpg/soul', 'name', 'carol'), row)
        row |= {'name': 'David'}
        self.assertIs(tables.row('rpg/soul', 'name', 'david'), row)
        row.pop('name')
        self.assertIs(tables.row('rpg/soul', 'name', ''), row)
        row.clear()
        self.assertEqual(tables.row('rpg/soul', 'id', '1', optional=True), {})

    def test_exposed_row_lists_keep_original_mutable_scan_semantics(self):
        tables = SourceTables('game', Reader(table({'name': 'Ada', 'id': '1'})))
        tables.row('rpg/soul', 'name', 'ada')
        rows = tables.get('rpg/soul')['rows']
        external = {'name': 'Bob', 'id': '2'}
        rows.append(external)
        self.assertIs(tables.row('rpg/soul', 'name', 'bob'), external)
        external['name'] = 'Carol'  # retained external dict, after a lookup
        self.assertEqual(tables.row('rpg/soul', 'name', 'bob', optional=True), {})
        self.assertIs(tables.row('rpg/soul', 'name', 'carol'), external)
        rows.pop(0)
        self.assertEqual(tables.row('rpg/soul', 'name', 'ada', optional=True), {})

    def test_returned_rows_copy_serialize_and_do_not_retain_the_importer(self):
        tables = SourceTables('game', Reader(table({'name': 'Ada', 'id': '1'})))
        row = tables.row('rpg/soul', 'name', 'ada')
        for clone in (copy.copy(row), copy.deepcopy(row), pickle.loads(pickle.dumps(row))):
            self.assertEqual(clone, row)
            clone['name'] = 'different'
            self.assertEqual(row['name'], 'Ada')
        owner = weakref.ref(tables)
        del tables
        gc.collect()
        self.assertIsNone(owner())
        row['name'] = 'still editable'


if __name__ == '__main__':
    unittest.main()
