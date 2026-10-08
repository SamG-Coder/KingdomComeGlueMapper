import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from skald_state_probe import convert_assignment, project_entities, execution_context


class SkaldStateProbeTests(unittest.TestCase):
    def test_execution_context_keeps_guards_and_order_without_flattening_branches(self):
        def node(op, children=(), **attrs):
            return dict(op=op, attributes=attrs, children=list(children))
        earlier = node('IfCondition', [node('EnableProfile')], condition='$available')
        root = node('Sequence', [earlier, node('IfCondition', [node('Expression')], condition='$flag == false')])
        context = execution_context(root, 4)
        self.assertEqual(context['path'], 'Root/1/0')
        self.assertEqual(context['ancestors'][-1]['attributes']['condition'], '$flag == false')
        self.assertEqual(context['ordered_predecessors'], [dict(path='Root/0', subtree=earlier)])
        with self.assertRaises(ValueError): execution_context(root, 99)

    def test_project_holder_is_separate_from_file_loader(self):
        blob = project_entities('OtherCampaign', 'Quests/OtherCampaign.xml')
        holder, loader = ET.fromstring(blob).findall('Entity')
        self.assertEqual((holder.get('Name'), holder.get('EntityClass')),
                         ('OtherCampaign', 'SmartObjectHolder'))
        self.assertEqual(loader.get('EntityClass'), 'Concept')
        self.assertNotEqual(holder.get('Name'), loader.get('Name'))
        self.assertNotEqual(holder.get('EntityGuid'), loader.get('EntityGuid'))
        self.assertEqual(loader.find('Properties').get('fileConcept'), 'Quests/OtherCampaign.xml')
        self.assertEqual(blob, project_entities('OtherCampaign', 'Quests/OtherCampaign.xml'))
        with self.assertRaises(ValueError): project_entities('OtherCampaign', 'Quests/../outside.xml')

    def fixture(self, variable='arbitraryFlag', value='true'):
        return {'behavior_documents': {'source': {'provenance': [{'sha256': 'source-hash'}],
            'trees': {'entry': {'variables': [{'name': variable, 'type': '_bool', 'form': 'single',
                'isPersistent': '1', 'values': 'false'}],
                'root': {'op': 'Root', 'attributes': {}, 'children': [
                    {'op': 'Expression', 'attributes': {'expressions': f'"${variable} = {value}"'}, 'children': []}]}}}}}}

    def test_real_conversion_rule_is_name_independent_and_preserves_provenance(self):
        for name, value in [('firstFlag', 'true'), ('anotherFlag', 'false')]:
            model = self.fixture(name, value); original = copy.deepcopy(model)
            blob, report = convert_assignment(model, 'source', 'entry', name, 'CustomProject')
            root = ET.fromstring(blob)
            state = root.find('.//State')
            self.assertEqual(state.get('TypeT'), 'bool')
            self.assertEqual(state.find('Edge').get('To'), 'SetTrue' if value == 'true' else 'SetFalse')
            self.assertEqual(report['source_provenance'][0]['sha256'], 'source-hash')
            self.assertFalse(report['campaign_ready'])
            self.assertEqual(model, original)

    def test_unknown_default_and_compound_expression_are_rejected(self):
        model = self.fixture(); tree = model['behavior_documents']['source']['trees']['entry']
        tree['variables'][0]['values'] = ''
        with self.assertRaisesRegex(ValueError, 'explicit Boolean default'):
            convert_assignment(model, 'source', 'entry', 'arbitraryFlag', 'Probe')
        tree['variables'][0]['values'] = 'false'
        tree['root']['children'][0]['attributes']['expressions'] = '"$arbitraryFlag = true; $other = true"'
        with self.assertRaisesRegex(ValueError, 'exact supported assignment'):
            convert_assignment(model, 'source', 'entry', 'arbitraryFlag', 'Probe')

    def test_duplicate_assignment_and_wrong_type_are_rejected(self):
        model = self.fixture(); tree = model['behavior_documents']['source']['trees']['entry']
        tree['variables'][0]['type'] = '_int'
        with self.assertRaises(ValueError): convert_assignment(model, 'source', 'entry', 'arbitraryFlag', 'Probe')
        tree['variables'][0]['type'] = '_bool'
        tree['root']['children'] *= 2
        with self.assertRaises(ValueError): convert_assignment(model, 'source', 'entry', 'arbitraryFlag', 'Probe')


if __name__ == '__main__': unittest.main()
