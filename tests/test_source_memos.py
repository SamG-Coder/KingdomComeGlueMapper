"""Read-only source and native documents are parsed once per unchanged archive."""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import campaign_dialogue_selection
import character_person
import chairs
from campaign_entity_links import guid_value

MISSION = (b'<Objects><Entity Name="a" EntityId="1" EntityClass="Chair" Pos="0,0,0" Rotate="1,0,0,0" Scale="1,1,1"/>'
           b'<Entity Name="b" EntityId="2" EntityClass="NPC" Layer="x"/></Objects>')
LAYER = b'<Objects><Entity Name="c" EntityId="3" EntityClass="Chair" Layer="l"><Properties/></Entity></Objects>'


def level(path, mission=MISSION, age=60):
    with zipfile.ZipFile(path, 'w') as out:
        out.writestr('objects_mission0.xml', mission)
        out.writestr('layers/l.xml', LAYER)
    old = path.stat().st_mtime_ns - age * 1_000_000_000
    os.utime(path, ns=(old, old))
    return path


def parses(fn):
    """The result and how many documents ElementTree parsed for it."""
    real = ET.fromstring
    with patch.object(ET, 'fromstring', side_effect=real) as counted:
        return fn(), counted.call_count


class SourceMemoTests(unittest.TestCase):
    def setUp(self):
        for memo in (character_person._SOURCE_ENTITIES, chairs._SCANS, campaign_dialogue_selection._NATIVE_SKILLS):
            memo.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_source_entities_are_parsed_once_per_unchanged_archive(self):
        path = level(self.root/'level.pak')
        with zipfile.ZipFile(path) as archive:
            first, parsed = parses(lambda: character_person.source_entities(archive))
            second, again = parses(lambda: character_person.source_entities(archive))
        self.assertEqual(([e.get('Name') for e in first], parsed, again), (['a', 'b'], 1, 0))
        self.assertIsNot(first, second)  # a fresh list of the shared elements
        self.assertIs(first[0], second[0])
        level(path, MISSION.replace(b'"a"', b'"z"'), age=30)
        with zipfile.ZipFile(path) as archive:
            changed, parsed = parses(lambda: character_person.source_entities(archive))
        self.assertEqual(([e.get('Name') for e in changed], parsed), (['z', 'b'], 1))
        buffer = io.BytesIO(path.read_bytes())
        with zipfile.ZipFile(buffer) as archive:
            self.assertEqual((parses(lambda: character_person.source_entities(archive))[1],
                              parses(lambda: character_person.source_entities(archive))[1]), (1, 1))

    def test_chair_inventory_scans_the_source_once(self):
        path = level(self.root/'level.pak')
        with zipfile.ZipFile(path) as archive:
            (entities, seats, deferred), parsed = parses(lambda: chairs.inventory(archive))
            self.assertEqual((sorted(entities), [s.get('EntityId') for s in seats], deferred, parsed),
                             (['1', '2', '3'], ['1'], [{'source_id': '3', 'layer': 'l'}], 2))
            entities.clear()  # a caller's copy, not the memo
            (entities, seats, _), parsed = parses(lambda: chairs.inventory(archive, 'NPC'))
            self.assertEqual((sorted(entities), [s.get('EntityId') for s in seats], parsed), (['1', '2', '3'], [], 0))

    def test_native_skill_checks_scan_scripts_once(self):
        scripts = self.root/'Data/Scripts.pak'
        scripts.parent.mkdir(parents=True)
        with zipfile.ZipFile(scripts, 'w') as out:
            out.writestr('Quests/q.xml', b'<Quest><Sequence Type="Skillcheck" SkillCheckType="speech"/>'
                                          b'<Sequence Type="Skillcheck" SkillCheckType="charisma"/></Quest>')
            out.writestr('Quests/plain.xml', b'<Quest><Sequence Type="Talk"/></Quest>')
            out.writestr('AI/other.xml', b'<Sequence Type="Skillcheck" SkillCheckType="strength"/>')
        first, parsed = parses(lambda: campaign_dialogue_selection.native_skill_checks(self.root))
        second, again = parses(lambda: campaign_dialogue_selection.native_skill_checks(self.root))
        self.assertEqual((first, second, parsed, again), (['charisma', 'speech'], ['charisma', 'speech'], 1, 0))
        first.append('edited')
        self.assertEqual(campaign_dialogue_selection.native_skill_checks(self.root), ['charisma', 'speech'])

    def test_guid_values_are_remembered_and_still_checked(self):
        self.assertEqual(guid_value('0000002a-0001-0002'), 0x2a | 1 << 32 | 2 << 48)
        self.assertEqual(guid_value('0000002a-0001-0002'), 0x2a | 1 << 32 | 2 << 48)
        for bad in ('not-a-guid', '00000000-0000-0000', 42):
            with self.assertRaises(ValueError):
                guid_value(bad)


if __name__ == '__main__':
    unittest.main()
