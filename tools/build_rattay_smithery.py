"""Swap two reviewed Rattay workstations for the native KCD2 smithing setup."""
import argparse
import copy
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile

from build_horse_trader_test import digest, rewrite
from campaign_entity_links import append_links, guid_value
from chairs import inventory
from character_blacksmith_jobs import pose, vector, workstation_transform
from compiled_terrain import parse
from region_travel import guid
from seating_animations import HELPERS, ANIMATIONS
from upgrade_map import read, xml
from build_smithing_repair import ALIAS, MATERIAL, repair_coal

SCHEDULER = 'tables/ai/scheduler.xml'
FILES = ('objects_mission0.xml', 'waitinglinks.xml', HELPERS, ANIMATIONS, SCHEDULER,
         'terrain/hlods.dat', 'terrain/hlods.xml')
STATIONS = (('62317', '62319', '62168', '62166', (2542.5242,525.32996,76.169624)),
            ('62236', '62238', '62323', '62321', (2674.6909,661.62781,99.5877)))
# Reviewed anvil, quench tub and adjacent tool barrel; never remove the sheds.
BRUSHES = {'gluebuild/setup_props/mesh1126.cgf', 'gluebuild/setup_props/mesh1119.cgf',
           'gluebuild/setup_props/mesh1127.cgf'}
PREFIX = 'gm_rattay_smith_'


def remove_old_brushes(data, hierarchy, meshes, brushes=BRUSHES):
    root = ET.fromstring(hierarchy)
    if struct.unpack_from('<I', data)[0] != 2:
        raise ValueError('Unknown HLOD version')
    output = bytearray(data[:4]); removed = []
    for group in root.iter('HLod'):
        start, size = int(group.get('DataOffset')), int(group.get('DataSize'))
        if struct.unpack_from('<I', data, start)[0] != size-4:
            raise ValueError('Invalid HLOD block')
        cursor, records = start+4, []
        while cursor < start+size:
            kind = struct.unpack_from('<I', data, cursor)[0]
            if kind not in (1,2): raise ValueError('Unknown HLOD record')
            length = 104 if kind == 1 else 64
            remove = False
            if kind == 1:
                mesh = meshes[struct.unpack_from('<H', data, cursor+40)[0]]
                p = tuple(struct.unpack_from('<f', data, cursor+i)[0] for i in (56,72,88))
                remove = mesh in brushes and any(math.dist(p,s[4]) < 4 for s in STATIONS)
                if remove: removed.append(dict(mesh=mesh, position=p))
            if not remove: records.append(data[cursor:cursor+length])
            cursor += length
        if cursor != start+size: raise ValueError('Invalid record boundary')
        payload = b''.join(records)
        group.set('DataOffset', str(len(output))); group.set('DataSize', str(len(payload)+4))
        output.extend(struct.pack('<I',len(payload))); output.extend(payload)
    if len(removed) != 6: raise ValueError('Expected six original workstation brushes')
    return bytes(output), xml(root), removed


