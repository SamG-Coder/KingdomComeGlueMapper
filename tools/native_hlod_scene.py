"""Reconstruct native editor placement inputs for a fresh KCD2 HLOD build.

This exports hierarchy inputs, not a replacement playable level. Existing
compiled terrain remains authoritative; no gameplay layers are exported here.
Old HLOD proxies and group membership are deliberately not used as inputs.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import uuid
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from compiled_terrain import parse
from editor_archive import decode, encode
from rebuild_geometry_hlod import extract
from upgrade_map import read


def vector(values):
    return ','.join(format(float(x), '.9g') for x in values)


def decompose(matrix):
    """Row-major 3x4 -> editor position, wxyz quaternion, signed scale.

    Shear cannot be represented by a native brush and must not be discarded.
    """
    m = np.asarray(matrix, dtype=float).reshape(3, 4)
    if not np.isfinite(m).all():
        raise ValueError('Non-finite placement transform')
    scale = np.linalg.norm(m[:, :3], axis=0)
    if np.min(scale) < 1e-8:
        raise ValueError('Singular placement transform')
    if np.linalg.det(m[:, :3]) < 0:
        scale[0] *= -1
    r = m[:, :3] / scale
    if not np.allclose(r.T @ r, np.eye(3), atol=2e-5):
        raise ValueError('Sheared placement requires geometry baking')
    # Largest-component form avoids loss of precision near 180 degrees.
    terms = [1 + np.trace(r), 1 + r[0, 0] - r[1, 1] - r[2, 2],
             1 - r[0, 0] + r[1, 1] - r[2, 2], 1 - r[0, 0] - r[1, 1] + r[2, 2]]
    i = int(np.argmax(terms)); q = np.zeros(4); q[i] = math.sqrt(max(0, terms[i])) / 2
    d = 4 * q[i]
    if i == 0:
        q[1:] = [(r[2, 1]-r[1, 2])/d, (r[0, 2]-r[2, 0])/d, (r[1, 0]-r[0, 1])/d]
    elif i == 1:
        q[[0, 2, 3]] = [(r[2, 1]-r[1, 2])/d, (r[0, 1]+r[1, 0])/d, (r[0, 2]+r[2, 0])/d]
    elif i == 2:
        q[[0, 1, 3]] = [(r[0, 2]-r[2, 0])/d, (r[0, 1]+r[1, 0])/d, (r[1, 2]+r[2, 1])/d]
    else:
        q[[0, 1, 2]] = [(r[1, 0]-r[0, 1])/d, (r[0, 2]+r[2, 0])/d, (r[1, 2]+r[2, 1])/d]
    return m[:, 3], q, scale


def identity(record, occurrence):
    # Asset identity must never be used as placement identity. Two identical
    # instances are also kept separate rather than silently deduplicated.
    digest = hashlib.sha256(record).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'kcd2-placement/{digest}/{occurrence}'))


def brush(record, terrain, occurrence):
    if len(record) != 104 or struct.unpack_from('<I', record)[0] != 1:
        raise ValueError('Expected a KCD2 brush placement')
    mesh, = struct.unpack_from('<H', record, 40)
    material, = struct.unpack_from('<i', record, 96)
    meshes = terrain.tables['meshes']['paths']; materials = terrain.tables['materials']['paths']
    if mesh >= len(meshes) or not -1 <= material < len(materials):
        raise ValueError('Brush dependency outside resource table')
    pos, rotation, scale = decompose(struct.unpack_from('<12f', record, 44))
    guid = identity(record, occurrence)
    result = ET.Element('Object', Id=guid, Name='placed_' + guid, Type='Brush',
                        Prefab=meshes[mesh], Pos=vector(pos), Rotate=vector(rotation), Scale=vector(scale))
    if material >= 0:
        result.set('Material', materials[material])
    return result


def vegetation_instance(record, group_count):
    if len(record) != 64 or struct.unpack_from('<I', record)[0] != 2:
        raise ValueError('Expected a KCD2 vegetation placement')
    group, = struct.unpack_from('<H', record, 40)
    values = struct.unpack_from('<4f', record, 44)
    if group >= group_count or not all(map(math.isfinite, values)) or values[3] <= 0:
        raise ValueError('Invalid vegetation placement')
    # Native editor Version=3 packed instance: pos, scale, int index,
    # brightness, three float angles in Z/X/Y order. Verified with Editor.exe.
    angles = [a * (2 * math.pi / 255) for a in record[61:64]]
    return struct.pack('<4fiB3f', *values, group, record[60], *angles)


def heightmap(terrain, template_blob):
    """Regenerate editor elevation from target terrain, including its extent.

    This is used for vegetation alignment/hierarchy only. The builder must
    preserve the original compiled terrain and merged vegetation when packing.
    """
    from world_surface_coverage import HeightField
    root, _ = decode(template_blob)
    heights = HeightField(terrain).heights[:4096, :4096]
    maximum = max(1024., math.ceil(float(heights.max()) + 1))
    if heights.min() < 0:
        raise ValueError('Negative elevation cannot be represented in editor height words')
    words = np.floor(heights * (65535 / maximum) + .5).astype('<u2')
    root.set('Width', '4096'); root.set('Height', '4096')
    root.set('UnitSize', '1'); root.set('MaxHeight', str(maximum))
    return encode(root, {'HeightmapDataW': words.tobytes(),
                         'HeightmapLayerIdBitmap_ver2': bytes(4096 * 4096),
                         'TerrainCompiledData': terrain.data})


def prepare(level_pak, terrain_pak, template, destination):
    template, destination = Path(template).resolve(), Path(destination).absolute()
    if destination.resolve() != destination or 'mods' in [s.lower() for s in destination.parts]:
        raise ValueError('Use an authored build workspace, not a mounted mod')
    if destination == template or not destination.name.replace('_', '').isalnum():
        raise ValueError('Invalid native source destination')
    marker = destination / 'native-placement-inputs.json'
    if destination.exists() and not marker.exists():
        raise ValueError('Refusing to overwrite an unrelated editor scene')
    with zipfile.ZipFile(level_pak) as z:
        document = ET.fromstring(read(z, 'terrain/hlods.xml'))
        data = read(z, 'terrain/hlods.dat')
    with zipfile.ZipFile(terrain_pak) as z:
        terrain = parse(read(z, 'terrain/terrain.dat'))
    groups = extract(document, data)
    layer_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'native-hlod-input/main'))
    layer = ET.Element('Layer', GUID=layer_id, Name='Main', HavePhysics='1', IsDefaultColor='1')
    objects = ET.SubElement(layer, 'Objects')
    occurrences = Counter(); counts = Counter(); instances = bytearray(); digest = hashlib.sha256()
    for _, details, _ in groups:
        for record in details:
            digest.update(record)
            kind, = struct.unpack_from('<I', record)
            if kind == 1:
                objects.append(brush(record, terrain, occurrences[record]))
                occurrences[record] += 1
            else:
                instances.extend(vegetation_instance(record, len(terrain.tables['vegetation']['paths'])))
            counts[kind] += 1
    veg_root, _ = decode((template / 'leveldata/VegetationMap.dat').read_bytes())
    exemplar = copy.deepcopy(veg_root.find('Objects/Object'))
    veg_objects = veg_root.find('Objects'); veg_objects.clear(); veg_root.set('Version', '3')
    for index, path in enumerate(terrain.tables['vegetation']['paths']):
        obj = copy.deepcopy(exemplar)
        obj.set('Id', str(index)); obj.set('Index', str(index))
        obj.set('GUID', str(uuid.uuid5(uuid.NAMESPACE_URL, f'native-hlod-input/vegetation/{index}/{path}')))
        obj.set('FileName', path); obj.set('Object', path); obj.set('Hidden', '0')
        obj.set('Category', 'Converted placements'); obj.set('UberlodEnabled', '1')
        obj.set('AutoMerged', '0')
        start = terrain.tables['vegetation']['offset'] + index * 360
        alignment, = struct.unpack_from('<f', terrain.data, start + 280)
        obj.set('AlignToTerrain', str(int(alignment != 0)))
        obj.set('AlignToTerrainCoefficient', str(alignment))
        mat, = struct.unpack_from('<i', terrain.data, start + 336)
        if not -1 <= mat < len(terrain.tables['materials']['paths']):
            raise ValueError('Vegetation material outside resource table')
        obj.set('Material', terrain.tables['materials']['paths'][mat] if mat >= 0 else '')
        veg_objects.append(obj)
    elevation = heightmap(terrain, (template / 'leveldata/Heightmap.dat').read_bytes())
    # All parsing/transform validation precedes writing. This scene is solely
    # for generating hierarchy, never exported over gameplay/terrain packages.
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'Layers').mkdir(exist_ok=True)
    (destination / 'layerexportprofiles').mkdir(exist_ok=True)
    (destination / 'leveldata').mkdir(exist_ok=True)
    for name in ('level.editor_xml', 'leveldata/Environment.xml', 'leveldata/TerrainTexture.xml', 'leveldata/TimeOfDay.xml'):
        shutil.copyfile(template / name, destination / name)
    p = destination / 'level.editor_xml'
    p.write_text(p.read_text().replace(template.name, destination.name))
    env = ET.parse(destination / 'leveldata/Environment.xml'); env.find('HierarchicalLods').set('Enable', '1')
    env.write(destination / 'leveldata/Environment.xml')
    ET.ElementTree(layer).write(destination / 'Layers/main.lyr', encoding='utf-8')
    profile = ET.Element('LayerProfile', HasNavigation='0'); ET.SubElement(profile, 'ParentProfiles')
    ET.SubElement(ET.SubElement(profile, 'ObjectLayers'), 'ObjectLayer', GUID=layer_id, DebugPath='Main', Exported='1')
    ET.ElementTree(profile).write(destination / 'layerexportprofiles/game.xml', encoding='utf-8')
    (destination / 'leveldata/VegetationMap.dat').write_bytes(encode(veg_root, {'VegetationInstancesArray': bytes(instances)}))
    (destination / 'leveldata/Heightmap.dat').write_bytes(elevation)
    with zipfile.ZipFile(destination / (destination.name + '.cry'), 'w'):
        pass
    report = dict(brushes=counts[1], vegetation_instances=counts[2],
                  discarded_proxy_records=sum(proxy is not None for _, _, proxy in groups),
                  placement_sha256=digest.hexdigest(), legacy_membership_used=False,
                  source_level_pak=str(Path(level_pak).resolve()), package_ready=False,
                  limitations=['Vegetation render settings require native roundtrip validation',
                               'Proxy geometry has not been generated'])
    marker.write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('level-pak', 'terrain-pak', 'template', 'destination'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.level_pak, args.terrain_pak, args.template, args.destination), indent=2))
