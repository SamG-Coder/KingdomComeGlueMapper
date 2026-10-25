import sys
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from world_ledges import (LAYER, _layer_xml, _leveldata_xml, _reserved_xml, convert,
                          next_entity_id, plan, prepare_level, read_ledges, rewrite_level)


def level_fixture(folder, *, metadata=False):
    pak = Path(folder) / 'level.pak'
    with zipfile.ZipFile(pak, 'w') as output:
        output.comment = b'keep archive metadata'
        output.writestr('objects_mission0.xml', b'<Mission><Entity EntityId="42"/></Mission>')
        output.writestr('layers/existing.xml', b'<Objects><Entity EntityId="500"/></Objects>')
        output.writestr('leveldata.xml', b'<LevelData><Layers><Layer Name="existing" Id="7" Specs="-1"/></Layers></LevelData>')
        output.writestr('extractedlayerentityids.xml', b'<ReservedEntityIDsFromLayers><ReservedEntityID ID="100"/></ReservedEntityIDsFromLayers>')
        output.writestr('terrain/unchanged.bin', b'unchanged terrain payload', compress_type=zipfile.ZIP_DEFLATED)
        if metadata:
            output.writestr('whdata_1', b'<Root version="1"><GameProfileManager><GameProfiles><GameProfile><Name>arrival</Name><Id>5</Id></GameProfile></GameProfiles><GameProfileGroups><Groups><Group>keep</Group></Groups></GameProfileGroups></GameProfileManager><OtherManager value="keep"/></Root>')
    return pak


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
    def test_prepare_preserves_existing_profiles_reservations_and_layer_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            pak = level_fixture(folder, metadata=True)
            with zipfile.ZipFile(pak) as source:
                before = ET.fromstring(source.read('whdata_1'))
            members, report = prepare_level(fixture(), pak, replace=True)
            after = ET.fromstring(members['whdata_1'])
            for manager in ('GameProfileManager', 'OtherManager'):
                self.assertEqual(ET.tostring(before.find(manager)), ET.tostring(after.find(manager)))
            self.assertEqual(report['entity_ids'], {'5': 501})
            self.assertFalse(report['runtime_verified'])
            reserved = ET.fromstring(members['extractedlayerentityids.xml'])
            self.assertEqual([row.get('ID') for row in reserved], ['100', '501'])
            layers = ET.fromstring(members['leveldata.xml']).find('Layers')
            self.assertEqual([(row.get('Name'), row.get('Id')) for row in layers], [('existing', '7'), (LAYER, '8')])

    def test_replace_is_repeatable_and_keeps_ledge_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            pak = level_fixture(folder)
            first, first_report = prepare_level(fixture(), pak)
            rewrite_level(Path(folder), first)
            with self.assertRaisesRegex(ValueError, 'already has whdata_1'):
                prepare_level(fixture(), pak)
            second, second_report = prepare_level(fixture(), pak, replace=True)
            self.assertEqual(second, first)
            self.assertEqual(second_report['entity_ids'], first_report['entity_ids'])

    def test_replace_rejects_colliding_or_unowned_layer_entities(self):
        with tempfile.TemporaryDirectory() as folder:
            pak = level_fixture(folder)
            members, _ = prepare_level(fixture(), pak)
            rewrite_level(Path(folder), members)
            with zipfile.ZipFile(pak) as source:
                layer = ET.fromstring(source.read('layers/' + LAYER + '.xml'))
            ident = layer[0].get('EntityId')
            rewrite_level(Path(folder), {'objects_mission0.xml': ('<Mission><Entity EntityId="' + ident + '"/></Mission>').encode()})
            with self.assertRaisesRegex(ValueError, 'collides'):
                prepare_level(fixture(), pak, replace=True)
            layer[0].set('EntityClass', 'NPC')
            rewrite_level(Path(folder), {'layers/' + LAYER + '.xml': ET.tostring(layer)})
            with self.assertRaisesRegex(ValueError, 'unowned'):
                prepare_level(fixture(), pak, replace=True)

    def test_reserved_writer_rejects_id_collision(self):
        _, entities, _, _ = plan(fixture(), 500)
        with self.assertRaisesRegex(ValueError, 'already reserved'):
            _reserved_xml(entities, b'<ReservedEntityIDsFromLayers><ReservedEntityID ID="500"/></ReservedEntityIDsFromLayers>')

    def test_rewrite_streams_unchanged_members_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            pak = level_fixture(folder)
            with zipfile.ZipFile(pak) as source:
                before = source.getinfo('terrain/unchanged.bin')
            with patch.object(zipfile.ZipFile, 'read', side_effect=AssertionError('Unchanged members must stream')):
                rewrite_level(Path(folder), {'new.xml': b'<New/>'})
            with zipfile.ZipFile(pak) as source:
                self.assertEqual(source.comment, b'keep archive metadata')
                self.assertEqual(source.read('terrain/unchanged.bin'), b'unchanged terrain payload')
                after = source.getinfo('terrain/unchanged.bin')
                self.assertEqual((after.compress_type, after.date_time, after.external_attr),
                                 (before.compress_type, before.date_time, before.external_attr))
                self.assertIsNone(source.testzip())

    def test_failed_rewrite_preserves_original_and_cleans_only_its_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            pak = level_fixture(folder)
            original = pak.read_bytes()
            unrelated = Path(folder) / 'level.pak.new'
            unrelated.write_bytes(b'unrelated pending work')
            with patch('world_ledges.shutil.copyfileobj', side_effect=OSError('write failed')):
                with self.assertRaisesRegex(OSError, 'write failed'):
                    rewrite_level(Path(folder), {})
            self.assertEqual(pak.read_bytes(), original)
            self.assertEqual(unrelated.read_bytes(), b'unrelated pending work')
            self.assertEqual(list(Path(folder).glob('ledges-*.pak')), [])

    def test_duplicate_archive_members_are_rejected_before_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            pak = level_fixture(folder)
            with zipfile.ZipFile(pak, 'a') as archive:
                archive.writestr('LEVELDATA.XML', b'<Other/>')
            original = pak.read_bytes()
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                prepare_level(fixture(), pak)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                rewrite_level(Path(folder), {})
            self.assertEqual(pak.read_bytes(), original)

    def test_plan_remaps_linked_ledges_and_synthesizes_entities(self):
        blob = fixture()
        before_markers = [ET.tostring(m) for m in read_ledges(blob).find('Markers')]
        converted, entities, id_map, report = plan(blob, 500)
        root = ET.fromstring(converted)
        ledges = list(root.find('LedgeManager/Ledges'))
        self.assertEqual([int(l.findtext('entityId')) for l in ledges], [500, 0])
        self.assertEqual([int(l.findtext('Flags0')) for l in ledges], [558, 551])
        self.assertEqual([int(l.findtext('Flags1')) for l in ledges], [558, 551])
        self.assertEqual([ET.tostring(m) for m in root.find('LedgeManager/Markers')], before_markers)
        self.assertIsNone(root.find('WeatherProfileManager'))
        self.assertEqual(len(list(root.find('GameProfileManager/GameProfiles'))), 0)
        self.assertEqual(root.get('version'), '1')
        self.assertEqual(id_map, {5: 500})
        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(entity.get('Name'), 'gm_ledge_5')
        self.assertEqual(entity.get('EntityClass'), 'LedgeObject')
        self.assertEqual(entity.get('EntityId'), '500')
        self.assertEqual(entity.get('Pos'), '0,1,2')  # first marker of the linked ledge
        self.assertEqual(entity.get('Layer'), LAYER)
        self.assertEqual(entity.find('Properties').get('bSaved_by_game'), '0')
        self.assertEqual(report['world_ledges'], 1)
        self.assertEqual(report['entity_linked'], 1)
        self.assertTrue(report['weather_profile_manager_dropped'])

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
        converted, report = convert(fixture().replace(b' version="1"', b'', 1), 500)
        self.assertEqual(ET.fromstring(converted).get('version'), '1')

    def test_layer_and_reserved_writers(self):
        _, entities, _, _ = plan(fixture(), 500)
        layer = ET.fromstring(_layer_xml(entities))
        self.assertEqual(layer.tag, 'Objects')
        self.assertEqual([e.get('EntityId') for e in layer], ['500'])
        reserved = ET.fromstring(_reserved_xml(entities))
        self.assertEqual(reserved.tag, 'ReservedEntityIDsFromLayers')
        self.assertEqual([e.get('ID') for e in reserved], ['500'])
        leveldata = b'<LevelData><Layers><Layer Name="a" Id="3" Specs="-1"/></Layers></LevelData>'
        updated = ET.fromstring(_leveldata_xml(leveldata))
        self.assertEqual([(e.get('Name'), e.get('Id')) for e in updated.find('Layers')],
                         [('a', '3'), (LAYER, '4')])
        with self.assertRaisesRegex(ValueError, 'already registered'):
            _leveldata_xml(_leveldata_xml(leveldata))
        with self.assertRaisesRegex(ValueError, 'Layers container'):
            _leveldata_xml(b'<LevelData/>')

    def test_next_entity_id_considers_mission_and_reserved_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            pak = Path(directory) / 'level.pak'
            mission = b'<Mission><Entity Name="a" EntityId="7"/><Entity Name="b" EntityId="42"/></Mission>'
            reserved = b'<ReservedEntityIDsFromLayers><ReservedEntityID ID="100"/></ReservedEntityIDsFromLayers>'
            with zipfile.ZipFile(pak, 'x', zipfile.ZIP_STORED) as output:
                output.writestr('objects_mission0.xml', mission)
                output.writestr('extractedlayerentityids.xml', reserved)
            self.assertEqual(next_entity_id(pak), 101)

    def test_rewrite_level_preserves_other_members_and_is_replaceable(self):
        blob = fixture()
        converted, entities, _, _ = plan(blob, 500)
        members = {'whdata_1': converted, 'layers/' + LAYER + '.xml': _layer_xml(entities)}
        with tempfile.TemporaryDirectory() as directory:
            level = Path(directory) / 'setup_world'
            level.mkdir()
            original = b'hlod-payload'
            with zipfile.ZipFile(level / 'level.pak', 'x', zipfile.ZIP_STORED) as output:
                output.writestr('terrain/hlods.dat', original)
                output.writestr('levelinfo.xml', b'<LevelInfo Name="setup_world"/>')
            rewrite_level(level, members)
            with zipfile.ZipFile(level / 'level.pak') as result:
                self.assertEqual(result.read('terrain/hlods.dat'), original)
                self.assertEqual(result.read('levelinfo.xml'), b'<LevelInfo Name="setup_world"/>')
                self.assertEqual(result.read('whdata_1'), converted)
                self.assertEqual(result.read('layers/' + LAYER + '.xml'), members['layers/' + LAYER + '.xml'])
            rewrite_level(level, members)  # replace path
            with zipfile.ZipFile(level / 'level.pak') as result:
                self.assertEqual(result.read('whdata_1'), converted)


if __name__ == '__main__':
    unittest.main()
