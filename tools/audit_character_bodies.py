"""Read-only KCD1/KCD2 male/female mesh, rig and component comparison.

Writes proprietary source dumps only beneath an explicit local output folder.
Does not install assets, change character definitions or control the game.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
import copy
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
from pathlib import Path
import posixpath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile

from campaign_sources import retail_sources
from clothing_regions import read_chunks
from upgrade_map import read


def key(value):
    return value.replace('\\', '/').lower()


class Assets:
    def __init__(self, game, stack):
        self.game = game
        self.index = {}
        data = game / 'Data'
        paths = sorted(p for p in data.glob('*.pak')
                       if any(v in p.name.lower() for v in ('characters', 'heads', 'cloth')))
        paths += sorted((data/'patch').glob('*.pak'), key=self.patch_order)
        for path in paths:
            archive = stack.enter_context(zipfile.ZipFile(path))
            for name in archive.namelist():
                if key(name).startswith('objects/characters/') and name.lower().endswith(
                        ('.skin', '.chr', '.cdf', '.chrparams', '.mtl')):
                    self.index.setdefault(key(name), []).append((path, archive, name))

    @staticmethod
    def patch_order(path):
        m = re.fullmatch(r'(?:ipl_)?patch_(\d+)([a-z]?)(?:_hd)?\.pak', path.name.lower())
        if not m: raise ValueError('Unknown patch order: ' + path.name)
        return int(m[1]), m[2], path.name

    def get(self, name):
        candidates = self.index[key(name)]
        provenance = []; payload = None
        for path, archive, entry in candidates:
            payload = read(archive, entry)
            provenance.append(dict(archive=str(path), entry=entry,
                                   sha256=hashlib.sha256(payload).hexdigest()))
        # Duplicate base archives are usually identical. Reject differing base
        # definitions rather than silently treating alphabetical order as load order.
        bases = [p for p in provenance if '/patch/' not in key(p['archive'])]
        if len({p['sha256'] for p in bases}) > 1:
            raise ValueError('Ambiguous base character asset: ' + name)
        by_revision = {}
        for p in provenance:
            if '/patch/' in key(p['archive']):
                rev = self.patch_order(Path(p['archive']))[:2]
                if rev in by_revision and by_revision[rev] != p['sha256']:
                    raise ValueError('Ambiguous same-revision asset: ' + name)
                by_revision[rev] = p['sha256']
        return payload, provenance


def bone_table(chunks):
    tables = [c for c in chunks if c.kind == 0x2000]
    if len(tables) != 1: return []
    b = tables[0]
    if b.version != 0x800 or (len(b.data)-32) % 584: raise ValueError('Unsupported compiled bones')
    result = []
    for i, o in enumerate(range(32, len(b.data), 584)):
        parent = struct.unpack_from('<i', b.data, o+572)[0]
        result.append(dict(index=i, name=b.data[o+312:o+568].split(b'\0')[0].decode('ascii'),
                           parent_index=i+parent if parent else None,
                           inverse_bind=struct.unpack_from('<12f', b.data, o+216),
                           bind=struct.unpack_from('<12f', b.data, o+264)))
    for b in result:
        p = b['parent_index']
        if p is not None and not 0 <= p < len(result): raise ValueError('Invalid bone parent')
        b['parent'] = result[p]['name'] if p is not None else None
    return result


def inspect_mesh(blob):
    chunks = read_chunks(blob); by_id = {c.id:c for c in chunks}
    report = dict(chunks=[dict(kind=hex(c.kind), version=hex(c.version), id=c.id,
                               bytes=len(c.data), sha256=hashlib.sha256(c.data).hexdigest()) for c in chunks],
                  bones=bone_table(chunks))
    meshes = [c for c in chunks if c.kind == 0x1000]
    if len(meshes) != 1 or meshes[0].version not in (0x800, 0x801) or len(meshes[0].data) != 264:
        raise ValueError('Expected one standard mesh')
    mesh = meshes[0]; flags, _, nv, ni, ns, sid = struct.unpack_from('<6I',mesh.data)
    if not nv or ni % 3: raise ValueError('Empty vertices or incomplete triangles')
    report.update(flags=flags, vertices=nv, triangles=ni//3, subsets=ns)
    streams = {}
    for kind, ident in enumerate(struct.unpack_from('<16I',mesh.data,28)):
        if not ident: continue
        c = by_id[ident]
        f, actual, count, width = struct.unpack_from('<4I',c.data)
        if c.kind != 0x1016 or c.version != 0x800 or actual != kind or len(c.data) != 24+count*width:
            raise ValueError('Unsupported stream')
        expected = ni if kind == 5 else nv*2 if kind == 9 and flags & 4 else nv
        if count != expected: raise ValueError('Stream count does not match mesh')
        streams[kind] = (c, count, width)
    report['streams'] = {str(k):dict(count=n, width=w) for k, (_,n,w) in streams.items()}
    if streams[0][2] != 12: raise ValueError('Unsupported vertex positions')
    positions = list(struct.iter_unpack('<3f', streams[0][0].data[24:]))
    if not all(math.isfinite(x) for p in positions for x in p): raise ValueError('Nonfinite position')
    report['bounds'] = [[min(p[k] for p in positions), max(p[k] for p in positions)] for k in range(3)]
    inds, count, width = streams[5]
    if width not in (2,4) or count % 3: raise ValueError('Unsupported indices')
    faces = list(struct.iter_unpack('<3'+('H' if width==2 else 'I'), inds.data[24:]))
    if any(i>=nv for f in faces for i in f): raise ValueError('Invalid render vertex index')
    subset_chunk = by_id[sid]
    subsets = subset_chunk.data
    if (subset_chunk.kind != 0x1017 or subset_chunk.version != 0x800 or
            len(subsets) != 16+ns*36 or struct.unpack_from('<2I',subsets) != (0,ns)):
        raise ValueError('Unsupported subsets')
    report['subset_records'] = [dict(zip(('first_index','num_indices','first_vertex','num_vertices','material_id'),
                                            struct.unpack_from('<5I',subsets,16+i*36))) for i in range(ns)]
    if 3 in streams and streams[3][2] == 4:
        colors = list(struct.iter_unpack('<4B',streams[3][0].data[24:]))
        report['vertex_color_channels'] = [dict(Counter(str(c[k]) for c in colors)) for k in range(4)]
    if 9 in streams and streams[9][2] == 12:
        data = streams[9][0].data
        influence = Counter(); extra = Counter(); weight_sums = [0]*nv; extra_vertices = 0
        for record,o in enumerate(range(24,len(data),12)):
            target = influence if record < nv else extra
            weights = data[o+8:o+12]
            weight_sums[record % nv] += sum(weights)
            if record >= nv and any(weights): extra_vertices += 1
            for i,w in zip(struct.unpack_from('<4H',data,o),weights):
                if w:
                    if i>=len(report['bones']): raise ValueError('Bone index outside table')
                    target[report['bones'][i]['name']] += w
        report['primary_weight_totals'] = dict(influence)
        report['extra_weight_totals'] = dict(extra)
        report['extra_influenced_vertices'] = extra_vertices
        report['weight_sum_histogram'] = dict(Counter(weight_sums))
        report['has_extra_weights'] = bool(flags & 4)
    return report, positions, faces


def resolve_component(root, name):
    found = []
    def visit(node, ancestors):
        chain = ancestors + [node]
        if node.get('Name') == name and node.tag in ('Body','Head','Hair','Clothing','Beard'): found.append(chain)
        for c in node: visit(c,chain)
    visit(root,[])
    if len(found) != 1: raise ValueError('Ambiguous or missing component: '+name)
    nodes = [n for n in found[0] if n.get('Name')]
    elements = {}; attributes = {}; path = ''; raw = []
    for n in nodes:
        attributes.update(n.attrib)
        if n.get('FilePath'): path = n.get('FilePath')
        raw_node = copy.deepcopy(n)
        for child in raw_node.findall('DerivedComponents'): raw_node.remove(child)
        raw.append(ET.tostring(raw_node,encoding='unicode'))
        for e in n.findall('./Elements/*'):
            if e.tag not in ('SkinElement', 'VClothElement'): continue
            slot = (e.get('EquipmentPart'),e.get('BodyLayerId'))
            # Child overrides commonly omit BodyLayerId; there is one inherited
            # element per part in the selected body/head reference components.
            previous = [k for k in elements if k[0] == slot[0]]
            if slot[1] is None and len(previous)==1: slot=previous[0]
            elif slot[1] is None and len(previous)>1:
                raise ValueError('Ambiguous inherited element: '+name)
            if slot[1] is not None and slot not in elements and (slot[0],None) in elements:
                elements[slot] = elements.pop((slot[0],None))
            merged = elements.get(slot,{}).copy()
            merged['element_type'] = e.tag
            for k,v in e.attrib.items():
                if k in ('Model','Material','SimBinding'):
                    if v: merged[k+'Resolved'] = posixpath.normpath('objects/characters/'+path.rstrip('/')+'/'+v)
                    else: merged.pop(k+'Resolved',None)
                merged[k]=v
            elements[slot] = merged
    return dict(name=name, ancestry=[dict(tag=n.tag,**n.attrib) for n in nodes],
                inherited_attributes=attributes, elements=list(elements.values()), source_nodes=raw)


def save_xml_sources(game, specifications, output):
    sources = retail_sources(game, {n:('Tables.pak', 'Libs/Tables/'+n+'.xml') for n in specifications})
    result = {}
    for name,s in sources.items():
        path=output/'tables'/(name+'.xml');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(s['data'])
        result[name] = dict(path=str(path), provenance=s['candidates'])
    return result


def dump_skin_shaders(game, output):
    """Capture the original shader source and its archive provenance."""
    archive_path = game/'Engine/Shaders.pak'
    result = []
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if Path(name).name.lower() not in ('humanskin.cfx', 'humanskinvalidations.cfi',
                                               'whhidinggroups.cfi', 'whclothingsystem.cfi'):
                continue
            data = read(archive,name)
            target = output/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
            result.append(dict(archive=str(archive_path),entry=name,path=str(target),
                               sha256=hashlib.sha256(data).hexdigest()))
    return result


def capture_live_manager(name, output):
    """GET only: capture the loaded character's selected components and masks.

    Depth four is intentional: deeper reflection enters unsupported equipment
    part zero in the development runtime. StreamingState is recorded verbatim;
    it is not a GPU visibility or draw-call assertion.
    """
    if not re.fullmatch(r'[A-Za-z0-9_]+', name): raise ValueError('Invalid entity name')
    connection = http.client.HTTPConnection('127.0.0.1',1403,timeout=10)
    try:
        connection.request('GET','/api/ent/ClothingSystem/AttachmentManagersByName/'+name+'?depth=4')
        response = connection.getresponse()
        if response.status != 200: raise RuntimeError('Clothing manager HTTP '+str(response.status))
        data = response.read()
    finally:
        connection.close()
    root = ET.fromstring(data)
    if root.tag != 'ComponentAttachmentManager' or root.get('OwnerName') != name:
        raise ValueError('Unexpected live clothing manager owner')
    output.mkdir(parents=True,exist_ok=True)
    path = output/(name+'.xml');path.write_bytes(data)
    return dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),
                captured_utc=datetime.now(timezone.utc).isoformat(),state=root.attrib,
                selected_components=[dict(n.attrib) for n in root.findall('.//SlotCache//Value[@Name]')],
                attachments=[dict(name=n.get('Key'),state=n.find('Value').attrib,
                                  elements=[e.attrib for e in n.findall('.//Element')])
                             for n in root.findall('AttachmentsByName/Pair')])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kcd1',type=Path,required=True);p.add_argument('--kcd2',type=Path,required=True)
    p.add_argument('--development',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--live',action='store_true',help='Also GET the loaded parents and player clothing managers')
    a=p.parse_args()
    if a.output.exists():raise FileExistsError('Choose a new dump directory')
    a.output.mkdir(parents=True)
    result=dict(schema=2, purpose='Read-only asset comparison with optional loaded component trace',
                created_utc=datetime.now(timezone.utc).isoformat(),assets={},tables={},components={},shaders={})
    with ExitStack() as stack:
        games={'KDC1':Assets(a.kcd1,stack),'KDC2':Assets(a.kcd2,stack)}
        def dump(game,name):
            label=game+'/'+key(name)
            if label in result['assets']:return label
            data,provenance=games[game].get(name)
            path=a.output/'raw'/game/key(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            entry=dict(path=str(path),provenance=provenance)
            if name.lower().endswith(('.skin','.chr')):
                info,vertices,faces=inspect_mesh(data);entry['mesh']=info
                obj=path.with_suffix(path.suffix+'.obj')
                with obj.open('w',encoding='utf-8') as out:
                    out.write('# Static authored mesh; no runtime hiding, morphs or animation applied\n')
                    for v in vertices:out.write('v '+' '.join(format(x,'.9g') for x in v)+'\n')
                    for f in faces:out.write('f '+' '.join(str(i+1) for i in f)+'\n')
                entry['obj']=str(obj)
            result['assets'][label]=entry
            return label
        for game,root in [('KDC1',a.kcd1),('KDC2',a.kcd2)]:
            common=['item/equipment_part','item/body_layer','item/body_subpart','item/body_material2subpart']
            extra=(['character_body','character_head','item/clothing','item/clothing_attachment','item/clothing_mesh_data',
                    'item/armor','item/armor2clothing_preset','item/armor2clothing_attachment','item/attachment2clothing_preset']
                   if game=='KDC1' else ['Character/CharacterComponent','Character/ClothingConfig','Character/ClothingMorph',
                                        'Character/ClothingHidingGroup','Character/ClothingFeature'])
            result['tables'][game]=save_xml_sources(root,common+extra,a.output/game)
            result['shaders'][game]=dump_skin_shaders(root,a.output/'raw'/game)
            for sex in ('male','female'):
                prefix='objects/characters/humans/'+(sex+'/' if game=='KDC2' else '')+'skeleton/'+sex
                for ext in ('.chr','.cdf','.chrparams'):
                    if key(prefix+ext) in games[game].index:dump(game,prefix+ext)
        for sex,namespace in [('male','martin_v1'),('female','campaign_mother_v1')]:
            root=a.development/'Data/objects/characters/gluenpc'/namespace
            source=json.loads((root/'npc-report.json').read_text(encoding='utf-8'))
            result['components']['KDC1_'+sex]=dict(npc=source['npc'],parts=[])
            for i,part in enumerate(source['parts']):
                label=dump('KDC1',part['mesh_path']);dump('KDC1',part['material_path'])
                result['components']['KDC1_'+sex]['parts'].append(dict(kind=part['kind'],asset=label,source_record=part))
                converted=root/('part'+str(i)+'.skin')
                if converted.exists():
                    payload=converted.read_bytes();info,_,_=inspect_mesh(payload)
                    target=a.output/'installed/objects/characters/gluenpc'/namespace/converted.name
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
                    result['assets']['installed/'+namespace+'/'+converted.name]=dict(path=str(target),source_path=str(converted),mesh=info,
                        sha256=hashlib.sha256(payload).hexdigest())
            for file in sorted(root.iterdir()):
                if file.suffix.lower() not in ('.mtl','.cdf') and file.name not in ('npc-report.json','clothing-report.json'):
                    continue
                target=a.output/'installed/objects/characters/gluenpc'/namespace/file.name
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(file.read_bytes())
        native=ET.parse(result['tables']['KDC2']['Character/CharacterComponent']['path']).getroot()
        for component in ('male_body_npc','female_body','m_head_000','f_head_000','m_head_father','f_head_mother',
                          'F_SimpleDress06_mMother','TunicShort05_mFather','f_underwear01_m08'):
            resolved=resolve_component(native,component);result['components']['KDC2_'+component]=resolved
            for e in resolved['elements']:
                for kind in ('Model','Material','SimBinding'):
                    asset=e.get(kind+'Resolved')
                    if asset and key(asset) in games['KDC2'].index:dump('KDC2',asset)
                    elif asset:result.setdefault('unresolved',[]).append(dict(component=component,asset=asset))
        # Snapshot current installed definitions as a separate comparison input.
        for relative in ('Libs/Tables/Character/CharacterComponent.xml','Libs/Tables/Character/ClothingConfig.xml',
                         'Libs/Storm/appearance/gluemapper.xml','Libs/Storm/equipment/gluemapper.xml'):
            file=a.development/'Data'/relative
            if file.exists():
                target=a.output/'installed'/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(file.read_bytes())
        result['shaders']['installed']=dump_skin_shaders(a.development,a.output/'installed')
        if a.live:
            result['live']={}
            for name in ('ska_fatherOfHenry','ska_motherOfHenry','Dude'):
                try: result['live'][name]=capture_live_manager(name,a.output/'live')
                except (OSError,ValueError,RuntimeError,http.client.HTTPException) as error:
                    result['live'][name]=dict(error=str(error))
        (a.output/'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(a.output),assets=len(result['assets']),components=len(result['components']),
                         unresolved=result.get('unresolved',[]))))


if __name__=='__main__':main()
