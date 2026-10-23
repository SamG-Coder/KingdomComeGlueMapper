from pathlib import Path
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as E
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from audit_ladder_navigation import audit, inspect
from campaign_entity_links import native_guid
from static_navigation_profiles import BASE, encode


SETTINGS = b'\0' * 56


def pair(stem, links=()):
    # One surface polygon, then one off-mesh polygon per synthetic connection.
    count = len(links)
    header = (0x444e4156, 14, 4, 5, 0, 0, 1 + count, 3 + 2 * count,
              3, 0, 0, 0, 0, 0, 0, count, 1)
    tile = bytearray(struct.pack('<17I11f', *header, 1.76, .32, .32, .8,
                                64, 80, 0, 80, 96, 8, 12.5))
    tile.extend(b'\0' * (3 * 4 + count * 24 + (1 + count) * 28))
    for index, (entity, category) in enumerate(links):
        tile.extend(struct.pack('<7fHBBQIIHH', 66, 83, 1, 66, 83, 4, .5,
            1 + index, 1, 255, entity, category, 0x7fffffff, 1, 65535))
    header, mesh = encode({(4, 5): (bytes(tile),)}, SETTINGS)
    return {stem + '.hdr': header, stem + '.nav': mesh}


def mission(*entities):
    root = E.Element('Objects')
    for number, name, kind in entities:
        E.SubElement(root, 'Entity', EntityGuid=native_guid(number), Name=name, EntityClass=kind)
    return root


def fixture():
    entries = pair(BASE, [(101, 3), (900, 2), (904, 3), (905, 3), (906, 8)])
    entries.update(pair('recast/rdnavmesh_some_variant_1', [(102, 3), (900, 2)]))
    installed = mission((101, 'base_ladder', 'Ladder'), (102, 'variant_ladder', 'Ladder'),
                        (103, 'new_ladder', 'Ladder'), (905, 'ordinary_prop', 'BasicEntity'))
    original = E.Element('Entity', EntityClass='Ladder', EntityId='17', Name='original_ladder',
                         EntityGuid=f'{900:016x}')
    native = mission((501, 'reference_ladder', 'Ladder'))
    return entries, installed, {900: original}, pair(BASE, [(501, 3)]), native


class LadderNavigationAuditTests(unittest.TestCase):
    def test_base_variant_and_missing_references_are_distinct(self):
        result = audit(*fixture(), required_names=['new_ladder', 'not_installed'])
        coverage = {r['name']: r for r in result['installed_ladders']}
        self.assertEqual(result['mesh_pairs'], 2)
        self.assertEqual(coverage['base_ladder']['reference_status'], 'base_reference_present')
        self.assertEqual(coverage['variant_ladder']['reference_status'], 'variant_only')
        self.assertEqual(coverage['new_ladder']['reference_status'], 'no_native_category_reference')
        self.assertEqual(result['requested_ladders'], [coverage['new_ladder']])
        self.assertEqual(result['missing_requested_entities'], ['not_installed'])
        self.assertFalse(result['runtime_verified'])
        self.assertFalse(result['polygon_connectivity_verified'])
        self.assertFalse(result['landing_and_bed_approaches_verified'])

    def test_legacy_source_identity_foreign_identity_and_wrong_class_reported(self):
        result = audit(*fixture())
        unresolved = {r['entity_guid']: r for r in result['unresolved_or_wrong_class_references']}
        self.assertEqual(set(unresolved), {native_guid(n) for n in (900, 904, 905)})
        old = unresolved[native_guid(900)]
        self.assertTrue(old['resolves_to_source_ladder'])
        self.assertFalse(old['category_matches_native'])
        self.assertEqual(old['base_references'], 1)
        self.assertEqual(old['total_references'], 2)
        self.assertEqual(old['variant_meshes'], ['recast/rdnavmesh_some_variant_1'])
        self.assertEqual(unresolved[native_guid(905)]['installed_class'], 'BasicEntity')
        self.assertNotIn(native_guid(906), {r['entity_guid'] for r in result['references']})

    def test_source_guid_still_rejected_when_category_alone_was_updated(self):
        args = list(fixture())
        args[0].update(pair(BASE, [(900, 3)]))
        result = audit(*args)
        row = next(r for r in result['references'] if r['entity_guid'] == native_guid(900) and r['category'] == 3)
        self.assertTrue(row['category_matches_native'])
        self.assertTrue(row['resolves_to_source_ladder'])
        self.assertFalse(row['resolves_to_installed_ladder'])

    def test_native_category_is_observed_not_assumed(self):
        args = list(fixture())
        args[0] = pair(BASE, [(101, 7)])
        args[3] = pair(BASE, [(501, 7)])
        result = audit(*args)
        self.assertEqual(result['native_ladder_categories'], [7])
        self.assertEqual(result['installed_ladders'][0]['base_references'], 1)
        args[3] = pair(BASE)
        with self.assertRaisesRegex(ValueError, 'no connections resolving to a Ladder'):
            audit(*args)

    def test_missing_pair_and_ambiguous_entities_fail(self):
        args = list(fixture())
        del args[0]['recast/rdnavmesh_some_variant_1.hdr']
        with self.assertRaisesRegex(ValueError, 'Missing navigation pair'):
            audit(*args)
        args = list(fixture())
        E.SubElement(args[1], 'Entity', EntityGuid=native_guid(101))
        with self.assertRaisesRegex(ValueError, 'Duplicate mission entity GUID'):
            audit(*args)

    def test_structurally_invalid_variant_does_not_escape_audit(self):
        args = list(fixture())
        key = 'recast/rdnavmesh_some_variant_1.nav'
        args[0][key] = args[0][key][:-1]
        with self.assertRaises(ValueError):
            audit(*args)

    def test_package_inspection_is_read_only_and_uses_source_identities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, source, target = (root / n for n in ('package', 'source', 'target'))
            entries, converted, originals, native_entries, native = fixture()
            original = E.Element('Objects'); original.extend(originals.values())
            def archive(path, files):
                path.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(path, 'w') as z:
                    for name, data in files.items():
                        z.writestr(name, data)
            archive(package/'Data/Levels/kcd1_travel/level.pak', {'objects_mission0.xml': E.tostring(converted)})
            archive(package/'Data/Levels/kcd1_travel/recast.pak', entries)
            archive(source/'Data/Levels/rataje/level.pak', {'objects_mission0.xml': E.tostring(original)})
            archive(target/'Data/Levels/trosecko/level.pak', {'objects_mission0.xml': E.tostring(native)})
            archive(target/'Data/Levels/trosecko/recast.pak', native_entries)
            before = {p: p.read_bytes() for p in root.rglob('*.pak')}
            report = inspect(package, source, target, required_names=['new_ladder'])
            self.assertEqual(report['requested_ladders'][0]['base_references'], 0)
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob('*.pak')})


if __name__ == '__main__':
    unittest.main()
