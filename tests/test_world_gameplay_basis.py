import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from world_gameplay_basis import apply, NativeGraphs
from region_travel import guid


class WorldGameplayBasisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.script_path = root / 'scripts.pak'
        self.native_path = root / 'level.pak'
        self.native_files = {
            'quests/final/barbora.xml': b'<Database><Skald><Project Name="Barbora"><Assets><SoulAsset Name="player" SharedSoulGuids="player-soul"/><SoulAsset Name="player_any" SharedSoulGuids="player-group"/></Assets></Project></Skald></Database>',
            'quests/final/barbora/open_world.xml': b'<Database><Skald><Gameplay Name="open_world"><Definitions><Definition File="open_world/combat.xml"/></Definitions><Nodes><combat Name="combat"/></Nodes><Assets><SmartObjectAsset Name="openworld"/></Assets></Gameplay></Skald></Database>',
            'quests/final/barbora/open_world/combat.xml': b'<Database><Skald><Gameplay Name="combat"><Assets><DialogueHolderAsset Name="surrenderDH"/></Assets><Nodes><LinkEffect Name="bind"><Asset Name="From" Alias="openworld"/><Asset Name="To" Alias="player_any"/></LinkEffect></Nodes></Gameplay></Skald></Database>'}
        self.write_scripts()
        with zipfile.ZipFile(self.native_path, 'w') as z:
            z.writestr('objects_mission0.xml', '<Objects><Entity Name="open_world" EntityClass="SmartObjectHolder" Pos="999,999,999"><EntityLinks><Link TargetId="999" Name="regional_service"/></EntityLinks></Entity><Entity Name="surrenderHolder" EntityClass="DialogueHolder" Pos="888,888,888"/></Objects>')
        self.files = {'objects_mission0.xml': b'<Objects><Entity Name="test_level" EntityClass="LevelHolder" EntityId="1" EntityGuid="00000001-0000-0000" Pos="10,20,30"/><Entity Name="sa_land" EntityClass="SmartAreaShape" EntityId="2" EntityGuid="00000002-0000-0000" Pos="0,0,-50"/></Objects>',
                      'waitinglinks.xml': b'<StaticLinksInfo version="1"><WaitingLinks/></StaticLinksInfo>', 'quest_state': b'preserve'}
        self.graphs = {'Quests/GlueTravel/entry/test_level.xml': b'<Database><Skald><Level Name="test_level"><Nodes><State Name="existing_quest"/></Nodes></Level></Skald></Database>', 'unrelated.xml': b'preserve'}

    def write_scripts(self):
        with zipfile.ZipFile(self.script_path, 'w') as z:
            for path, data in self.native_files.items(): z.writestr(path, data)

    def run_apply(self):
        with zipfile.ZipFile(self.script_path) as scripts, zipfile.ZipFile(self.native_path) as native, patch('world_gameplay_basis.SYSTEMS', ('combat',)):
            return apply(self.files, self.graphs, 'Quests/GlueTravel/project.xml', 'test_level', native, scripts, guid)

    def test_destination_binding_and_native_dependency_preservation(self):
        files, graphs, report = self.run_apply()
        self.assertEqual(files['quest_state'], b'preserve')
        self.assertEqual(graphs['unrelated.xml'], b'preserve')
        self.assertFalse(report['runtime_verified'])
        self.assertFalse(report['full_trosky_parity'])
        self.assertEqual(len(report['native_dependencies']), 1)
        self.assertFalse(any(p.lower().startswith('quests/final/') for p in graphs))
        mission = ET.fromstring(files['objects_mission0.xml'])
        for name in ('open_world', 'surrenderHolder'):
            entity = mission.find("Entity[@Name='" + name + "']")
            self.assertEqual(entity.get('Pos'), '10,20,30')
            self.assertNotIn('regional_service', ET.tostring(entity).decode())
        links = ET.fromstring(files['waitinglinks.xml'])
        self.assertEqual({n.text for n in links.iter('LinkDefinition')}, {'module', "asset['openworld']", "asset['surrenderDH']", 'punishment_openworldQSO'})
        entry = ET.fromstring(graphs['Quests/GlueTravel/entry/test_level.xml'])
        self.assertIsNotNone(entry.find("Skald/Level/Nodes/State[@Name='existing_quest']"))

    def test_missing_native_asset_fails_before_modifying_input(self):
        key = 'quests/final/barbora/open_world/combat.xml'
        self.native_files[key] = self.native_files[key].replace(b'Alias="openworld"', b'Alias="regional_guard"')
        self.write_scripts()
        with self.assertRaisesRegex(ValueError, 'regional_guard'): self.run_apply()
        self.assertNotIn(b'surrenderHolder', self.files['objects_mission0.xml'])

    def test_archive_dependencies_resolve_without_parent_path_normalization(self):
        # Native includes may climb directories. Python normpath used to hide
        # the bad paths emitted to the engine's literal archive lookup.
        key = 'quests/final/barbora/open_world/combat.xml'
        self.native_files[key] = self.native_files[key].replace(b'<Assets>', b'<Definitions><Definition File="../helper.xml"/></Definitions><Assets>')
        self.native_files['quests/final/barbora/helper.xml'] = b'<Database><Skald><Gameplay Name="helper"><Nodes/></Gameplay></Skald></Database>'
        self.write_scripts()
        _, graphs, _ = self.run_apply()
        visited = set()
        def visit(path):
            if path in visited: return
            visited.add(path)
            root = ET.fromstring(graphs[path])
            for definition in root.findall('.//Definitions/Definition'):
                filename = definition.get('File')
                self.assertNotIn('..', filename.split('/'))
                # Deliberately no normpath: match the failing runtime lookup.
                child = path.rsplit('/', 1)[0] + '/' + filename
                self.assertIn(child, graphs)
                visit(child)
        visit('Quests/GlueTravel/entry/test_level.xml')
        self.assertEqual(len(visited), 4)

    def test_inherited_namespace_dependencies_are_packaged_transitively(self):
        key = 'quests/final/barbora/open_world/combat.xml'
        self.native_files[key] = self.native_files[key].replace(b'<Nodes>', b'<Nodes><istired Name="tired" Namespace="utils.combat"/>')
        self.native_files['quests/final/barbora/utils/combat.xml'] = b'<Database><Skald><Library Name="combat"><Definitions><Definition File="combat/istired.xml"/></Definitions><Nodes><istired Name="istired" InstanceType="Static"/></Nodes></Library></Skald></Database>'
        self.native_files['quests/final/barbora/utils/combat/istired.xml'] = b'<Database><Skald><Module Name="istired"><Nodes><clock Name="clock" Namespace="utils.time"/></Nodes></Module></Skald></Database>'
        self.native_files['quests/final/barbora/utils/time.xml'] = b'<Database><Skald><Library Name="time"><Definitions><Definition File="time/clock.xml"/></Definitions><Nodes><clock Name="clock" InstanceType="Static"/></Nodes></Library></Skald></Database>'
        self.native_files['quests/final/barbora/utils/time/clock.xml'] = b'<Database><Skald><Module Name="clock"><Nodes/></Module></Skald></Database>'
        self.write_scripts()
        _, graphs, _ = self.run_apply()
        base = 'Quests/GlueTravel/entry/world/'
        wrapper = ET.fromstring(graphs[base+'open_world.xml'])
        self.assertIsNotNone(wrapper.find(".//Nodes/utils[@Name='utils']"))
        utility = ET.fromstring(graphs[base+'native/utils.xml'])
        self.assertEqual({n.tag for n in utility.findall('.//Nodes/*')}, {'combat', 'time'})
        library = ET.fromstring(graphs[base+'native/utils_combat.xml'])
        self.assertEqual(library.find('.//Nodes/istired').get('InstanceType'), 'Static')
        from world_gameplay_basis import package_dependencies
        entry = 'Quests/GlueTravel/entry/test_level.xml'
        with zipfile.ZipFile(self.script_path) as scripts:
            migrated = package_dependencies(graphs, entry, base+'open_world.xml', scripts)
        self.assertEqual(graphs, migrated)
        # The same dependency can use a namespace relative to the utils parent.
        key = 'quests/final/barbora/utils/combat/istired.xml'
        self.native_files[key] = self.native_files[key].replace(b'Namespace="utils.time"', b'Namespace="time"')
        self.write_scripts()
        _, relative, _ = self.run_apply()
        self.assertIn(base+'native/utils_time.xml', relative)

    def test_campaign_input_requires_adapter(self):
        key = 'quests/final/barbora/open_world/combat.xml'
        self.native_files[key] = self.native_files[key].replace(b'<Assets>', b'<Ports><Port Name="trosky_story" Direction="In" Type="bool"/></Ports><Assets>')
        self.write_scripts()
        with self.assertRaisesRegex(ValueError, 'trosky_story'): self.run_apply()

    def test_nested_libraries_keep_parent_helpers_and_relative_namespaces(self):
        key = 'quests/final/barbora/open_world/combat.xml'
        self.native_files[key] = self.native_files[key].replace(
            b'<Nodes>', b'<Nodes><arrest Name="arrest" Namespace="utils.law.resolution"/>')
        base = 'quests/final/barbora/utils/'
        self.native_files[base+'law.xml'] = b'<Database><Skald><Library Name="law"><Definitions><Definition File="law/resolution.xml"/><Definition File="law/feedback.xml"/><Definition File="law/unrelated.xml"/></Definitions><Nodes><resolution Name="resolution"/><feedback Name="feedback" InstanceType="Static"/><unrelated Name="unrelated"/></Nodes></Library></Skald></Database>'
        self.native_files[base+'law/resolution.xml'] = b'<Database><Skald><Library Name="resolution" VoiceNamespace="true"><Definitions><Definition File="resolution/arrest.xml"/></Definitions><Nodes><arrest Name="arrest" InstanceType="Static"/></Nodes></Library></Skald></Database>'
        self.native_files[base+'law/resolution/arrest.xml'] = b'<Database><Skald><Module Name="arrest"><Nodes><feedback Name="f" Namespace=""/><feedback Name="relative" Namespace="law"/></Nodes></Module></Skald></Database>'
        self.native_files[base+'law/feedback.xml'] = b'<Database><Skald><Module Name="feedback"><Nodes/></Module></Skald></Database>'
        self.native_files[base+'law/unrelated.xml'] = b'<Database><Skald><Module Name="unrelated"><Nodes/></Module></Skald></Database>'
        self.write_scripts()
        _, graphs, _ = self.run_apply()
        from world_gameplay_basis import package_dependencies, validate_dependency_scopes
        wrapper = 'Quests/GlueTravel/entry/world/open_world.xml'
        parent = ET.fromstring(graphs['Quests/GlueTravel/entry/world/native/utils_law.xml'])
        self.assertEqual({n.tag for n in parent.findall('.//Nodes/*')}, {'resolution','feedback'})
        nested = ET.fromstring(graphs['Quests/GlueTravel/entry/world/native/utils_law.resolution.xml'])
        self.assertEqual(nested.find('Skald')[0].get('VoiceNamespace'), 'true')
        self.assertEqual(nested.find('.//Nodes/arrest').get('InstanceType'), 'Static')
        definitions = b'<Definitions><Nodes><Definition TypeName="LinkEffect"/></Nodes></Definitions>'
        validate_dependency_scopes(graphs, wrapper, definitions)
        with zipfile.ZipFile(self.script_path) as scripts:
            self.assertEqual(graphs, package_dependencies(graphs, 'Quests/GlueTravel/entry/test_level.xml', wrapper, scripts))

    def test_qualified_library_is_not_hidden_by_partial_local_namespace(self):
        from world_gameplay_basis import validate_dependency_scopes
        root='Quests/world/'
        graphs={root+'root.xml':b'<Database><Skald><Module Name="root"><Definitions><Definition File="global.xml"/><Definition File="child.xml"/></Definitions></Module></Skald></Database>',
                root+'global.xml':b'<Database><Skald><Library Name="utils"><Definitions><Definition File="law.xml"/></Definitions></Library></Skald></Database>',
                root+'law.xml':b'<Database><Skald><Library Name="law"><Definitions><Definition File="helper.xml"/></Definitions></Library></Skald></Database>',
                root+'helper.xml':b'<Database><Skald><Module Name="helper"/></Skald></Database>',
                root+'local.xml':b'<Database><Skald><Library Name="utils"/></Skald></Database>',
                root+'child.xml':b'<Database><Skald><Module Name="child"><Definitions><Definition File="local.xml"/></Definitions><Nodes><helper Name="helper" Namespace="utils.law"/></Nodes></Module></Skald></Database>'}
        self.assertEqual(validate_dependency_scopes(graphs,root+'root.xml',b'<Definitions><Nodes/></Definitions>'),6)

    def test_empty_namespace_inherits_library_and_collects_sibling_chain(self):
        key = 'quests/final/barbora/open_world/combat.xml'
        self.native_files[key] = self.native_files[key].replace(b'<Nodes>', b'<Nodes><getmoney Name="money" Namespace="utils.money"/>')
        base = 'quests/final/barbora/utils/'
        self.native_files[base+'money.xml'] = b'<Database><Skald><Library Name="money"><Definitions><Definition File="money/getmoney.xml"/><Definition File="money/converttodecigroshen.xml"/><Definition File="money/converttogroshen.xml"/></Definitions><Nodes><getmoney Name="getmoney" InstanceType="Static"/><converttodecigroshen Name="converttodecigroshen" InstanceType="Static"/><converttogroshen Name="converttogroshen" InstanceType="Static"/></Nodes></Library></Skald></Database>'
        for name, child in [('getmoney','converttodecigroshen'), ('converttodecigroshen','converttogroshen'), ('converttogroshen','getmoney')]:
            self.native_files[base+'money/'+name+'.xml'] = f'<Database><Skald><Module Name="{name}"><Nodes><{child} Name="helper" Namespace=""/></Nodes></Module></Skald></Database>'.encode()
        self.write_scripts()
        _, graphs, _ = self.run_apply()
        library = ET.fromstring(graphs['Quests/GlueTravel/entry/world/native/utils_money.xml'])
        self.assertEqual({n.tag for n in library.findall('.//Nodes/*')}, {'getmoney','converttodecigroshen','converttogroshen'})
        self.assertEqual(len(library.findall('.//Definitions/Definition')), 3)
        from world_gameplay_basis import validate_dependency_scopes
        builtins = b'<Definitions><Nodes><Definition TypeName="State"/><Definition TypeName="LinkEffect"/></Nodes></Definitions>'
        wrapper = 'Quests/GlueTravel/entry/world/open_world.xml'
        validate_dependency_scopes(graphs, wrapper, builtins)
        libpath = 'Quests/GlueTravel/entry/world/native/utils_money.xml'
        root = ET.fromstring(graphs[libpath])
        section = root.find('.//Definitions')
        section.remove(section[0])
        graphs[libpath] = ET.tostring(root)
        with self.assertRaisesRegex(ValueError, 'Unresolved node type'):
            validate_dependency_scopes(graphs, wrapper, builtins)

    def test_missing_transitive_definition_is_rejected(self):
        key = 'quests/final/barbora/open_world/combat.xml'
        self.native_files[key] = self.native_files[key].replace(b'<Assets>', b'<Definitions><Definition File="missing.xml"/></Definitions><Assets>')
        self.write_scripts()
        with self.assertRaisesRegex(ValueError, 'Missing native gameplay dependency'): self.run_apply()


if __name__ == '__main__': unittest.main()