def convert(files, source, native, prefabs, meshes, brushes=BRUSHES):
    root = ET.fromstring(files['objects_mission0.xml'])
    if any(e.get('Name','').startswith(PREFIX) for e in root):
        raise ValueError('Rattay smithery already installed')
    src, _, _ = inventory(source)
    donor = {e.get('EntityId'):e for e in ET.fromstring(read(native,'objects_mission0.xml'))}
    if donor['46192'].get('EntityClass') != 'Smithery': raise ValueError('Donor changed')
    # Full minigame graph, including the player clearance point. Merchant/shop
    # links on that point belong to Tachov and are intentionally not imported.
    selected = {'46192'}
    while True:
        expanded = selected | {l.get('TargetId') for i in selected if i != '46215'
                               for l in donor[i].findall('EntityLinks/Link')}
        if expanded == selected: break
        selected = expanded
    helpers = ET.fromstring(files[HELPERS]); native_helpers = ET.fromstring(read(native,HELPERS))
    animations = ET.fromstring(files[ANIMATIONS]); rows = animations.find('SmartObjectAnimationss')
    scheduler = ET.fromstring(files[SCHEDULER]); hubs = scheduler.find('Schedulers')
    next_id = max(int(e.get('EntityId','0')) for e in root)+1
    network, reports = [], []
    base = ET.fromstring(read(prefabs,'Prefabs/profession/blacksmith/smithy_workshop_base.xml'))
    small = ET.fromstring(read(prefabs,'Prefabs/profession/blacksmith/smithy_workshop_small.xml'))
    # Base prefab origin is the exported alignment point. The small prefab has
    # a translated nested base; undo that translation for its forge/coal props.
    nested = next(e for e in small.iter('Object') if e.get('PrefabTemplateName','').endswith('/smithy_workshop_base'))
    base_offset = vector(nested)
    for forge, heat, hammer, old_hammer, _ in STATIONS:
        q,t,errors = workstation_transform([donor['46208'],donor['46209']], [src[heat],src[hammer]], tolerance=.1)
        mapped = {}
        for old in sorted(selected,key=int):
            node = pose(donor[old],q,t)
            for k in ('Prefab','EditorLayer','Layer','ParentId','ParentGuid'):node.attrib.pop(k,None)
            node.set('EntityId',str(next_id)); next_id += 1
            node.set('EntityGuid',guid(f'rattay-smith/{forge}/{old}'))
            node.set('Name',f'{PREFIX}{forge}_{old}')
            if old == '46215':node.remove(node.find('EntityLinks'))
            mapped[old] = node
        for old,node in mapped.items():
            for link in node.findall('EntityLinks/Link'):
                target = mapped[link.get('TargetId')]
                link.set('TargetId',target.get('EntityId'));link.set('TargetGuid',target.get('EntityGuid'))
                # Native reservation and workstation AI links have a separate network.
                if old in ('46199','46190'):
                    network.append((node.get('EntityGuid'),target.get('EntityGuid'),link.get('Name','')))
            ET.SubElement(hubs,'C_SmartHub',EntityGuid=str(guid_value(node.get('EntityGuid'))),IgnoreDeadEnds='false',StupidHub='false')
            props = node.find('Properties')
            family = props.get('soclass_SmartObjectHelpers') if props is not None else None
            if family:
                collection = next(e for e in native_helpers if e.get('HelperSetName') == family)
                if not any(e.get('HelperSetName') == family for e in helpers):helpers.append(copy.deepcopy(collection))
                collections = collection.findall('SmartObjectHelperSetAnimationCollection')
                if len(collections)!=1:raise ValueError('Ambiguous animation collection')
                row = ET.SubElement(rows,'SmartObjectAnimations',EntityGuid=str(guid_value(node.get('EntityGuid'))),
                                    Hash=collections[0].get('HashValue'),AllowRuntimeAnimValidationFallback='true')
                ET.SubElement(row,'SmartObjectTransform',Position=node.get('Pos'),Rotation=node.get('Rotate','1,0,0,0'))
        root.extend(mapped.values())
        align = mapped['46189']; aq = vector(align,'Rotate'); ap = vector(align)
        equipment = []
        for prefab, offset in ((base,(0,0,0)),(small,base_offset)):
            for obj in prefab.iter('Object'):
                if obj.get('Type')!='Brush' and obj.get('Name')!='coal_forge2':continue
                local = copy.deepcopy(obj)
                local.set('Pos',','.join(str(a-b) for a,b in zip(vector(obj),offset)))
                node = pose(local,aq,ap)
                geometry = obj.get('Geometry') or obj.get('Prefab')
                attrs = {k:node.get(k) for k in ('Pos','Rotate','Scale','Material') if node.get(k)}
                e = ET.Element('Entity',attrs,Name=f'{PREFIX}{forge}_prop_{obj.get("Id")}',
                               EntityClass='GeomEntity',EntityId=str(next_id),EntityGuid=guid(f'rattay-smith/{forge}/prop/{obj.get("Id")}'),
                               Geometry=geometry.replace('\\','/'),CastShadowMinSpec='1',ViewDistRatio='100')
                next_id += 1
                ET.SubElement(e,'Properties',bSaved_by_game='0',bInteractiveCollisionClass='1')
                root.append(e);equipment.append(e.get('Geometry'))
        removed = []
        for name in (f'glue_item_{forge}',f'glue_item_{old_hammer}'):
            matches = [e for e in root if e.get('Name')==name]
            if len(matches)!=1:raise ValueError('Missing original equipment '+name)
            root.remove(matches[0]);removed.append(name)
        reports.append(dict(forge=forge,position=vector(align),contact_errors_metres=errors,
                            entities=len(mapped)+len(equipment),removed=removed,native_equipment=equipment))
    result = dict(files)
    result.update({'objects_mission0.xml':repair_coal(xml(root)),HELPERS:xml(helpers),ANIMATIONS:xml(animations),SCHEDULER:xml(scheduler),
                   'waitinglinks.xml':append_links(files['waitinglinks.xml'],network,allow_untagged=True)})
    result['terrain/hlods.dat'],result['terrain/hlods.xml'],removed = remove_old_brushes(files['terrain/hlods.dat'],files['terrain/hlods.xml'],meshes,brushes)
    return result,dict(stations=reports,removed_brushes=removed,runtime_verified=False)


