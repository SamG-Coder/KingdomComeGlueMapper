"""Register source cartography as a separate native region, with its mip files."""
import hashlib
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from campaign_sources import RetailSourceReader
from upgrade_map import read, xml


def register_map(level_table, level, map_name, size, tiles):
    root = ET.fromstring(level_table)
    matches = [r for r in root.findall('./levels/LevelData') if r.get('LevelName') == level]
    if len(matches) != 1:
        raise ValueError('Expected one destination level registration')
    side = math.isqrt(len(tiles))
    if not side or side * side != len(tiles) or sorted(tiles) != list(range(1, len(tiles) + 1)):
        raise ValueError('Source cartography must have a complete square tile grid')
    if not math.isfinite(size) or size <= 0:
        raise ValueError('Invalid source map extent')
    matches[0].attrib.update(MapName=map_name, MapDefaultZoom='1', MapMinZoom='-1', MapMaxZoom='1',
        MapLevelSize=f'{size:g},{size:g}', MapWorldOrig='0,0', MapWorldSize=f'{size:g},{size:g}',
        MapDefaultPos=f'{size/2:g},{size/2:g}', MapTilesSize=f'{side},{side}',
        MapTilesCropOrig='0,0', MapTilesCropSize=f'{side},{side}', MapTiles=','.join(map(str, sorted(tiles))))
    return xml(root)


def resources(source, level_table, level):
    source = Path(source)
    prefix = 'Libs/UI/Textures/Maps/'
    pattern = re.compile(r'libs/ui/textures/maps/globalmap_(\d+)\.dds(?:\.(?:\d+a?|a))?$', re.I)
    with zipfile.ZipFile(source / 'Data/GameData.pak') as archive:
        entries = [n for n in archive.namelist() if pattern.fullmatch(n)]
    tiles = sorted({int(pattern.fullmatch(n)[1]) for n in entries if n.lower().endswith('.dds')} - {0})
    if not any(n.lower().endswith('globalmap_0.dds') for n in entries):
        raise ValueError('Source map overview texture is missing')
    with zipfile.ZipFile(source / 'Data/Levels/rataje/level.pak') as archive:
        info = ET.fromstring(read(archive, 'levelinfo.xml')).find('TerrainInfo')
        size = float(info.get('HeightmapSize')) * float(info.get('UnitSize'))
    name = 'gluemapper_rataje'
    files, provenance = {}, []
    with RetailSourceReader(source) as reader:
        for entry in entries:
            result = reader(source, {'map': ('GameData.pak', entry)})['map']
            suffix = entry.rsplit('/', 1)[1].split('_', 1)[1]
            target = prefix + 'global_map_' + name + '_' + suffix
            files[target] = result['data']
            provenance.append(dict(source=entry, target=target, sha256=hashlib.sha256(result['data']).hexdigest(),
                                   candidates=result['candidates']))
    return register_map(level_table, level, name, size, tiles), files, dict(
        map_name=name, extent=size, detailed_tiles=len(tiles), texture_files=len(files),
        coordinate_basis='Full source TerrainInfo extent, unchanged world coordinates',
        source=provenance, runtime_verified=False)
