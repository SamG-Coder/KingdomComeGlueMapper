"""Structural checks for audit_hlod_structure against synthetic trees."""
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from audit_hlod_structure import structure, span_report, audit, compare
from spatial_hlod import serialize, world_hierarchy, Group


def xml(element):
    return ET.tostring(element, encoding='utf-8')


class HlodStructureTests(unittest.TestCase):
    def test_counts_mixed_types_depth_and_proxies(self):
        root = ET.Element('HLod', Type='Cluster', ProxyIndex='-1', Radius='8000')
        layer = ET.SubElement(root, 'HLod', Type='Layer', ProxyIndex='-1')
        child_a = ET.SubElement(layer, 'HLod', Type='Vegetation', ProxyIndex='0', NearestObserverDistance='0')
        ET.SubElement(layer, 'HLod', Type='Prefab', ProxyIndex='-1', NearestObserverDistance='120')
        ET.SubElement(child_a, 'HLod', Type='Vegetation', ProxyIndex='-1')
        report = structure(root)
        self.assertEqual(report['nodes'], 5)
        self.assertEqual(report['max_depth'], 3)
        self.assertEqual(report['depth_distribution'], {0: 1, 1: 1, 2: 2, 3: 1})
        self.assertEqual(report['node_types'], {'Cluster': 1, 'Layer': 1, 'Vegetation': 2, 'Prefab': 1})
        self.assertEqual(report['proxy_references'], 1)
        self.assertEqual(report['nearest_observer_distances'], {'0': 1, '120': 1, None: 3})
        self.assertEqual(report['hash_attributes'], 0)

    def test_flat_converted_serializer_output_has_no_proxies(self):
        # The known converted-level gap from docs/texture-streaming-comparison.md:
        # the serializer builds Cluster/Vegetation trees and links no proxies.
        leaves = [Group(f'veg_{i}', 'Vegetation', (i * 64.0, 0.0, 0.0), 32.0) for i in range(4)]
        document, data, summary = serialize(world_hierarchy(leaves))
        report = structure(document)
        self.assertEqual(report['proxy_references'], 0)
        self.assertEqual(set(report['node_types']), {'Cluster', 'Layer', 'Vegetation'})
        self.assertGreater(report['max_depth'], 2)
        spans = span_report(document, data)
        self.assertEqual(spans['invalid_blocks'], 0)
        self.assertEqual(spans['valid_blocks'], report['nodes'])

    def test_span_report_isolates_malformed_blocks(self):
        root = ET.Element('HLod', DataOffset='4', DataSize='8', ProxyIndex='-1')
        ET.SubElement(root, 'HLod', DataOffset='12', DataSize='999999', ProxyIndex='-1')
        ET.SubElement(root, 'HLod', DataOffset='abc', DataSize='4', ProxyIndex='-1')
        report = span_report(root, b'\x00' * 100)
        self.assertEqual(report, {'valid_blocks': 1, 'invalid_blocks': 2})

    def test_audit_reads_archive_and_tolerates_missing_payload(self):
        document = ET.Element('HLod', Type='Cluster', ProxyIndex='-1')
        ET.SubElement(document, 'HLod', Type='Vegetation', ProxyIndex='-1')
        with tempfile.NamedTemporaryFile(suffix='.pak', delete=False) as temporary:
            path = Path(temporary.name)
        self.addCleanup(path.unlink)
        with zipfile.ZipFile(path, 'w') as pak:
            pak.writestr('terrain/hlods.xml', xml(document))
        report = audit(path)
        self.assertEqual(report['nodes'], 2)
        self.assertIsNone(report['spans'])

    def test_structure_rejects_missing_root(self):
        with self.assertRaisesRegex(ValueError, 'No HLod root'):
            structure(ET.fromstring('<Other/>'))

    def test_compare_reports_deltas_and_type_drift(self):
        small = {'nodes': 3, 'max_depth': 1, 'proxy_references': 0,
                 'node_types': {'Cluster': 1, 'Vegetation': 2},
                 'nearest_observer_distances': {'0': 3}}
        large = {'nodes': 5, 'max_depth': 3, 'proxy_references': 4,
                 'node_types': {'Cluster': 1, 'Vegetation': 2, 'Prefab': 2},
                 'nearest_observer_distances': {'0': 5}}
        report = compare(small, large, 'other.pak')
        self.assertEqual(report['deltas'], {'nodes': 2, 'max_depth': 2, 'proxy_references': 4})
        self.assertEqual(report['first']['nodes'], 3)
        self.assertEqual(report['second']['nodes'], 5)
        self.assertEqual(report['node_types_first'], small['node_types'])
        self.assertEqual(report['node_types_second'], large['node_types'])


if __name__ == '__main__':
    unittest.main()
