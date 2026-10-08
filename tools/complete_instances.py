"""Append previously omitted instanced foliage and props to a v13-derived level."""
import argparse
from game_paths import GameLibrary
from collections import defaultdict, Counter
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from compiled_terrain import parse
from upgrade_map import read, xml
from vegetation import read_all_instances, verify_target_hlods, convert_instance
from merged_vegetation import inventory
from static_assets import asset_pack
from entity_visuals import initial_layer
from building_brushes import resource_table


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library', type=GameLibrary, default=Path(r'D:\SteamLibrary\steamapps\common'))
    p.add_argument('--base-level', required=True)
    p.add_argument('--level', required=True)
    p.add_argument('--original-report', type=Path, required=True, help='Report for the inherited v13 individual instances')
    args = p.parse_args()
    if not all(n and n.replace('_','').isalnum() for n in (args.level,args.base_level)):
        p.error('Invalid level name')
    root = args.library/'KCD2Mod/Data'
    base = root/'Levels'/args.base_level
    dest = root/'Levels'/args.level
    prefix = 'glueveg/'+args.level+'/'
    if dest.exists() or (root/prefix).exists():
        p.error('Use a new output level')
    old_report = json.loads(args.original_report.read_text())
    inherited = old_report['selected_instances']
    offsets = {r['offset'] for r in inherited}
    with ExitStack() as stack:
        source_pak = stack.enter_context(zipfile.ZipFile(args.library/'KingdomComeDeliverance/Data/Levels/rataje/level.pak'))
        source = parse(read(source_pak,'terrain/terrain.dat'))
        records, counts = read_all_instances(source)
        layers = ET.fromstring(read(source_pak,'leveldata.xml')).find('Layers')
        allowed = {0} | {int(e.get('Id')) for e in layers if initial_layer(e.get('Name'))}
        additions = [r for r in records if r['offset'] not in offsets and r['layer'] in allowed]
        _, merged_groups, _ = inventory(source_pak,len(source.tables['vegetation']['paths']))
        old_groups = sorted({r['group'] for r in inherited}|merged_groups)
        terrain_pak = stack.enter_context(zipfile.ZipFile(base/'terrain.pak'))
        level_pak = stack.enter_context(zipfile.ZipFile(base/'level.pak'))
        baseline = parse(terrain_pak.read('terrain/terrain.dat'))
        hlods = bytearray(level_pak.read('terrain/hlods.dat'))
        before = verify_target_hlods(hlods)
        if before.get(2) != len(inherited) or len(old_groups) != len(baseline.tables['vegetation']['paths']):
            raise ValueError('Base does not match inherited vegetation report')
        group_map = {g:i for i,g in enumerate(old_groups)}
        group_start = baseline.tables['vegetation']['offset']
        group_data = bytearray(baseline.data[group_start:group_start+len(old_groups)*360])
        for group, mapped in group_map.items():
            old = source.data[source.tables['vegetation']['offset']+group*360:source.tables['vegetation']['offset']+(group+1)*360]
            new = group_data[mapped*360:(mapped+1)*360]
            if old[256:320]+old[324:336]+old[340:] != new[256:320]+new[324:336]+new[340:]:
                raise ValueError('Inherited group identity mismatch')
        Path('outputs').mkdir(exist_ok=True)
        cache = stack.enter_context(tempfile.TemporaryDirectory(prefix='instances-',dir='outputs'))
        index, emitted, material, mesh_bytes = asset_pack(stack,args.library,prefix,cache)
        failures = {}
        materials = list(baseline.tables['materials']['paths'])
        groups = sorted({r['group'] for r in additions}-set(old_groups))
        for group in groups:
            original = source.tables['vegetation']['paths'][group].lower()
            target = prefix+'mesh'+str(len(group_map))
            try:
                payload = mesh_bytes(original)
                lods = {lod:mesh_bytes(original[:-4]+f'_lod{lod}.cgf') for lod in range(1,7) if original[:-4]+f'_lod{lod}.cgf' in index}
            except (KeyError,ValueError,FileNotFoundError) as error:
                failures[group] = str(error)
                continue
            start = source.tables['vegetation']['offset']+group*360
            record = bytearray(source.data[start:start+360])
            source_material = struct.unpack_from('<i',record,336)[0]
            target_material = -1
            if source_material != -1:
                try:
                    material_path = material(source.tables['materials']['paths'][source_material])
                except (KeyError,ValueError,FileNotFoundError) as error:
                    failures[group] = 'Group material: '+str(error)
                    continue
                if material_path not in materials:materials.append(material_path)
                target_material = materials.index(material_path)
            emitted[target+'.cgf'] = payload
            for lod, blob in lods.items():
                emitted[target+f'_lod{lod}.cgf'] = blob
            mapped = len(group_map)
            group_map[group] = mapped
            record[:256] = (target+'.cgf').encode().ljust(256,b'\0')
            struct.pack_into('<i',record,320,mapped+1)
            # Preserve per-group material overrides instead of discarding them.
            struct.pack_into('<i',record,336,target_material)
            group_data.extend(record)
        additions = [r for r in additions if r['group'] in group_map]
        print(f'Appending {len(additions)} instances; {len(group_map)-len(old_groups)} new groups',flush=True)
        material_start = baseline.tables['materials']['offset']-4
        terrain = bytearray(baseline.data[:group_start-4]+struct.pack('<I',len(group_map))+group_data
                            +baseline.data[group_start+len(old_groups)*360:material_start]
                            +resource_table(materials)+baseline.data[baseline.nodes[0]['offset']:])
        struct.pack_into('<I',terrain,4,len(terrain))
        checked = parse(bytes(terrain))
        if checked.data[checked.nodes[0]['offset']:] != baseline.data[baseline.nodes[0]['offset']:]:
            raise ValueError('Existing terrain or water changed')
        hlod_xml = ET.fromstring(level_pak.read('terrain/hlods.xml'))
        sectors = defaultdict(list)
        for r in additions:
            sectors[(int(r['pos'][0])//64,int(r['pos'][1])//64)].append(r)
        for (x,y), instances in sorted(sectors.items()):
            payload = b''.join(convert_instance(source.data,r['offset'],group_map[r['group']]) for r in instances)
            offset = len(hlods)
            hlods.extend(struct.pack('<I',len(payload))+payload)
            z = sum(r['pos'][2] for r in instances)/len(instances)
            ET.SubElement(hlod_xml,'HLod',DataOffset=str(offset),DataSize=str(len(payload)+4),ProxyIndex='-1',Type='Vegetation',
                          Pos=f'{x*64+32},{y*64+32},{z}',Radius='150',NearestObserverDistance='0',Name=f'added_instances_{x}_{y}')
        after = verify_target_hlods(hlods)
        if after.get(2)-before.get(2) != len(additions) or after.get(1) != before.get(1):
            raise ValueError('Unexpected HLOD counts')
        dest.mkdir(parents=True)
        for archive, filename in ((terrain_pak,'terrain.pak'),(level_pak,'level.pak')):
            with zipfile.ZipFile(dest/filename,'x',zipfile.ZIP_STORED) as output:
                for name in archive.namelist():
                    payload = archive.read(name)
                    if name == 'terrain/terrain.dat': payload = terrain
                    elif name == 'terrain/hlods.dat': payload = hlods
                    elif name == 'terrain/hlods.xml': payload = xml(hlod_xml)
                    elif name == 'levelinfo.xml':
                        doc=ET.fromstring(payload);doc.set('Name','data/levels/'+args.level);payload=xml(doc)
                        (dest/name).write_bytes(payload)
                    elif name == 'leveldata.xml':
                        doc=ET.fromstring(payload);doc.find('LevelInfo').set('Name',args.level);payload=xml(doc)
                    output.writestr(name,payload)
        for name,path in emitted.items():
            target = (root/name).resolve()
            if not target.is_relative_to((root/prefix).resolve()):raise ValueError('Invalid asset path')
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as out,path.open('rb') as inp:shutil.copyfileobj(inp,out)
        report = dict(level=args.level,base_level=args.base_level,source_counts=counts,added_instances=len(additions),
                      added_foliage=sum('/vegetation/' in r['mesh'].lower() for r in additions),
                      groups_added=len(group_map)-len(old_groups),before=before,after=after,failures=failures,
                      excluded_layers=dict(Counter(r['layer'] for r in records if r['offset'] not in offsets and r['layer'] not in allowed)),
                      note='Source positions, scale, rotation bytes and existing HLOD prefix preserved; visual LOD alignment still needs checking')
        (dest/'instances-report.json').write_text(json.dumps(report,indent=2))
        Path('reports/instances-probe.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True)


if __name__ == '__main__':main()