def build(library, mod, output):
    if output.exists():raise FileExistsError(output)
    level = 'Data/Levels/kcd1_travel/level.pak'; travel = 'Data/gluemappertravel.pak'
    with zipfile.ZipFile(mod/level) as installed, \
         zipfile.ZipFile(mod/'Data/Levels/kcd1_travel/terrain.pak') as terrain, \
         zipfile.ZipFile(library/'KingdomComeDeliverance/Data/Levels/rataje/level.pak') as source, \
         zipfile.ZipFile(library/'KingdomComeDeliverance2/Data/Levels/trosecko/level.pak') as native, \
         zipfile.ZipFile(library/'KingdomComeDeliverance2/Data/IPL_GameData.pak') as prefabs:
        files = {n:read(installed,n) for n in FILES}
        meshes = parse(read(terrain,'terrain/terrain.dat')).tables['meshes']['paths']
        brushes = resolve_brushes(files, meshes, mod, library/'KingdomComeDeliverance')
        updated,report = convert(files,source,native,prefabs,meshes,brushes)
    modid = ET.parse(mod/'mod.manifest').findtext('info/modid')
    changes = {level:updated,travel:{f'Mods/{modid}/Data/Levels/kcd1_travel/{n}':updated[n] for n in (ANIMATIONS,SCHEDULER)}}
    with zipfile.ZipFile(library/'KingdomComeDeliverance2/Data/IPL_Characters-part0.pak') as characters:
        changes[travel][ALIAS]=read(characters,MATERIAL)
    report.update(baseline={p:digest(mod/p) for p in changes},packages={},installed=False)
    for relative,replacements in changes.items():
        destination = output/'package'/relative
        rewrite(mod/relative,destination,replacements)
        with zipfile.ZipFile(mod/relative) as a,zipfile.ZipFile(destination) as b:
            before={i.filename:(i.CRC,i.file_size) for i in a.infolist() if i.filename not in replacements}
            after={i.filename:(i.CRC,i.file_size) for i in b.infolist() if i.filename not in replacements}
            if before!=after or any(read(b,n)!=v for n,v in replacements.items()):raise ValueError('Archive preservation failed')
        report['packages'][relative]=digest(destination)
    (output/'smithery.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


def resolve_brushes(files, meshes, mod, source):
    """Find the three original equipment meshes regardless of build numbering."""
    from build_open_test_gates import source_geometry
    from clothing_regions import chunk_digest
    from spatial_hlod import records
    models=('objects/props/blacksmith_tools/anvil_01.cgf',
            'objects/props/blacksmith_tools/water_container.cgf',
            'objects/props/wooden_bins/barrels/barrel_02/barrel_02_metal_pieces.cgf')
    nearby=set();data=files['terrain/hlods.dat']
    for group in ET.fromstring(files['terrain/hlods.xml']).iter('HLod'):
        start=int(group.get('DataOffset'));end=start+int(group.get('DataSize'))
        for row in records(data[start+4:end]):
            if len(row)!=104:continue
            point=tuple(struct.unpack_from('<f',row,i)[0] for i in (56,72,88))
            if any(math.dist(point,s[4])<4 for s in STATIONS):nearby.add(meshes[struct.unpack_from('<H',row,40)[0]].lower())
    from mesh_lod_metadata import normalize
    # Material paths may change and packaging computes missing mesh face areas.
    # Match exact reviewed variants, retaining one identity per source model.
    variants={}
    expected=set()
    for name,blob in source_geometry(source,models).items():
        expected.add(name)
        variants[chunk_digest(blob)]=name
        variants[chunk_digest(normalize(blob))]=name
    found={};matched=set()
    for path in sorted((mod/'Data').glob('*.pak')):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.lower() not in nearby:continue
                value=chunk_digest(read(archive,name))
                if value in variants:found[name.lower()]=value;matched.add(variants[value])
    if matched!=expected:raise ValueError('Missing original Rattay equipment geometry')
    return set(found)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('library','mod','output'):parser.add_argument('--'+name,type=Path,required=True)
    build(**vars(parser.parse_args()))
