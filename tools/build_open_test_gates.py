"""Opt-in testing override for the reviewed Talburg and Skalitz gate pieces."""
import argparse
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile
from build_horse_trader_test import digest, rewrite
from compiled_terrain import parse
from upgrade_map import read, xml

GRATES = ((665.926513671875,3467.761962890625,68.28124237060547),
          (725.7950439453125,3473.84814453125,62.68354797363281))
LIFT = 4.0


def source_geometry(source, names):
    """Read geometry across the same base/DLC/patch families as asset_pack."""
    wanted=set(names);found={}
    paths=sorted((p for p in (Path(source)/'Data').glob('*.pak') if p.name.startswith(('GameData','Objects','Buildings','Characters'))),key=lambda p:('_HD' in p.name,p.name.lower()))
    paths.extend(sorted((Path(source)/'Data/patch').glob('*.pak')))
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                key=name.replace('\\','/').lower()
                if key in wanted:found[key]=read(archive,name)
    if set(found)!=wanted:raise ValueError('Missing original geometry: '+str(sorted(wanted-set(found))))
    return found


def open_gates(files, meshes, grate_mesh='gluebuild/setup_buildings/mesh1197.cgf', palisade_mesh='gluebuild/setup_props/entity104.cga'):
    data = bytearray(files['terrain/hlods.dat'])
    hierarchy = ET.fromstring(files['terrain/hlods.xml'])
    mission = ET.fromstring(files['objects_mission0.xml'])
    hits = []
    if struct.unpack_from('<I',data)[0] != 2:
        raise ValueError('Unknown HLOD format')
    for group in hierarchy.iter('HLod'):
        start = int(group.get('DataOffset')); size = int(group.get('DataSize'))
        if start+size > len(data) or struct.unpack_from('<I',data,start)[0] != size-4:
            raise ValueError('Invalid HLOD block')
        cursor = start+4
        while cursor < start+size:
            kind = struct.unpack_from('<I',data,cursor)[0]
            if kind not in (1,2):raise ValueError('Unknown record type')
            if kind == 1:
                mesh = meshes[struct.unpack_from('<H',data,cursor+40)[0]]
                position = tuple(struct.unpack_from('<f',data,cursor+i)[0] for i in (56,72,88))
                if mesh == grate_mesh and any(math.dist(position,p)<.01 for p in GRATES):
                    if position in hits:raise ValueError('Duplicate gate placement')
                    hits.append(position)
                    # Move the actual brush and its world-space render/collision bounds.
                    for off in (12,24,88):
                        struct.pack_into('<f',data,cursor+off,struct.unpack_from('<f',data,cursor+off)[0]+LIFT)
                    group.set('Radius',str(float(group.get('Radius'))+LIFT))
            cursor += 104 if kind==1 else 64
        if cursor != start+size:raise ValueError('Invalid record boundary')
    if len(hits)!=2:raise ValueError('Expected two original Skalitz portcullises; wrong baseline or already applied')
    gates = [e for e in mission.iter('Entity') if e.get('Name')=='glue_item_124019']
    if len(gates)!=1:raise ValueError('Missing Talburg palisade gate')
    gate=gates[0]
    if gate.get('Geometry')!=palisade_mesh or gate.get('Pos')!='2346.5435,2828.605,105.96074':
        raise ValueError('Talburg gate baseline changed')
    gate.set('Pos','2346.5435,2828.605,109.96074')
    result=dict(files)
    result.update({'terrain/hlods.dat':bytes(data),'terrain/hlods.xml':xml(hierarchy),'objects_mission0.xml':xml(mission)})
    return result,dict(skalitz_portcullises=[list(p) for p in hits],talburg_palisade='glue_item_124019',lift_metres=LIFT,
                       testing_only=True,runtime_passage_verified=False)


