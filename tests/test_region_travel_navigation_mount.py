import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from region_travel_navigation import mounted_navigation


class NavigationMountTests(unittest.TestCase):
    def test_declared_ai_path_resolves_identical_navigation_in_root_pak(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            entries = {'ubernav.tmm': b'graph', 'areasmission0.bai': b'areas'}
            nav = {'recast/rdnavmesh_1.nav': b'mesh',
                   'recast/rdnavmesh_1.hdr': b'index',
                   'recast/groupcombinations_town.xml': b'states'}
            for archive, files in [('level.pak', entries), ('recast.pak', nav)]:
                with zipfile.ZipFile(folder / archive, 'w') as z:
                    for name, blob in files.items(): z.writestr(name, blob)
            packed = dict(mounted_navigation(folder, 'data/levels/custom_world'))
            # The root PAK mounts beneath Data, unlike the mod level PAK.
            mounted = {'data/' + name: blob for name, blob in packed.items()}
            for name, blob in {**entries, **nav}.items():
                self.assertEqual(mounted['data/levels/custom_world/' + name], blob)
            self.assertEqual(len(packed), 5)

    def test_rejects_unsafe_or_non_data_paths(self):
        for path in ('mods/foo', 'data/../native', 'data/levels//world', 'C:/foo'):
            with self.assertRaises(ValueError):
                list(mounted_navigation('.', path))


if __name__ == '__main__': unittest.main()
