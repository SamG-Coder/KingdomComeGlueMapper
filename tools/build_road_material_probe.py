"""Apply KCD1 decal shading compatibility to an isolated copy of a surface build."""
import argparse
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

from compiled_terrain import parse
from static_assets import preserve_legacy_decal_shadows
from upgrade_map import xml


def write_level_copy(base, destination, terrain_data):
    """Clone a level and replace only its terrain package payload and identity."""
    destination.mkdir(parents=True)
    for file in base.iterdir():
        if file.is_file() and file.name not in ('terrain.pak', 'level.pak', 'levelinfo.xml'):
            shutil.copy2(file, destination/file.name)
    for pak in ('terrain.pak', 'level.pak'):
        with zipfile.ZipFile(base/pak) as src, zipfile.ZipFile(destination/pak, 'x', zipfile.ZIP_STORED) as dst:
            for name in src.namelist():
                payload = src.read(name)
                if pak == 'terrain.pak' and name == 'terrain/terrain.dat':
                    payload = terrain_data
                elif pak == 'level.pak' and name in ('levelinfo.xml', 'leveldata.xml'):
                    doc = ET.fromstring(payload)
                    if name == 'levelinfo.xml':
                        doc.set('Name', 'data/levels/'+destination.name)
                    else:
                        doc.find('LevelInfo').set('Name', destination.name)
                    payload = xml(doc)
                    if name == 'levelinfo.xml':
                        (destination/name).write_bytes(payload)
                dst.writestr(name, payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, default=Path('D:/SteamLibrary/steamapps/common/KCD2Mod'))
    parser.add_argument('--base-level', default='kcd1_world_v37')
    parser.add_argument('--level', default='kcd1_world_v38')
    args = parser.parse_args()
    if not all(re.fullmatch(r'[A-Za-z0-9_]+', n) for n in (args.base_level, args.level)):
        parser.error('Invalid level name')
    root = args.tools/'Data'
    base, destination = (root/'Levels'/n for n in (args.base_level, args.level))
    prefix = 'gluelandscape/'+args.level+'/'
    if destination.exists() or (root/prefix).exists():
        parser.error('Output exists; choose a new level name')
    with zipfile.ZipFile(base/'terrain.pak') as archive:
        terrain = parse(archive.read('terrain/terrain.dat'))
    output = bytearray(terrain.data)
    changes = []
    materials = {}
    for number, name in enumerate(terrain.tables['materials']['paths']):
        # Only imported surface materials; native assets and brush materials
        # belong to separate build stages and are not modified in this probe.
        if not name.startswith('gluelandscape/'):
            continue
        doc = ET.fromstring((root/(name+'.mtl')).read_bytes())
        changed = False
        for material in doc.iter('Material'):
            changed |= preserve_legacy_decal_shadows(material, set(re.findall(r'%\w+', material.get('StringGenMask', ''))))
        if not changed:
            continue
        target = prefix+f'm{number}'
        encoded = target.encode()
        if len(encoded) >= 256:
            raise ValueError('Material path too long')
        start = terrain.tables['materials']['offset']+number*256
        output[start:start+256] = encoded.ljust(256, b'\0')
        materials[target+'.mtl'] = xml(doc)
        changes.append(dict(source=name, target=target))
    if not changes:
        raise ValueError('No imported parallax decal materials found')
    parse(bytes(output))
    write_level_copy(base, destination, output)
    for name, payload in materials.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    report = dict(level=args.level, base_level=args.base_level, changes=changes,
                  behavior='Preserve KCD1 disabled decal parallax self-shadowing; retain displacement and textures',
                  visual_status='Pending runtime verification')
    (destination/'road-material-compatibility.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(dict(level=args.level, changed_materials=len(changes)), indent=2))


if __name__ == '__main__':
    main()