def resolve_models(files, meshes, asset, original):
    """Generated mesh numbers vary; require the original geometry at each site."""
    from clothing_regions import chunk_digest
    from spatial_hlod import records
    candidates=set()
    data=files['terrain/hlods.dat']
    for group in ET.fromstring(files['terrain/hlods.xml']).iter('HLod'):
        start=int(group.get('DataOffset'));end=start+int(group.get('DataSize'))
        for row in records(data[start+4:end]):
            if len(row)!=104:continue
            point=tuple(struct.unpack_from('<f',row,i)[0] for i in (56,72,88))
            if any(math.dist(point,p)<.01 for p in GRATES):
                candidates.add(meshes[struct.unpack_from('<H',row,40)[0]])
    if len(candidates)!=1:raise ValueError('Expected one gate model at the two reviewed Skalitz placements')
    grate=candidates.pop()
    gate=ET.fromstring(files['objects_mission0.xml']).find(".//Entity[@Name='glue_item_124019']")
    if gate is None:raise ValueError('Missing Talburg gate source identity')
    palisade=gate.get('Geometry')
    for target,source in ((grate,'objects/buildings/castles/castle_talmberk/castle_talmberk_grate.cgf'),
                          (palisade,'objects/buildings/wall_wooden/talmberk_palisade_gate.cga')):
        actual=chunk_digest(asset(target))
        reviewed=original(source)
        matches=actual==chunk_digest(reviewed)
        if not matches and source.endswith('.cgf'):
            # Packaging calculates missing face-area metadata. Compare against
            # that exact source conversion, never mask arbitrary header changes.
            from mesh_lod_metadata import normalize
            try:
                matches=actual==chunk_digest(normalize(reviewed))
            except (ValueError, KeyError, struct.error):
                matches=False
        if not matches:
            raise ValueError('Gate geometry differs from its reviewed source: '+source)
    return grate,palisade


def build(mod, output, source=None):
    if output.exists():raise FileExistsError(output)
    level=mod/'Data/Levels/kcd1_travel/level.pak'
    with zipfile.ZipFile(mod/'Data/Levels/kcd1_travel/terrain.pak') as z:
        meshes=parse(read(z,'terrain/terrain.dat')).tables['meshes']['paths']
    with zipfile.ZipFile(level) as z:
        original={n:read(z,n) for n in ('terrain/hlods.dat','terrain/hlods.xml','objects_mission0.xml')}
    models=()
    if source is not None:
        from contextlib import ExitStack
        with ExitStack() as stack:
            assets={}
            for path in sorted((mod/'Data').glob('*.pak')):
                archive=stack.enter_context(zipfile.ZipFile(path))
                assets.update({n.lower():(archive,n) for n in archive.namelist() if n.lower().endswith(('.cgf','.cga'))})
            originals=source_geometry(source,('objects/buildings/castles/castle_talmberk/castle_talmberk_grate.cgf','objects/buildings/wall_wooden/talmberk_palisade_gate.cga'))
            models=resolve_models(original,meshes,lambda n:read(*assets[n.lower()]),originals.__getitem__)
    updated,report=open_gates(original,meshes,*models)
    from skalitz_outer_gates import normalize
    updated, correction = normalize(updated)
    report['skalitz_outer_gates'] = correction
    report['skalitz_portcullises'] = []
    target=output/'Data/Levels/kcd1_travel/level.pak'
    rewrite(level,target,updated)
    with zipfile.ZipFile(level) as a,zipfile.ZipFile(target) as b:
        before={i.filename:(i.CRC,i.file_size) for i in a.infolist() if i.filename not in updated}
        after={i.filename:(i.CRC,i.file_size) for i in b.infolist() if i.filename not in updated}
        if before!=after:raise ValueError('Unrelated archive entries changed')
        for n,payload in updated.items():
            if read(b,n)!=payload:raise ValueError('Staging mismatch')
    report.update(baseline_sha256=digest(level),staged_sha256=digest(target),unrelated_entries_preserved=len(before),installed=False)
    (output/'gates.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mod',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source',type=Path,help='KCD1 installation; resolve generated model names from original geometry')
    build(**vars(p.parse_args()))
