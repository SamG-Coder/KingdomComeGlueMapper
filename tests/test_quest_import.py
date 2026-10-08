import sys
from pathlib import Path
import unittest
import tempfile
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from quest_import import behavior_path, import_quest, parse_behavior, parse_graph, resolve_null_tracking_edges, walk


GRAPH = b'''<Graph><Nodes><Node Id="2" Class="Quest:Begin"><Inputs Name="q_test"/></Node>
<Node Id="5" Class="Quest:Objective"><Inputs Name="speakWithFather" IsHidden="0"/></Node>
</Nodes><Edges><Edge nodeOut="2" nodeIn="5" enabled="1"/>
<Edge nodeOut="0" nodeIn="5" enabled="0"/></Edges></Graph>'''


class QuestImportTests(unittest.TestCase):
    def test_runtime_editor_and_disconnected_nodes_are_separate(self):
        data = b'''<BehaviorTrees><BehaviorTree name="onUpdate"><Variables>
<Variable name="phase" type="_int" isPersistent="1" values="0"/></Variables>
<Root><Sequence><UnknownOperation code="keep this"/></Sequence></Root>
<ForestContainer><ExecuteLua code="disconnected"/></ForestContainer>
<EditorData><Root><ExecuteLua code="editor duplicate"/></Root></EditorData>
</BehaviorTree></BehaviorTrees>'''
        tree = parse_behavior(data, 'test')['onUpdate']
        self.assertEqual([n['op'] for n in walk(tree['root'])], ['Root', 'Sequence', 'UnknownOperation'])
        self.assertEqual(tree['disconnected'][0]['attributes']['code'], 'disconnected')
        self.assertEqual(tree['variables'][0]['isPersistent'], '1')

    def test_edges_and_unknown_sentinels_are_retained_not_rewired(self):
        graph = parse_graph(GRAPH, 'q_test')
        self.assertEqual(len(graph['edges']), 2)
        self.assertEqual(graph['unresolved_edges'], [graph['edges'][1]])
        self.assertEqual(graph['edges'][1]['enabled'], '0')
        with self.assertRaises(ValueError): parse_graph(GRAPH, 'different')
        with self.assertRaises(ValueError): parse_graph(GRAPH.replace(b'Id="5"', b'Id="2"'), 'q_test')

    def test_include_paths_cannot_escape_retail_namespace(self):
        self.assertEqual(behavior_path('"final/questUtils.xml"'), 'Libs/AI/final/questUtils.xml')
        for value in ('../file.xml', '/file.xml', 'C:/file.xml', 'file.lua'):
            with self.assertRaises(ValueError): behavior_path(value)

    def test_recursive_and_dynamic_includes_are_preserved_with_provenance(self):
        documents = {
            'Libs/Tables/quest/quest.xml': b'<database><table name="quest"><rows><row quest_id="7" quest_name="q_test"/><row quest_id="9" quest_name="other"/></rows></table></database>',
            'Libs/Tables/quest/quest_objective.xml': b'<database><table name="quest_objective"><rows><row quest_id="7" objective_id="5" objective_name="speakWithFather"/><row quest_id="9" objective_id="5" objective_name="other"/></rows></table></database>',
            'Libs/quests/flowgraphs/q_test.xml': GRAPH,
            'Libs/AI/quests/q_test.xml': b'''<BehaviorTrees><BehaviorTree name="onUpdate"><Root>
<IncludeTree File="final/shared.xml" Name="helper"/>
<IncludeTree File="$filename" Name="$tree"/>
<ExecuteLua code="System.DoThing()"/></Root></BehaviorTree></BehaviorTrees>''',
            'Libs/AI/quests/q_master_main.xml': b'<BehaviorTrees><BehaviorTree name="onUpdate"><Root/></BehaviorTree></BehaviorTrees>',
            'Libs/AI/final/shared.xml': b'''<BehaviorTrees><BehaviorTree name="helper"><Root>
<IncludeTree File="quests/q_test.xml" Name="onUpdate"/>
<IncludeTree File="final/shared.xml" Name="missing"/>
</Root></BehaviorTree></BehaviorTrees>''',
        }
        def resolve(game, requested):
            return {label: dict(entry=entry, data=documents[entry], candidates=[{'archive': pak, 'sha256': 'fixture'}])
                    for label, (pak, entry) in requested.items()}
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp); (game / 'Data').mkdir()
            with zipfile.ZipFile(game / 'Data/Tables.pak', 'w') as archive:
                for entry, data in documents.items():
                    if entry.startswith('Libs/Tables/'): archive.writestr(entry, data)
            with patch('quest_import.retail_sources', side_effect=resolve):
                model, raw = import_quest(game, 'q_test')
                documents['Libs/Tables/quest/quest_objective.xml'] = documents['Libs/Tables/quest/quest_objective.xml'].replace(b'speakWithFather', b'wrong')
                with self.assertRaisesRegex(ValueError, 'objectives disagree'): import_quest(game, 'q_test')
        self.assertEqual(len(raw), 5)
        self.assertEqual(model['quest_id'], '7')
        self.assertEqual(len(model['tables']['quest_objective']['rows']), 1)
        self.assertEqual(len(model['unresolved_includes']), 2)
        self.assertTrue(model['unresolved_includes'][0]['dynamic'])
        self.assertEqual(model['lua_sites'][0]['code'], 'System.DoThing()')
        self.assertFalse(model['executable'])
        self.assertEqual(model['behavior_documents']['libs/ai/final/shared.xml']['provenance'][0]['sha256'], 'fixture')

    def test_ambiguous_behavior_names_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_behavior(b'<BehaviorTrees><BehaviorTree name="x"><Root/></BehaviorTree><BehaviorTree name="x"><Root/></BehaviorTree></BehaviorTrees>', 'test')

    def test_null_asset_requires_exact_database_evidence(self):
        data = b'''<Graph><Nodes><Node Id="2" Class="Quest:Begin"><Inputs Name="q_test"/></Node>
<Node Id="9" Class="Quest:AssetPlace"/><Node Id="4" Class="Quest:Objective"/>
<Node Id="7" Class="Quest:AssetTracked"><Inputs MapAsset="9" TrackCnt="1" User="0" OnMap="1"/></Node>
</Nodes><Edges><Edge nodeOut="0" nodeIn="7" portOut="Out" portIn="Asset" enabled="1"/>
<Edge nodeOut="7" nodeIn="4" portOut="Objective" portIn="Tracked" enabled="1"/></Edges></Graph>'''
        row = dict(objective_id='4', quest_asset_id='', map_asset_id='9', count='1', user_id='0', on_map='True')
        graph = parse_graph(data, 'q_test')
        resolve_null_tracking_edges(graph, {'quest_tracked_asset': {'rows': [row]}})
        self.assertFalse(graph['unresolved_edges'])
        self.assertEqual(graph['edges'][0]['nodeOut'], '0')
        self.assertEqual(len(graph['null_tracking_edges']), 1)
        graph = parse_graph(data, 'q_test'); row['quest_asset_id'] = '9'
        resolve_null_tracking_edges(graph, {'quest_tracked_asset': {'rows': [row]}})
        self.assertEqual(len(graph['unresolved_edges']), 1)


if __name__ == '__main__': unittest.main()
