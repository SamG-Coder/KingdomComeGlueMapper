import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_sources import OPENING_FILES, audit_opening, opening_sources
from setup_campaign import MOD_ID, RECEIPT, build_probe, install, verify_package, uninstall


class CampaignSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / 'old', self.root / 'new'
        for root in (self.source, self.target):
            (root / 'Data').mkdir(parents=True)
        grouped = {}
        for label, (pak, entry) in OPENING_FILES.items():
            grouped.setdefault(pak, {})[entry] = (
                b'<Graph><Nodes><Node Id="1" Class="Quest:Begin"/></Nodes><Edges/></Graph>'
                if label == 'quest_graph' else b'<BehaviorTrees/>')
        for pak, files in grouped.items():
            self.pak(self.source / 'Data' / pak, files)
        for root, relative in [(self.source, 'Levels/rataje/level.pak'),
                               (self.target, 'Levels/trosecko/level.pak'),
                               (self.target, 'Scripts.pak'), (self.target, 'IPL_GameData.pak')]:
            self.pak(root / 'Data' / relative, {})
        (self.target / 'system.cfg').write_text('wh_sys_version = "1.5.2"')

    def pak(self, path, files):
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as z:
            for name, data in files.items():
                z.writestr(name, data)

    def build(self):
        output = self.root / 'package'
        build_probe(self.source, self.target, output)
        return output

    def test_retail_only_package_and_install(self):
        output = self.build()
        with zipfile.ZipFile(output / 'Data' / (MOD_ID + '.pak')) as z:
            self.assertEqual(z.namelist(), ['Scripts/Mods/' + MOD_ID + '.lua'])
            self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist()))
        self.assertFalse(verify_package(output)['ready_to_play'])
        destination = install(output, self.target)
        self.assertEqual(verify_package(destination), verify_package(output))
        with self.assertRaises(ValueError):
            install(output, self.target)

    def test_patch_revision_beats_prefix_sort(self):
        entry = OPENING_FILES['master'][1]
        self.pak(self.source / 'Data/patch/patch_010300.pak', {entry: b'<Old/>'})
        self.pak(self.source / 'Data/patch/ipl_patch_010900.pak', {entry: b'<New/>'})
        self.assertEqual(opening_sources(self.source)['master']['data'], b'<New/>')

    def test_ambiguous_patch_is_rejected(self):
        entry = OPENING_FILES['master'][1]
        self.pak(self.source / 'Data/patch/patch_010900.pak', {entry: b'<One/>'})
        self.pak(self.source / 'Data/patch/ipl_patch_010900.pak', {entry: b'<Two/>'})
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            opening_sources(self.source)

    def test_tampering_cannot_be_installed(self):
        output = self.build()
        (output / 'mod.manifest').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            install(output, self.target)
        self.assertFalse((self.target / 'Mods').exists())

    def test_path_escape_rejected(self):
        output = self.build()
        external = self.root / 'outside'
        external.write_bytes(b'data')
        receipt = json.loads((output / RECEIPT).read_text())
        receipt['files']['../outside'] = hashlib.sha256(b'data').hexdigest()
        (output / RECEIPT).write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'Invalid package path'):
            verify_package(output)

    def test_existing_mod_order_is_preserved(self):
        output = self.build()
        (self.target / 'Mods').mkdir()
        order = self.target / 'Mods/mod_order.txt'
        order.write_text('some_other_mod\n')
        with self.assertRaisesRegex(ValueError, 'excludes'):
            install(output, self.target)
        self.assertEqual(order.read_text(), 'some_other_mod\n')

    def test_version_mismatch_rejected(self):
        output = self.build()
        (self.target / 'system.cfg').write_text('wh_sys_version = "1.6"')
        with self.assertRaisesRegex(ValueError, 'version differs'):
            install(output, self.target)

    def test_uninstall_preserves_package_and_other_mods(self):
        output = self.build()
        destination = install(output, self.target)
        other = self.target / 'Mods/other_mod'
        other.mkdir()
        (other / 'important').write_text('keep')
        backup = self.root / 'backup'
        uninstall(self.target, backup)
        self.assertFalse(destination.exists())
        self.assertEqual(verify_package(backup), verify_package(output))
        self.assertEqual((other / 'important').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
