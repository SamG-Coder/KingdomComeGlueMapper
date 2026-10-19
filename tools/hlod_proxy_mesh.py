"""Compose static CGF render geometry in a cluster's local coordinates.

Preserves source vertex streams, UVs, normals and material bindings. Physics,
helpers and embedded LOD nodes are not part of the distant representation.
This is geometry composition, not mesh simplification or texture atlasing.
"""
import struct
import copy
import xml.etree.ElementTree as ET
import numpy as np
from functools import lru_cache
from clothing_regions import Chunk, read_chunks, write_chunks
from mesh_lod_metadata import face_area


@lru_cache(maxsize=128)
def source_mesh(blob):
    chunks=read_chunks(blob)
    lookup={c.id:c for c in chunks}
    areas={}
    for c in chunks:
        if c.kind!=0x1000 or c.version!=0x801:continue
        area=face_area(c,lookup)
        if area is None:
            # Tiny but valid surfaces (rings, nails) have no statistic after
            # the engine's area threshold. Use that minimum, never zero; truly
            # degenerate geometry still fails validation.
            tiny=face_area(c,lookup,minimum_area=0)
            if tiny is not None:area=max(1e-5,tiny)
        areas[c.id]=area
    return chunks,lookup,areas


def compose(placements, origin, material_resolver=None, material_path=None, material_mode='combined'):
    """placements: iterable of (compiled CGF bytes, world 3x4 matrix, material).

    Material is an optional override path. Fail on unsupported render data;
    never replace an unreadable object with an empty mesh.
    """
    origin = np.asarray(origin, float)
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError('Invalid cluster origin')
    if bool(material_resolver) != bool(material_path):
        raise ValueError('Material resolver and output path must be supplied together')
    if material_mode not in ('combined','per_node'):raise ValueError('Invalid material binding mode')
    output = []; bounds = []; count = 0
    material_map = {}; submaterials = []; material_chunks = []; shared = {}; first_material=None
    def append(kind, version, data):
        data=bytes(data)
        key=(kind,version,data)
        if kind!=0x100b and key in shared:return shared[key]
        ident = len(output) + 1
        output.append(Chunk(kind, version, ident, data))
        if kind!=0x100b:shared[key]=ident
        return ident
    for occurrence, (blob, world, override) in enumerate(placements):
        world = np.asarray(world, float).reshape(3,4)
        if not np.isfinite(world).all() or abs(np.linalg.det(world[:,:3])) < 1e-10:
            raise ValueError('Invalid placement transform')
        placement = np.eye(4); placement[:3,:] = world; placement[:3,3] -= origin
        chunks,by_id,areas = source_mesh(blob); matrices = {}
        render_nodes=[c for c in chunks if c.kind==0x100b
                      and not c.data[:64].split(b'\0')[0].lower().startswith((b'$physics',b'$lod'))
                      and by_id.get(struct.unpack_from('<i',c.data,64)[0]) is not None
                      and by_id[struct.unpack_from('<i',c.data,64)[0]].kind==0x1000]
        # CStatObj::LoadCGF consumes a simple object's mesh directly. Its node
        # transform is authoring metadata, not an extra runtime transform.
        # Separate LOD files frequently carry large authoring-grid offsets.
        simple=len(render_nodes)==1
        merge=any(c.kind==0x1015 and len(c.data)>=4 and struct.unpack_from('<I',c.data)[0]&1 for c in chunks)
        if merge and not simple:raise ValueError('Unsupported merged compound source')
        def matrix(ident, seen=frozenset()):
            if ident in matrices:return matrices[ident]
            if ident in seen:raise ValueError('Cyclic source node hierarchy')
            node = by_id[ident]
            if node.kind != 0x100b or node.version != 0x824 or len(node.data)<204:
                raise ValueError('Unsupported source node')
            raw = np.array(struct.unpack_from('<16f',node.data,84)).reshape(4,4)
            m = np.eye(4); m[:3,:3] = raw[:3,:3].T; m[:3,3] = raw[3,:3]*.01
            parent, = struct.unpack_from('<i',node.data,68)
            if parent>0:m = matrix(parent,seen|{ident}) @ m
            matrices[ident] = m
            return m
        emitted = 0
        for node in chunks:
            if node.kind != 0x100b:continue
            name = node.data[:64].split(b'\0')[0].decode('utf-8')
            if name.lower().startswith(('$physics','$lod')):continue
            mid, _, _, material = struct.unpack_from('<4i',node.data,64)
            mesh = by_id.get(mid)
            if mesh is not None and mesh.kind == 0x1001:continue
            if mesh is None or mesh.kind != 0x1000 or mesh.version != 0x801 or len(mesh.data)!=264:
                raise ValueError('Unsupported static render mesh: '+name)
            flags,_,nv,ni,ns,subset = struct.unpack_from('<6I',mesh.data)
            if flags&5 or not nv or not ni:raise ValueError('Empty or skinned render mesh: '+name)
            data = bytearray(mesh.data)
            sub = by_id[subset]
            if sub.kind!=0x1017 or sub.version!=0x800:raise ValueError('Unsupported subsets')
            mat = by_id.get(material)
            if mat is None or mat.kind!=0x1014 or mat.version!=0x802:
                raise ValueError('Missing or unsupported material binding')
            sub_data=bytearray(sub.data)
            if material_resolver:
                path=(override or mat.data[:128].split(b'\0')[0].decode()).removesuffix('.mtl')+'.mtl'
                if path not in material_map:
                    root=ET.fromstring(material_resolver(path))
                    if first_material is None:first_material=copy.deepcopy(root)
                    children=root.find('SubMaterials')
                    source=list(children) if children is not None else [root]
                    if not source:raise ValueError('Empty source material')
                    material_map[path]=(len(submaterials),len(source),children is None)
                    submaterials.extend(copy.deepcopy(c) for c in source)
                base,length,single_material=material_map[path]
                if len(sub_data)<16+ns*36:raise ValueError('Truncated subsets')
                for subset_index in range(ns):
                    offset=16+subset_index*36+16
                    old_id,=struct.unpack_from('<i',sub_data,offset)
                    if single_material:old_id=0
                    if not 0<=old_id<length:raise ValueError(f'Subset material {old_id} outside {length} slots: {path}')
                    struct.pack_into('<i',sub_data,offset,(base if material_mode=='combined' else 0)+old_id)
            struct.pack_into('<I',data,20,append(sub.kind,sub.version,sub_data))
            struct.pack_into('<i',data,24,-1)
            for i, old in enumerate(struct.unpack_from('<16I',mesh.data,28)):
                if not old:continue
                stream = by_id[old]
                if stream.kind!=0x1016 or stream.version!=0x800:
                    raise ValueError('Unsupported stream')
                struct.pack_into('<I',data,28+4*i,append(stream.kind,stream.version,stream.data))
            # Render-only proxy: no collision mesh or animation references.
            struct.pack_into('<4i',data,92,0,0,0,0)
            area = areas[mesh.id]
            if area is None:raise ValueError('No nondegenerate render surface: '+name)
            struct.pack_into('<I',data,0,flags|8);struct.pack_into('<f',data,136,area)
            new_mesh = append(mesh.kind,mesh.version,data)
            mat_data = bytearray(mat.data)
            if override or (material_resolver and material_mode=='per_node'):
                path=(override or mat.data[:128].split(b'\0')[0].decode()).removesuffix('.mtl').encode('utf-8')
                if len(path)>=128:raise ValueError('Material path too long')
                mat_data[:128]=path.ljust(128,b'\0')
            new_material = append(mat.kind,mat.version,mat_data)
            material_chunks.append(new_material)
            transform = placement if simple else placement @ matrix(node.id)
            raw = np.eye(4);raw[:3,:3]=transform[:3,:3].T;raw[3,:3]=transform[:3,3]*100
            new_node = bytearray(204)
            new_node[:64] = f'proxy_{occurrence}_{emitted}'.encode().ljust(64,b'\0')
            struct.pack_into('<4i',new_node,64,new_mesh,-1,0,new_material)
            struct.pack_into('<16f',new_node,84,*raw.ravel())
            struct.pack_into('<3i',new_node,188,-1,-1,-1)
            append(node.kind,node.version,new_node)
            box = np.array(struct.unpack_from('<6f',mesh.data,108)).reshape(2,3)
            corners = np.array([[box[x,0],box[y,1],box[z,2]] for x in (0,1) for y in (0,1) for z in (0,1)])
            bounds.append(corners @ transform[:3,:3].T + transform[:3,3])
            count += ni//3;emitted += 1
        if not emitted:raise ValueError('Placement has no render nodes')
    if not output:raise ValueError('Empty cluster')
    nodes=[c for c in output if c.kind==0x100b]
    if len(nodes)==1:
        # Keep the compound-object loading contract by partitioning the draw
        # ranges into two actual meshes. No triangles are duplicated and no
        # position/normal quantisation is introduced by baking transforms.
        node=nodes[0];mesh=output[struct.unpack_from('<i',node.data,64)[0]-1]
        streams=struct.unpack_from('<16I',mesh.data,28)
        index_stream=output[streams[5]-1]
        width=struct.unpack_from('<I',index_stream.data,12)[0]
        subset=output[struct.unpack_from('<I',mesh.data,20)[0]-1]
        ns=struct.unpack_from('<I',mesh.data,16)[0]
        triangle_count=sum(struct.unpack_from('<I',subset.data,20+i*36)[0]//3 for i in range(ns))
        if triangle_count<2:raise ValueError('Single-triangle proxy requires vertex baking')
        remaining=triangle_count//2
        halves=[[],[]]
        for i in range(ns):
            raw=subset.data[16+i*36:52+i*36]
            first,num=struct.unpack_from('<2I',raw)
            take=min(remaining,num//3)*3;remaining-=take//3
            for half,start,length in ((0,first,take),(1,first+take,num-take)):
                if length:halves[half].append((raw,index_stream.data[24+start*width:24+(start+length)*width]))
        for half,parts in enumerate(halves):
            indices=bytearray();subsets=bytearray(subset.data[:16])
            struct.pack_into('<I',subsets,4,len(parts))
            for raw,payload in parts:
                row=bytearray(raw);struct.pack_into('<2I',row,0,len(indices)//width,len(payload)//width)
                subsets.extend(row);indices.extend(payload)
            header=bytearray(index_stream.data[:24]);struct.pack_into('<I',header,8,len(indices)//width)
            md=bytearray(mesh.data)
            struct.pack_into('<3I',md,12,len(indices)//width,len(parts),append(subset.kind,subset.version,subsets))
            struct.pack_into('<I',md,48,append(index_stream.kind,index_stream.version,header+indices))
            nd=bytearray(node.data);struct.pack_into('<i',nd,64,append(mesh.kind,mesh.version,md))
            nd[:64]=f'proxy_single_{half}'.encode().ljust(64,b'\0')
            if half==0:output[node.id-1]=Chunk(node.kind,node.version,node.id,bytes(nd))
            else:append(node.kind,node.version,nd)
    material_xml=None
    if material_resolver and material_mode=='per_node':
        material_xml=ET.tostring(first_material,encoding='unicode')
    elif material_resolver:
        if len(submaterials)>128:raise ValueError('Combined proxy exceeds native 128-submaterial limit')
        path=material_path.removesuffix('.mtl').encode()
        if len(path)>=128:raise ValueError('Output material path too long')
        root=ET.Element('Material',Name='HLOD cluster',MtlFlags='524544')
        children=ET.SubElement(root,'SubMaterials');children.extend(submaterials)
        material_xml=ET.tostring(root,encoding='unicode')
        # Each node uses the same combined material and globally remapped
        # subset IDs, so a native Brush material override cannot repaint it.
        data=path.ljust(128,b'\0')+struct.pack('<i',len(submaterials))
        data+=struct.pack('<'+'i'*len(submaterials),*([-1]*len(submaterials)))
        data+=b''.join(('part_'+str(i)).encode()+b'\0' for i in range(len(submaterials)))
        for ident in material_chunks:
            c=output[ident-1];output[ident-1]=Chunk(c.kind,c.version,c.id,data)
    points=np.concatenate(bounds)
    return write_chunks(output,{}), dict(triangles=count, bounds=[*points.min(axis=0),*points.max(axis=0)],
                                        simplified=False, textures_reused=True,
                                        material_xml=material_xml,submaterials=len(submaterials))
