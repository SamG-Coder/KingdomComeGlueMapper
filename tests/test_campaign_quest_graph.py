import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_quest_graph import emit_project


def model():
    objectives = [dict(quest_id='42', objective_id=str(i), objective_name=n,
                       is_hidden=str(i == 3), is_exclusive='False', condition='',
                       autocomplete_timeout_str='', expiration_timeout_str='')
                  for i, n in ((3, 'intro'), (4, 'first'), (5, 'second'), (6, 'returnHome'), (7, 'disabled'))]
    nodes = {'1': dict(attributes={'Class': 'Quest:Begin'}), '2': dict(attributes={'Class': 'Quest:End'})}
    for o in objectives:
        nodes[o['objective_id']] = dict(attributes={'Class': 'Quest:Objective'}, payload=[dict(op='Inputs', attributes=dict(
            Name=o['objective_name'], IsHidden='1' if o['is_hidden'] == 'True' else '0', IsExclusive='0'))])
    transitions = [dict(quest_id='42', from_objective_id=a, to_objective_id=b, enabled=str(enabled))
                   for a, b, enabled in [('1', '3', True), ('3', '4', True), ('3', '5', True),
                                         ('4', '6', True), ('5', '6', True), ('3', '7', False), ('6', '2', True)]]
    edges = [dict(nodeOut=t['from_objective_id'], nodeIn=t['to_objective_id'], portOut='Out', portIn='In',
                  enabled='1' if t['enabled'] == 'True' else '0') for t in transitions]
    return dict(quest='arbitrary_quest', quest_id='42', graph=dict(nodes=nodes, edges=edges, unresolved_edges=[]),
                tables={'quest': dict(rows=[dict(quest_id='42', quest_name='arbitrary_quest', is_activated='False')]),
                        'quest_objective': dict(rows=objectives), 'quest_transition': dict(rows=transitions)})


class QuestGraphTests(unittest.TestCase):
    def test_hidden_objectives_remain_states_and_production_has_no_test_inputs(self):
        m = model(); before = copy.deepcopy(m)
        files, report = emit_project([m], 'Example')
        q = E.fromstring(files['Example/arbitrary_quest.xml'])
        self.assertIsNotNone(q.find(".//State[@Name='objective_3']"))
        self.assertIsNone(q.find(".//Objective[@Name='intro']"))
        self.assertEqual(len(q.findall('.//Objective')), 4)
        self.assertFalse(q.findall('.//HasteTrigger'))
        self.assertFalse(report['campaign_ready'])
        self.assertFalse(report['source_behavior_executed'])
        self.assertEqual(m, before)

    def test_transition_requires_every_predecessor_and_cannot_reopen_completed_destination(self):
        files, _ = emit_project([model()], 'Example')
        q = E.fromstring(files['Example/arbitrary_quest.xml'])
        gates = q.findall(".//If[@Name='advance_6']/Edge")
        self.assertEqual({e.get('From') for e in gates if e.get('To') == 'Exec'},
                         {'objective_4.OnCompleted', 'objective_5.OnCompleted'})
        terms = {e.get('From') for n in q.findall('.//Function') if n.get('Name').startswith('ready_6_') for e in n}
        self.assertTrue({'objective_4.Completed', 'objective_5.Completed', 'objective_6.Unchanged'} <= terms)
        self.assertIsNone(q.find(".//If[@Name='advance_7']"))

    def test_runtime_table_difference_is_reported_and_not_silently_dropped(self):
        m = model(); m['graph']['edges'].pop(1)
        _, report = emit_project([m], 'Example')
        self.assertEqual(report['quests'][0]['graph_transition_differences']['table_only'], [('3', '4', True)])
        m['graph']['unresolved_edges'] = [{'unknown': 'source edge'}]
        with self.assertRaisesRegex(ValueError, 'Unresolved'): emit_project([m], 'Example')

    def test_native_boolean_and_failed_log_contracts_and_explicit_diagnostic_start(self):
        files, _ = emit_project([model()], 'Example', 'arbitrary_quest', diagnostics=True)
        root = E.fromstring(files['Example.xml'])
        self.assertEqual(root.find(".//If[@Name='first_start']/Edge[@To='Condition']").get('From'), 'not_started.bool')
        q = E.fromstring(files['Example/arbitrary_quest.xml'])
        self.assertTrue(all(n.get('Type') == 'Canceled' for n in q.findall(".//EnumLog[@Name='Failed']")))
        self.assertTrue(any(n.get('Name') == 'fail_first' for n in q.findall('.//Port')))
        self.assertTrue(root.findall('.//GameStart'))
        with self.assertRaises(ValueError): emit_project([model()], 'Example', 'not_in_package')


if __name__ == '__main__': unittest.main()
