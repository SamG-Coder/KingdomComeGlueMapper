import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from game_paths import GameLibrary
from steam_installations import discover, read_vdf
from desktop_setup import Cancelled, conversion_plan, install_transaction, preflight
from setup_campaign import MOD_ID, RECEIPT, verify_package


class DesktopSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_steam_finds_separate_libraries_and_ignores_missing_games(self):
        steam, second = self.root / 'steam', self.root / 'second'
        for path in (steam, second): (path / 'steamapps/common').mkdir(parents=True)
        escaped = str(second).replace('\\', '\\\\')
        (steam / 'steamapps/libraryfolders.vdf').write_text(f'// comment\n"libraryfolders" {{ "0" {{ "path" "{escaped}" "empty" "" }} }}')
        game = second / 'steamapps/common/My Game'; game.mkdir()
        (second / 'steamapps/appmanifest_379430.acf').write_text('"AppState" { "appid" "379430" "installdir" "My Game" }')
        found, warnings = discover([steam])
        self.assertEqual(found['kcd1'], [str(game.resolve())]); self.assertEqual(found['kcd2'], [])
        self.assertEqual(warnings, [])
        self.assertEqual(read_vdf('"x" "" "y" "C:\\\\Games"'), {'x': '', 'y': 'C:\\Games'})

    def test_game_paths_route_writes_away_from_both_installs(self):
        config = self.root / 'paths.json'
        config.write_text(json.dumps(dict(schema=1, kcd1=str(self.root / 'a'), kcd2=str(self.root / 'b'), build=str(self.root / 'build'))))
        library = GameLibrary(config)
        self.assertEqual(library / 'KCD2Mod/Data/Levels', (self.root / 'build/Data/Levels').resolve())
        self.assertEqual(library / 'KingdomComeDeliverance' / 'Data', (self.root / 'a/Data').resolve())
        with self.assertRaises(ValueError): _ = library / 'KCD2Mod/../a'
        config.write_text(json.dumps(dict(schema=1, kcd1=str(self.root / 'a'), kcd2=str(self.root / 'b'), build=str(self.root / 'b/build'))))
        with self.assertRaises(ValueError): GameLibrary(config)

    def test_conversion_plan_has_no_external_editor_or_npc_dependency(self):
        plan = conversion_plan(self.root / 'paths.json', self.root / 'build')
        self.assertEqual(len(plan), 12)
        self.assertIn('--merged', plan[1][2]); self.assertIn('--character-visuals', plan[-2][2])
        self.assertEqual(plan[-1][1], 'repair_tree_materials')
        self.assertTrue(all('--library' not in args or str(self.root / 'paths.json') in args for _, _, args in plan))

    def package(self, path, marker):
        files = {'mod.manifest': f'<kcd_mod><info><modid>{MOD_ID}</modid></info></kcd_mod>'.encode(),
                 'campaign-audit.json': b'{}', 'campaign.json': b'{"world":{"asset_archives":[]}}',
                 f'Data/{MOD_ID}.pak': marker, f'Data/{MOD_ID}_sources.pak': b'sources',
                 'Data/Levels/kcd1_rataje/level.pak': b'level', 'Data/Levels/kcd1_rataje/terrain.pak': b'terrain',
                 'Data/Levels/kcd1_rataje/levelinfo.xml': b'<LevelInfo/>'}
        for name, data in files.items():
            dest = path / name; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
        (path / RECEIPT).write_text(json.dumps(dict(schema=1, owner=MOD_ID, kind='retail-campaign-package', target_version='1.5.6',
                                                files={n: hashlib.sha256(d).hexdigest() for n, d in files.items()})))
        return path

    def target(self):
        target = self.root / 'target'; target.mkdir()
        (target / 'system.cfg').write_text('wh_sys_version="1.5.6"')
        return target

    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=100 * 1024 ** 3)
    def test_install_backs_up_owned_mod_and_preserves_others(self, *_):
        target = self.target()
        old = self.package(target / 'Mods' / MOD_ID, b'old')
        other = target / 'Mods/other_mod'; other.mkdir(); (other / 'keep').write_text('untouched')
        source = self.package(self.root / 'package', b'new')
        result = install_transaction(source, target)
        self.assertEqual((Path(result['backup']) / f'Data/{MOD_ID}.pak').read_bytes(), b'old')
        self.assertEqual((old / f'Data/{MOD_ID}.pak').read_bytes(), b'new')
        self.assertEqual((other / 'keep').read_text(), 'untouched'); verify_package(old)

    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=100 * 1024 ** 3)
    def test_failed_swap_restores_old_install(self, *_):
        target = self.target(); dest = self.package(target / 'Mods' / MOD_ID, b'old')
        source = self.package(self.root / 'package', b'new')
        original = Path.rename
        def fail_stage(path, to):
            if path.parent.name.startswith('.gluemapper-install-'): raise OSError('simulated swap failure')
            return original(path, to)
        with patch.object(Path, 'rename', fail_stage):
            with self.assertRaisesRegex(OSError, 'simulated'): install_transaction(source, target)
        self.assertEqual((dest / f'Data/{MOD_ID}.pak').read_bytes(), b'old'); verify_package(dest)

    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=100 * 1024 ** 3)
    def test_cancel_and_tampering_leave_old_install_intact(self, *_):
        target = self.target(); dest = self.package(target / 'Mods' / MOD_ID, b'old')
        source = self.package(self.root / 'package', b'new')
        with self.assertRaises(Cancelled): install_transaction(source, target, cancelled=lambda: True)
        (source / f'Data/{MOD_ID}.pak').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'changed'): install_transaction(source, target)
        self.assertEqual((dest / f'Data/{MOD_ID}.pak').read_bytes(), b'old')

    @patch('desktop_setup.validate_games', return_value='1.5.6')
    @patch('desktop_setup.game_running', return_value=False)
    @patch('desktop_setup.free_space', return_value=1024 ** 3)
    def test_low_space_is_reported_before_build(self, *_):
        config = dict(kcd1=str(self.root / 'a'), kcd2=str(self.root / 'b'), workspace=str(self.root / 'work'), action='build_only')
        with self.assertRaisesRegex(ValueError, '60.0 GiB'): preflight(config)


if __name__ == '__main__': unittest.main()
