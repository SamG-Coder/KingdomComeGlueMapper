"""Repackage a population checkpoint without repeating character conversion."""
import json
from pathlib import Path
import shutil
import zipfile

from current_build import paths, working_environment, publish
from retail_pak import RetailPakWriter, PakSet
from region_travel_policy import update_resource, WEATHER_TABLE, WEATHER_ALIAS
from travel_world_package import prepare_world_for_travel
from upgrade_map import read
from character_shared_materials import SharedMaterials
from current_build import write_json


def repack(layout, config):
    current, candidate = layout['current'], layout['next']
    if candidate.exists(): raise FileExistsError('Inspect pending build: ' + str(candidate))
    with zipfile.ZipFile(Path(config['target']) / 'Data/Levels/trosecko/level.pak') as native:
        weather = read(native, WEATHER_TABLE)
    source_travel = current / 'Mods/gluemappertravel'
    travel = candidate / 'Mods/gluemappertravel'
    main_names = {p.relative_to(source_travel).as_posix() for p in (source_travel / 'Data').glob('*.pak')}
    rewritten = main_names | {'Data/Levels/kcd1_travel/level.pak', 'Localization/English_xml.pak'}
    for source in current.rglob('*'):
        if not source.is_file() or source == current / 'build.json': continue
        if source.is_relative_to(source_travel) and source.relative_to(source_travel).as_posix() in rewritten:
            continue
        destination = candidate / source.relative_to(current)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    prepare_world_for_travel(candidate / 'Mods/kingdomcomegluemapper')
    (travel / 'Data').mkdir(parents=True, exist_ok=True)
    with PakSet(source_travel / 'Data') as source, RetailPakWriter(travel / 'Data', 'gluemappertravel') as out:
        materials = SharedMaterials()
        for name in source.namelist():
            if name.lower().endswith('.mtl'): materials.add(name, source.read(name))
        print(json.dumps(dict(stage='shared_material_plan', **materials.report())), flush=True)
        for index, name in enumerate(source.namelist(), 1):
            payload = materials.rewrite(name, update_resource(name, source.read(name), weather))
            if payload is not None: out.writestr(name, payload)
            if index % 5000 == 0:
                print(json.dumps(dict(stage='repack', entries=index, archives=len(out.paths))), flush=True)
        if not any(n.lower() == WEATHER_ALIAS.lower() for n in source.namelist()):
            out.writestr(WEATHER_ALIAS, weather)
        for name, payload in materials.materials.items(): out.writestr(name, payload)
    write_json(travel / 'shared-materials.json', materials.report())
    for relative in ('Data/Levels/kcd1_travel/level.pak', 'Localization/English_xml.pak'):
        destination = travel / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source_travel / relative) as src, zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, allowZip64=False, compresslevel=1) as dst:
            for name in src.namelist():
                data = weather if name.lower() == WEATHER_TABLE else update_resource(name, src.read(name))
                dst.writestr(name, data)
    return publish(layout, config)


if __name__ == '__main__':
    layout = paths(Path(__file__).resolve().parents[1] / 'outputs')
    config = json.loads((layout['cache'] / 'build-inputs.json').read_text())
    with working_environment(layout):
        receipt = repack(layout, config)
    print(json.dumps(receipt['validation'], indent=2))
