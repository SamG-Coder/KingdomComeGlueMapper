from pathlib import Path
import sys
import unittest
import json
import tempfile
import xml.etree.ElementTree as E
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from merge_population_package import merge_xml, assemble


class PopulationPackageTests(unittest.TestCase):
    def test_stale_report_with_missing_overlays_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'source'; output = root/'out'
            report = source/'objects/characters/gluenpc/example/clothing-report.json'
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps(dict(namespace='example', soul_id='id',
                              source_person_registered=True, overlay_files=['Libs/missing.xml'])))
            with self.assertRaisesRegex(ValueError, 'Missing or invalid staged overlay'):
                assemble([source], output)
            self.assertFalse(output.exists())

    def test_shared_native_rows_and_distinct_people_survive(self):
        left = E.fromstring('<database><souls><soul soul_id="native"/><soul soul_id="a"/></souls></database>')
        right = E.fromstring('<database><souls><soul soul_id="native"/><soul soul_id="b"/></souls></database>')
        merged = merge_xml(left, right)
        self.assertEqual([e.get('soul_id') for e in merged.findall('./souls/soul')], ['native', 'a', 'b'])
        self.assertEqual(len(left.findall('./souls/soul')), 2)

    def test_conflicting_character_definition_is_rejected(self):
        left = E.fromstring('<database><Components><Component Name="person"><Body Name="a"/></Component></Components></database>')
        right = E.fromstring('<database><Components><Component Name="person"><Body Name="b"/></Component></Components></database>')
        with self.assertRaisesRegex(ValueError, 'Conflicting XML record'): merge_xml(left, right)

    def test_storm_tasks_accumulate_unique_sources(self):
        left = E.fromstring('<storm><tasks><task name="appearance"><source path="a.xml"/></task></tasks></storm>')
        right = E.fromstring('<storm><tasks><task name="appearance"><source path="b.xml"/></task></tasks></storm>')
        result = merge_xml(left, right)
        self.assertEqual([e.get('path') for e in result.findall('./tasks/task/source')], ['a.xml', 'b.xml'])


if __name__ == '__main__': unittest.main()
