import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from skald_quest_probe import generate


class QuestProbeTests(unittest.TestCase):
    def test_objective_is_selected_by_source_id_and_bound_to_typed_state(self):
        model = self.model('arbitraryQuest')
        row = dict(objective_id='73', quest_id='42', objective_name='someTask', is_hidden='False')
        model['tables']['quest_objective'] = dict(rows=[row])
        files, report = generate(model, 'TestProject', '73')
        root = ET.fromstring(files['TestProject/arbitraryQuest.xml'])
        self.assertEqual(root.find('.//Objective').get('Name'), 'someTask')
        self.assertEqual(root.find('.//someTask/Edge').get('From'), 'objective_progress.State')
        self.assertEqual(report['diagnostic_objective'], row)
        with self.assertRaises(ValueError): generate(model, 'TestProject', '74')
        row['quest_id'] = 'anotherQuest'
        with self.assertRaises(ValueError): generate(model, 'TestProject', '73')

    def model(self, name):
        return dict(quest=name, quest_id='42', tables={'quest': dict(
            rows=[dict(quest_name=name, quest_id='42')], provenance=[{'sha256': 'source'}])})

    def test_generic_identity_and_native_progress_interface(self):
        for name in ('different_quest', 'anotherQuest'):
            model = self.model(name); original = copy.deepcopy(model)
            files, report = generate(model, 'TestProject')
            quest = ET.fromstring(files['TestProject/' + name + '.xml']).find('./Skald/Quest')
            self.assertEqual(quest.get('Name'), name)
            self.assertEqual(quest.find('./Nodes/Output/Edge').get('To'), 'Progress')
            self.assertEqual([e.get('To') for e in quest.findall('./Nodes/State/Edge')],
                             ['SetActive', 'SetDone', 'SetFailed'])
            self.assertFalse(report['source_activation_translated'])
            self.assertEqual(model, original)

    def test_rejects_mismatched_identity_and_unsafe_names(self):
        model = self.model('valid'); model['quest_id'] = '43'
        with self.assertRaises(ValueError): generate(model, 'TestProject')
        with self.assertRaises(ValueError): generate(self.model('../bad'), 'TestProject')


if __name__ == '__main__': unittest.main()
