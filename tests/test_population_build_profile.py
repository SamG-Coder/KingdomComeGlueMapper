import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from current_build import build, paths
from population_build_profile import assemble, validate, LEVEL, SCHEDULER


def write_pak(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in entries.items(): z.writestr(name, data)


class PopulationBuildProfileTests(unittest.TestCase):
    def prepare(self, root):
        base, current = root / 'base', root / 'current'
        actors = ''.join(f'<Entity Name="service{i}" EntityClass="NPC" EntityGuid="{i}"/>' for i in range(3))
        world = {
            'objects_mission0.xml': '<Objects>' + actors + '</Objects>',
            'whdata_0': '<World><SoulList><Souls><Soul>player</Soul><Soul>services</Soul></Souls></SoulList></World>',
            'tables/ai/scheduler.xml': '<Scheduler>original services and horse</Scheduler>',
            'triggerareas.fubar': 'original areas', 'waitinglinks.xml': '<Links/>',
            'leveldata.xml': '<Level><LevelInfo Name="kcd1_travel"/><Layers/><Settings Value="old"/></Level>',
            'terrain/hlods.dat': 'old scenery',
        }
        write_pak(base / LEVEL, world)
        world = dict(world)
        world.update({
            'objects_mission0.xml': '<Objects>' + actors + '<Entity Name="extra" EntityGuid="4" EntityClass="NPC_Female"/></Objects>',
            'whdata_0': '<World>extra population souls</World>',
            'tables/ai/scheduler.xml': '<Scheduler>extra population</Scheduler>',
            'triggerareas.fubar': 'extra population areas', 'waitinglinks.xml': '<Links>extra population</Links>',
            'leveldata.xml': '<Level><LevelInfo Name="kcd1_travel"/><Layers><Layer Name="population"/></Layers><Settings Value="current"/></Level>',
            'terrain/hlods.dat': 'current scenery', 'tables/weatherprofiles.xml': '<Weather>current weather</Weather>',
            'layers/population.xml': '<Objects><Entity Name="conditional" EntityGuid="5" EntityClass="NPC"/></Objects>',
            'extractedlayerentityids.xml': '<ReservedEntityIDsFromLayers/>', 'whdata_1': '<PopulationProfiles/>',
        })
        write_pak(current / LEVEL, world)
        main = {
            'Libs/Tables/rpg/soul__gluemappertravel.xml': '<Souls>original services</Souls>',
            'Libs/Storm/storm.xml': '<Storm>original services</Storm>',
            SCHEDULER: '<Scheduler>original services and horse</Scheduler>',
            'Scripts/Entities/Actor/Player.lua': 'old gameplay code',
            'objects/characters/service.mtl': '<Material Name="service"/>',
        }
        write_pak(base / 'Data/gluemappertravel.pak', main)
        main.update({
            'Libs/Tables/rpg/soul__gluemappertravel.xml': '<Souls>original plus extras</Souls>',
            'Libs/Storm/storm.xml': '<Storm>original plus extras</Storm>',
            SCHEDULER: '<Scheduler>extra population</Scheduler>',
            'Scripts/Entities/Actor/Player.lua': 'current horse and quest code',
            'Libs/Tables/ai/brain__gluemappertravel.xml': '<Brains>population only</Brains>',
        })
        write_pak(current / 'Data/gluemappertravel.pak', main)
        write_pak(current / 'Data/gluemappertravel_population_000.pak', {'objects/characters/gluepopulation/extra.dds': 'extra asset'})
        (base / 'mod.manifest').write_text('base')
        (current / 'mod.manifest').write_text('current')
        (current / 'Data/Levels/kcd1_travel/terrain.pak').write_bytes(b'current terrain pack')
        (current / 'shared-materials.json').write_text('{}')
        return base, current

    def test_profile_removes_registrations_and_assets_but_preserves_gameplay_and_scenery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); base, current = self.prepare(root)
            before = {p: p.read_bytes() for folder in (base, current) for p in folder.rglob('*') if p.is_file()}
            result = assemble(base, current, root / 'out', target=root / 'game')
            self.assertEqual(result['disabled_placements'], 2)
            self.assertEqual(validate(root / 'out', base)['enabled_npc_count'], 3)
            self.assertEqual(result['placements'], [])
            self.assertFalse((root / 'out/Data/gluemappertravel_population_000.pak').exists())
            self.assertFalse((root / 'out/shared-materials.json').exists())
            with zipfile.ZipFile(root / 'out/Data/gluemappertravel.pak') as z:
                self.assertEqual(z.read('Scripts/Entities/Actor/Player.lua'), b'current horse and quest code')
            with zipfile.ZipFile(root / 'out' / LEVEL) as z:
                self.assertEqual(z.read('terrain/hlods.dat'), b'current scenery')
                self.assertIn(b'Value="current"', z.read('leveldata.xml'))
                self.assertEqual(z.read('tables/weatherprofiles.xml'), b'<Weather>current weather</Weather>')
                self.assertIn(b'player', z.read('whdata_0'))
            self.assertEqual((root / 'out/Data/Levels/kcd1_travel/terrain.pak').read_bytes(), b'current terrain pack')
            self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))

    def test_stale_population_asset_shard_cannot_pass_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); base, current = self.prepare(root)
            assemble(base, current, root / 'out', target=root / 'game')
            shutil.copy2(current / 'Data/gluemappertravel_population_000.pak', root / 'out/Data')
            with self.assertRaisesRegex(ValueError, 'extra or missing population resources'):
                validate(root / 'out', base)

    def test_persistent_soul_or_scheduler_cannot_survive_with_hidden_entities(self):
        for entry in ('whdata_0', 'tables/ai/scheduler.xml'):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); base, current = self.prepare(root)
                assemble(base, current, root / 'out', target=root / 'game')
                level = root / 'out' / LEVEL
                with zipfile.ZipFile(level) as z: entries = {n: z.read(n) for n in z.namelist()}
                entries[entry] = b'<ExtraPopulation/>'
                write_pak(level, entries)
                with self.assertRaisesRegex(ValueError, 'changed world registration'):
                    validate(root / 'out', base)

    def test_build_setting_skips_population_conversion_and_preserves_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); base, current = self.prepare(root)
            layout = paths(root / 'outputs')
            shutil.copytree(base, layout['cache'] / 'TravelBase/gluemappertravel')
            shutil.copytree(current, layout['current'] / 'Mods/gluemappertravel')
            world = layout['current'] / 'Mods/kingdomcomegluemapper'
            world.mkdir(); (world / 'scenery.pak').write_bytes(b'current world')
            population = layout['cache'] / 'Population'; population.mkdir()
            (population / 'checkpoint').write_bytes(b'keep converted NPCs')
            config = dict(population_profile='services-only', target=str(root / 'game'))
            with patch('character_population.stage') as stage, patch('current_build.publish', return_value='checked'):
                self.assertEqual(build(layout, config), 'checked')
                stage.assert_not_called()
            self.assertEqual((population / 'checkpoint').read_bytes(), b'keep converted NPCs')
            self.assertEqual((layout['next'] / 'Mods/kingdomcomegluemapper/scenery.pak').read_bytes(), b'current world')
            report = json.loads((layout['next'] / 'Mods/gluemappertravel/population-package.json').read_text())
            self.assertEqual(report['population_profile'], 'services-only')


if __name__ == '__main__': unittest.main()
