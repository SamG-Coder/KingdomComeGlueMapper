import sys
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from world_ledges import convert, read_ledges, rewrite_level


def marker(x, end):
    return ET.fromstring(
        f'<Marker><worldPosition>{x},1,2</worldPosition>'
        f'<facingDirection>0,1,0</facingDirection><endOrCorner>{end}</endOrCorner></Marker>')


def fixture():
    root = ET.Element('Root', version='1')
    ET.SubElement(root, 'WeatherProfileManager').append(ET.Element('WeatherProfiles'))
    manager = ET.SubElement(root, 'LedgeManager')
    ledges = ET.SubElement(manager, 'Ledges')
    markers = ET.SubElement(manager, 'Markers')
    for entity, flags0, count in ((5, '558', 2), (0, '551', 3)):
        record = ET.SubElement(ledges, 'Ledge')
        ET.SubElement(record, 'entityId').text = str(entity)
        ET.SubElement(record, 'markersStartIdx').text = str(len(markers))
        ET.SubElement(record, 'markersCount').text = str(count)
        ET.SubElement(record, 'ledgeCornerEndAdjustAmount').text = '0.5'
        ET.SubElement(record, 'Flags0').text = flags0
        ET.SubElement(record, 'Flags1').text = flags0
        for i in range(count):
            markers.append(marker(str(len(markers)), 1 if i in (0, count - 1) else 0))
    profiles = ET.SubElement(root, 'GameProfileManager')
    ET.SubElement(profiles, 'GameProfiles').append(ET.SubElement(ET.Element('GameProfile'), 'Name'))
    ET.SubElement(ET.SubElement(profiles, 'GameProfileGroups'), 'Groups')
    return ET.tostring(root, encoding='utf-8')


class WorldLedgesTests(unittest.TestCase):
    def test_conversion_zeroes_entity_links_and_preserves_markers(self):
        blob = fixture()
        before = read_ledges(blob)
        before_markers = [ET.tostring(m) for m in before.find('Markers')]
        converted, report = convert(blob)
        root = ET.fromstring(converted)
        manager = root.find('LedgeManager')
        ledges = list(manager.find('Ledges'))
        self.assertEqual(len(ledges), 2)
        self.assertEqual([int(l.findtext('entityId')) for l in ledges], [0, 0])
        self.assertEqual([int(l.findtext('Flags0')) for l in ledges], [559, 551])
        self.assertEqual([int(l.findtext('Flags1')) for l in ledges], [558, 551])
        self.assertEqual([ET.tostring(m) for m in manager.find('Markers')], before_markers)
        self.assertIsNone(root.find('WeatherProfileManager'))
        profiles = root.find('GameProfileManager')
        self.assertEqual(len(list(profiles.find('GameProfiles'))), 0)
        self.assertEqual(len(list(profiles.find('GameProfileGroups/Groups'))), 0)
        self.assertEqual(report['ledges'], 2)
        self.assertEqual(report['markers'], 5)
        self.assertEqual(report['entity_linked_zeroed'], [5])
        self.assertEqual(report['flags_world_bit_set'], 1)
        self.assertTrue(report['weather_profile_manager_dropped'])
        self.assertEqual(root.get('version'), '1')

    def test_read_ledges_rejects_gaps_and_missing_end_markers(self):
        def gap_fixture():
            root = ET.Element('Root', version='1')
            manager = ET.SubElement(root, 'LedgeManager')
            ledges = ET.SubElement(manager, 'Ledges')
            markers = ET.SubElement(manager, 'Markers')
            for start, count in ((0, 2), (3, 2)):
                record = ET.SubElement(ledges, 'Ledge')
                ET.SubElement(record, 'entityId').text = '0'
                ET.SubElement(record, 'markersStartIdx').text = str(start)
                ET.SubElement(record, 'markersCount').text = str(count)
                ET.SubElement(record, 'ledgeCornerEndAdjustAmount').text = '0.5'
                ET.SubElement(record, 'Flags0').text = '551'
                ET.SubElement(record, 'Flags1').text = '551'
            for i in range(5):
                markers.append(marker(str(i), 1))
            return root
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            read_ledges(ET.tostring(gap_fixture()))
        root = ET.fromstring(fixture())
        for node in root.find('LedgeManager/Markers'):
            node.find('endOrCorner').text = '0'
        with self.assertRaisesRegex(ValueError, 'endOrCorner'):
            read_ledges(ET.tostring(root))
        with self.assertRaisesRegex(ValueError, 'no LedgeManager'):
            read_ledges(b'<Root version="1"/>')
        with self.assertRaisesRegex(ValueError, 'version 1'):
            read_ledges(b'<Root version="2"><LedgeManager/></Root>')
        # KCD1 writes no version attribute; the converter accepts it and emits "1".
        converted, _ = convert(fixture().replace(b' version="1"', b'', 1))
        self.assertEqual(ET.fromstring(converted).get('version'), '1')

    def test_rewrite_level_preserves_other_members_and_is_replaceable(self):
        blob = fixture()
        converted, _ = convert(blob)
        with tempfile.TemporaryDirectory() as directory:
            level = Path(directory) / 'setup_world'
            level.mkdir()
            original = b'hlod-payload'
            with zipfile.ZipFile(level / 'level.pak', 'x', zipfile.ZIP_STORED) as output:
                output.writestr('terrain/hlods.dat', original)
                output.writestr('levelinfo.xml', b'<LevelInfo Name="setup_world"/>')
            rewrite_level(level, converted)
            with zipfile.ZipFile(level / 'level.pak') as result:
                self.assertEqual(result.read('terrain/hlods.dat'), original)
                self.assertEqual(result.read('levelinfo.xml'), b'<LevelInfo Name="setup_world"/>')
                self.assertEqual(result.read('whdata_1'), converted)
            rewrite_level(level, converted)  # replace path
            with zipfile.ZipFile(level / 'level.pak') as result:
                self.assertEqual(result.read('whdata_1'), converted)


if __name__ == '__main__':
    unittest.main()
