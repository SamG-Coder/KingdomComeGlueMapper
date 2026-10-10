"""Upgrade compiled KDC1 skins for a target rig, preserving source proportions.

Pure byte-in/byte-out operations: no game files are written here. Materials,
UVs and source triangles are preserved. Rig conversion retains all influences;
the separate native packing step normalizes them to the target's four slots.
Render/internal positions, tangent frames, morph deltas and bounds move together
when explicit bind-space reposing is requested.
"""
from collections import Counter, defaultdict
import math
import re
import struct

import numpy as np

from audit_character_bodies import bone_table, inspect_mesh
from clothing_regions import Chunk, read_chunks, write_chunks


class CompiledSkin:
    def __init__(self, blob):
        self.blob=blob
        self.info,vertices,faces=inspect_mesh(blob)
        self.chunks=read_chunks(blob);self.by_id={c.id:c for c in self.chunks}
        self.mesh=self.one(0x1000);self.bones=self.one(0x2000)
        self.vertices=np.asarray(vertices,dtype=np.float64)
        self.faces=np.asarray(faces,dtype=np.int64)
        self.streams={k:self.by_id[i] for k,i in enumerate(struct.unpack_from('<16I',self.mesh.data,28)) if i}
        self.n=len(vertices)
        self.remap_chunk=self.one(0x2006)
        self.remap=np.frombuffer(self.remap_chunk.data,dtype='<u2')
        self.internal=self.one(0x2005)
        if self.internal.version!=0x800 or (len(self.internal.data)-32)%64 or len(self.remap)!=self.n:
            raise ValueError('Unsupported internal skin vertices')
        self.ninternal=(len(self.internal.data)-32)//64
        if np.any(self.remap>=self.ninternal):raise ValueError('Invalid internal remap')
        internal=np.array([struct.unpack_from('<3f',self.internal.data,32+64*int(i)+12) for i in self.remap])
        if not np.allclose(internal,self.vertices,rtol=0,atol=1e-5):
            raise ValueError('Render/internal positions disagree before conversion')
        weight=self.streams.get(9)
        if weight is None or struct.unpack_from('<I',weight.data,12)[0]!=12:
            raise ValueError('Expected compiled four/eight bone influences')
        records=np.frombuffer(weight.data[24:],dtype=np.dtype([('ids','<u2',4),('weights','u1',4)]))
        blocks=2 if self.info['has_extra_weights'] else 1
        self.ids=np.concatenate([records['ids'][i*self.n:(i+1)*self.n] for i in range(blocks)],axis=1)
        self.weights=np.concatenate([records['weights'][i*self.n:(i+1)*self.n] for i in range(blocks)],axis=1).astype(float)
        if np.any(self.weights.sum(axis=1)!=255):raise ValueError('Skin weights do not sum to 255')
        self.weights/=255

    def one(self, kind):
        matches=[c for c in self.chunks if c.kind==kind]
        if len(matches)!=1:raise ValueError(f'Expected one {kind:#x} chunk')
        return matches[0]


def matrix(values):
    m=np.eye(4);m[:3]=np.asarray(values).reshape(3,4)
    if not np.isfinite(m).all() or abs(np.linalg.det(m[:3,:3]))<1e-8:
        raise ValueError('Invalid bind transform')
    return m


def spine_mapping(source, target):
    """Match the axial chain monotonically, preserving its two end joints.

    Equivalent chains use exact names. Different chains use their relative
    rest-pose arc lengths; this handles the additional native female spine
    joint without renaming its upper chest to the lower native Spine3.
    """
    def chain(bones):
        lookup={b['name']:b for b in bones};name=lookup.get('Neck',{}).get('parent');out=[]
        while name and re.fullmatch(r'Spine\d*',name):
            if name in out:raise ValueError('Cyclic spine')
            out.append(name);name=lookup[name]['parent']
        out.reverse();return lookup,out
    a,sa=chain(source);b,sb=chain(target)
    if not sa or not sb or sa==sb:return {}
    if len(sa)>len(sb):raise ValueError('Target spine has fewer joints; an explicit mapping is required')
    def fractions(names,lookup):
        points=[matrix(lookup[n]['bind'])[:3,3] for n in names]
        distances=np.r_[0,np.cumsum([np.linalg.norm(y-x) for x,y in zip(points,points[1:])])]
        if distances[-1]<=0:raise ValueError('Degenerate spine chain')
        return distances/distances[-1]
    x,y=fractions(sa,a),fractions(sb,b);chosen=[]
    for i,f in enumerate(x):
        lower=chosen[-1]+1 if chosen else 0
        upper=len(sb)-(len(sa)-i)
        j=0 if i==0 else len(sb)-1 if i==len(sa)-1 else min(range(lower,upper+1),key=lambda j:abs(y[j]-f))
        chosen.append(j)
    return {s:sb[j] for s,j in zip(sa,chosen) if s!=sb[j]}


def read_morphs(chunk):
    """Decode both original float (0x801) and compressed (0x802) morphs."""
    data=chunk.data
    if chunk.version not in (0x801,0x802) or len(data)<4:raise ValueError('Unsupported morph format')
    out=[];offset=4;names=set()
    for _ in range(struct.unpack_from('<I',data)[0]):
        length=48 if chunk.version==0x802 else 16
        if offset+length>len(data):raise ValueError('Truncated morph header')
        header=data[offset:offset+length];mesh,nname,ni,ne=struct.unpack_from('<4I',header)
        offset+=length
        if not 1<=nname<=256 or offset+nname>len(data):raise ValueError('Invalid morph name')
        raw=data[offset:offset+nname];offset+=nname
        if raw[-1:]!=b'\0' or b'\0' in raw[:-1]:raise ValueError('Invalid morph name termination')
        name=raw[:-1].decode('ascii')
        if name in names:raise ValueError('Duplicate morph name')
        names.add(name)
        if chunk.version==0x802:
            nr=struct.unpack_from('<I',header,40)[0]
            if offset+nr*4+ne*3>len(data):raise ValueError('Truncated compressed morph')
            runs=list(struct.iter_unpack('<2H',data[offset:offset+nr*4]));offset+=nr*4
            ids=[];previous=0
            for start,end in runs:
                if start<previous or end<=start:raise ValueError('Invalid morph runs')
                ids.extend(range(start,end));previous=end
            if len(ids)!=ne:raise ValueError('Morph run count mismatch')
            q=np.frombuffer(data[offset:offset+ne*3],dtype='u1').reshape(-1,3);offset+=ne*3
            minimum=np.array(struct.unpack_from('<3f',header,16));extent=np.array(struct.unpack_from('<3f',header,28))
            delta=minimum+q*extent/255
            internal=None
        else:
            if offset+(ni+ne)*16>len(data):raise ValueError('Truncated float morph')
            internal=list(struct.iter_unpack('<I3f',data[offset:offset+ni*16]));offset+=ni*16
            external=list(struct.iter_unpack('<I3f',data[offset:offset+ne*16]));offset+=ne*16
            ids=[r[0] for r in external];delta=np.array([r[1:] for r in external],dtype=float).reshape(-1,3)
            if len(ids)!=len(set(ids)):raise ValueError('Duplicate morph vertex')
        if not np.isfinite(delta).all():raise ValueError('Nonfinite morph deltas')
        out.append(dict(name=name,header=header,ids=np.array(ids,dtype=int),delta=delta,internal=internal))
    if offset!=len(data):raise ValueError('Trailing morph bytes')
    return out


def encode_morphs(chunk, records, linear, internal_linear):
    output=bytearray(struct.pack('<I',len(records)));max_error=0.
    for r in records:
        ids=r['ids']
        if np.any(ids>=len(linear)):raise ValueError('Morph vertex out of range')
        delta=np.einsum('nij,nj->ni',linear[ids],r['delta'])
        name=r['name'].encode('ascii')+b'\0';header=bytearray(r['header'])
        if chunk.version==0x802:
            if len(ids) and np.any(np.diff(ids)<=0):raise ValueError('Unsorted compressed morph vertices')
            lo=delta.min(axis=0) if len(delta) else np.zeros(3)
            extent=(delta.max(axis=0)-lo) if len(delta) else np.zeros(3)
            q=np.rint((delta-lo)/np.where(extent>0,extent,1)*255).clip(0,255).astype('u1')
            if len(delta):max_error=max(max_error,float(np.abs(lo+q*extent/255-delta).max()))
            runs=[]
            for value in ids:
                if runs and runs[-1][1]==value:runs[-1][1]=int(value)+1
                else:runs.append([int(value),int(value)+1])
            if runs and runs[-1][1]>65535:raise ValueError('Morph exceeds uint16 run range')
            struct.pack_into('<6fI',header,16,*lo,*extent,len(runs))
            output.extend(header+name)
            output.extend(b''.join(struct.pack('<2H',*run) for run in runs));output.extend(q.tobytes())
        else:
            output.extend(header+name)
            for index,x,y,z in r['internal']:
                if index>=len(internal_linear):raise ValueError('Internal morph vertex out of range')
                d=internal_linear[index]@np.array([x,y,z]);output.extend(struct.pack('<I3f',index,*d))
            for index,d in zip(ids,delta):output.extend(struct.pack('<I3f',int(index),*d))
    return bytes(output),max_error


def upgrade_skin(blob, target_skeleton, *, variant=None, clear_hiding=True, bone_mapping=None,
                 discard_unused_variants=False, morph_weights=None, geometry_mode='preserve', geometry_field=None):
    """Return upgraded bytes and evidence; source buffers are never modified.

    ``variant`` is the authored source garment shape, baked once before reposing.
    ``clear_hiding`` removes legacy alpha membership, preserving RGB features.
    Native region/layer assembly owns covered-body visibility. Unmatched helper
    bones retain their local transform relative to their converted parent.

    ``preserve`` keeps the source model-space shape and rebinds its joint palette.
    This keeps the original head, torso and clothing cuts together. ``repose``
    additionally moves the geometry by the target/source bind transform blend;
    this is useful for explicit pose conversion, but can separate independently
    weighted clothing and body seams when the target has different proportions.
    ``geometry_field`` instead uses one anatomical field shared by all parts;
    its Jacobian updates normals, tangents and retained morph deltas together.
    """
    if geometry_mode not in ('preserve','repose'):raise ValueError('Unknown geometry mode')
    if geometry_field is not None and geometry_mode != 'preserve':
        raise ValueError('Shared geometry field cannot be combined with weighted reposing')
    skin=CompiledSkin(blob);source=skin.info['bones'];target_chunks=read_chunks(target_skeleton)
    target=bone_table(target_chunks);native={b['name']:b for b in target}
    if not target:raise ValueError('Missing native skeleton')
    mapping=spine_mapping(source,target) if bone_mapping is None else dict(bone_mapping)
    native_case={name.casefold():name for name in native}
    if len(native_case)!=len(native):raise ValueError('Case-ambiguous native bone names')
    for bone in source:
        original=bone['name'];requested=mapping.get(original,original)
        canonical=native_case.get(requested.casefold(),requested)
        if canonical!=original:mapping[original]=canonical
    names=[mapping.get(b['name'],b['name']) for b in source]
    if len(names)!=len(set(names)):raise ValueError('Bone mapping collision')
    if any(v not in native for v in mapping.values()):raise ValueError('Bone mapping target is missing')
    old=[matrix(b['bind']) for b in source];new=[];inverses=[];deltas=[]
    for i,(b,name) in enumerate(zip(source,names)):
        if name in native:n=matrix(native[name]['bind'])
        elif b['parent_index'] is None:n=old[i]
        else:
            parent=b['parent_index']
            if parent>=i:raise ValueError('Expected parent before child')
            n=new[parent]@np.linalg.inv(old[parent])@old[i]
        # Identity transfer must preserve the original male geometry exactly.
        d=n@np.linalg.inv(old[i]) if geometry_mode=='repose' else np.eye(4)
        if np.allclose(d,np.eye(4),rtol=0,atol=1e-5):d=np.eye(4)
        new.append(n);inverses.append(np.linalg.inv(n));deltas.append(d)
    transforms=np.asarray(deltas)[skin.ids]
    blended=np.einsum('nk,nkij->nij',skin.weights,transforms)
    linear=blended[:,:3,:3]
    if np.any(np.abs(np.linalg.det(linear))<1e-5):raise ValueError('Degenerate blended bind transform')
    vertices=skin.vertices.copy();replacements={};chunks=list(skin.chunks)
    morph_chunks=[c for c in chunks if c.kind==0x2002]
    morphs=read_morphs(morph_chunks[0]) if morph_chunks else []
    shapes=dict(morph_weights or {})
    if variant:
        if variant in shapes:raise ValueError('Variant specified twice')
        shapes[variant]=1.
    for name,weight in shapes.items():
        if not math.isfinite(weight):raise ValueError('Nonfinite morph weight')
        selected=[m for m in morphs if m['name']==name]
        if len(selected)!=1:raise ValueError('Requested garment variant missing: '+name)
        m=selected[0]
        if np.any(m['ids']>=skin.n):raise ValueError('Variant vertex out of range')
        vertices[m['ids']]+=m['delta']*weight
    updated=np.einsum('nij,nj->ni',linear,vertices)+blended[:,:3,3]
    if geometry_field is not None:
        updated, linear = geometry_field.transform(vertices)
    moved=np.linalg.norm(updated-skin.vertices,axis=1)
    geometry_changed=bool(np.any(moved>1e-7))
    # Repose render and internal geometry as a pair, retaining influence records.
    internal=bytearray(skin.internal.data);internal_linear=np.broadcast_to(np.eye(3),(skin.ninternal,3,3)).copy()
    seen={}
    for i,idx in enumerate(skin.remap):
        idx=int(idx)
        if idx in seen and (not np.allclose(updated[i],updated[seen[idx]],rtol=0,atol=1e-5)
                            or not np.allclose(linear[i],linear[seen[idx]],rtol=0,atol=1e-5)):
            raise ValueError('Conversion would tear a shared internal vertex')
        seen[idx]=i;internal_linear[idx]=linear[i]
        if geometry_changed:struct.pack_into('<3f',internal,32+64*idx+12,*updated[i])
    if geometry_changed:
        replacements[skin.streams[0].id]=skin.streams[0].data[:24]+updated.astype('<f4').tobytes()
        replacements[skin.internal.id]=bytes(internal)
        def direction(vectors,normal=False):
            if normal:out=np.einsum('nji,nj->ni',np.linalg.inv(linear),vectors)
            else:out=np.einsum('nij,nj->ni',linear,vectors)
            return out/np.maximum(np.linalg.norm(out,axis=1,keepdims=True),1e-12)
        if 1 in skin.streams:
            c=skin.streams[1]
            if struct.unpack_from('<I',c.data,12)[0]!=12:raise ValueError('Unsupported normal stream')
            normals=np.frombuffer(c.data[24:],dtype='<f4').reshape(-1,3)
            replacements[c.id]=c.data[:24]+direction(normals,True).astype('<f4').tobytes()
        if 6 in skin.streams:
            c=skin.streams[6]
            if struct.unpack_from('<I',c.data,12)[0]!=16:raise ValueError('Unsupported tangent stream')
            packed=np.frombuffer(c.data[24:],dtype='<i2').reshape(-1,8).copy()
            for start in (0,4):packed[:,start:start+3]=np.rint(direction(packed[:,start:start+3]/32767)*32767).clip(-32767,32767)
            replacements[c.id]=c.data[:24]+packed.astype('<i2').tobytes()
        if 7 in skin.streams:raise ValueError('Quaternion tangents require explicit conversion')
        mesh=bytearray(skin.mesh.data)
        struct.pack_into('<6f',mesh,108,*updated.min(axis=0),*updated.max(axis=0));replacements[skin.mesh.id]=bytes(mesh)
        subsets=skin.one(0x1017);payload=bytearray(subsets.data)
        for j,s in enumerate(skin.info['subset_records']):
            indices=skin.faces.reshape(-1)[s['first_index']:s['first_index']+s['num_indices']]
            points=updated[indices];centre=(points.min(axis=0)+points.max(axis=0))/2
            radius=np.linalg.norm(points-centre,axis=1).max()
            struct.pack_into('<4f',payload,16+j*36+20,float(radius),*centre)
        replacements[subsets.id]=bytes(payload)
        for box in chunks:
            if box.kind!=0x3004:continue
            if box.version!=0x801 or len(box.data)<32:raise ValueError('Unsupported bone bounds')
            n=struct.unpack_from('<I',box.data,28)[0]
            if len(box.data)!=32+n*2:raise ValueError('Malformed bone bounds')
            ids=np.frombuffer(box.data[32:],dtype='<u2')
            if np.any(ids>=skin.n):raise ValueError('Invalid bone bound member')
            if len(ids):
                p=bytearray(box.data);struct.pack_into('<6f',p,4,*updated[ids].min(axis=0),*updated[ids].max(axis=0));replacements[box.id]=bytes(p)
    quantization_error=0.
    if morph_chunks:
        c=morph_chunks[0]
        if shapes or discard_unused_variants:replacements[c.id]=struct.pack('<I',0)
        elif not np.allclose(linear,np.eye(3),rtol=0,atol=1e-7):
            replacements[c.id],quantization_error=encode_morphs(c,morphs,linear,internal_linear)
    # Bone indices/hierarchy are retained; renamed axial joints stay one-to-one.
    payload=bytearray(skin.bones.data);native_chunk=next(c for c in target_chunks if c.kind==0x2000)
    renamed_controllers={}
    for i,(b,name,n,inv) in enumerate(zip(source,names,new,inverses)):
        o=32+584*i
        struct.pack_into('<24f',payload,o+216,*inv[:3].ravel(),*n[:3].ravel())
        if name!=b['name']:
            target_i=native[name]['index'];controller=native_chunk.data[32+584*target_i:36+584*target_i]
            renamed_controllers[bytes(payload[o:o+4])]=controller
            payload[o:o+4]=controller;payload[o+312:o+568]=name.encode('ascii').ljust(256,b'\0')
    replacements[skin.bones.id]=bytes(payload)
    for c in chunks:
        if c.kind!=0x2001 or not renamed_controllers:continue
        if c.version!=0x800 or (len(c.data)-32)%152:raise ValueError('Unsupported physical bones')
        p=bytearray(c.data)
        for o in range(32,len(p),152):p[o+12:o+16]=renamed_controllers.get(bytes(p[o+12:o+16]),bytes(p[o+12:o+16]))
        replacements[c.id]=bytes(p)
    if clear_hiding:
        c=skin.streams.get(3)
        if c:
            if struct.unpack_from('<I',c.data,12)[0]!=4:raise ValueError('Expected RGBA vertex colours')
            p=bytearray(c.data);p[27::4]=b'\xff'*skin.n;replacements[c.id]=bytes(p)
        else:
            ident=max(c.id for c in chunks)+1
            chunks.append(Chunk(0x1016,0x800,ident,struct.pack('<6I',0,3,skin.n,4,0,0)+b'\xff\xff\xff\xff'*skin.n))
            mesh=bytearray(replacements.get(skin.mesh.id,skin.mesh.data));struct.pack_into('<I',mesh,40,ident);replacements[skin.mesh.id]=bytes(mesh)
        # Internal colour bytes mirror render alpha; source RGB is preserved.
        p=bytearray(replacements.get(skin.internal.id,skin.internal.data))
        for i in range(skin.ninternal):p[32+64*i+63]=255
        replacements[skin.internal.id]=bytes(p)
    output=write_chunks(chunks,replacements);checked=CompiledSkin(output)
    if not np.array_equal(checked.ids,skin.ids) or not np.array_equal(checked.weights,skin.weights):
        raise AssertionError('Bind conversion changed skin influences')
    if not np.array_equal(checked.faces,skin.faces):raise AssertionError('Bind conversion changed topology')
    return output,dict(vertices=skin.n,triangles=len(skin.faces),variant=variant,baked_morphs=shapes,
        geometry_mode='anatomical_landmarks' if geometry_field is not None else geometry_mode,
        anatomical_landmarks=len(geometry_field.source) if geometry_field is not None else 0,
        bone_mapping=mapping,extra_influenced_vertices=skin.info['extra_influenced_vertices'],
        moved_vertices=int(np.count_nonzero(moved>1e-7)),maximum_displacement_mm=float(moved.max()*1000),
        morph_quantization_error_mm=quantization_error*1000,
        preserved_morphs=0 if shapes or discard_unused_variants else len(morphs),
        legacy_hiding_disabled=clear_hiding,geometry_and_weights_checked=True)


def canonicalize_skin_skeletons(blobs):
    """Unify bone palettes by name before combining different source garments."""
    skins=[CompiledSkin(b) for b in blobs];records={};parents={};rank={};required_by_skin=[];used_records=set()
    # The engine resolves joint/controller names case-insensitively. Boots and
    # dresses can spell the same face helper FV_* / fv_*; two such entries in
    # the combined palette cause a fatal duplicated-CRC error at load time.
    canonical={}
    for skin in skins:
        for b in skin.info['bones']:canonical.setdefault(b['name'].casefold(),b['name'])
    for skin in skins:
        for b in skin.info['bones']:
            b['name']=canonical[b['name'].casefold()]
            if b['parent'] is not None:b['parent']=canonical[b['parent'].casefold()]
    for skin in skins:
        bones=skin.info['bones']
        required=set()
        for index in np.unique(skin.ids[skin.weights>0]):
            while index is not None:
                b=bones[int(index)];required.add(b['name']);index=b['parent_index']
        required_by_skin.append(required)
    for skin,required in zip(skins,required_by_skin):
        for b in skin.info['bones']:
            name=b['name'];o=32+584*b['index'];record=bytearray(skin.bones.data[o:o+584])
            record[312:568]=name.encode('ascii').ljust(256,b'\0');record=bytes(record)
            if name in records:
                if name in required and name in used_records and parents[name]!=b['parent']:raise ValueError('Conflicting bone parent: '+name)
                if name in required and name in used_records and not np.allclose(np.frombuffer(records[name][216:312],dtype='<f4'),np.frombuffer(record[216:312],dtype='<f4'),rtol=0,atol=1e-5):
                    raise ValueError('Conflicting bind pose: '+name)
                if name in required and name not in used_records:
                    # Boots may contain stale, unused shoulder-plate joints.
                    # A garment that actually weights that joint defines its
                    # bind pose; two weighted owners must still agree.
                    records[name]=record;parents[name]=b['parent']
            else:records[name]=record;parents[name]=b['parent'];rank[name]=len(rank)
            if name in required:used_records.add(name)
    children=defaultdict(list)
    for name,parent in parents.items():children[parent].append(name)
    if len(children[None])!=1:raise ValueError('Expected one skin root')
    order=list(children[None]);cursor=0
    while cursor<len(order):
        order.extend(sorted(children[order[cursor]],key=rank.get));cursor+=1
    if len(order)!=len(records) or len(set(order))!=len(order):raise ValueError('Invalid canonical skeleton')
    controllers=[records[name][:4] for name in order]
    if len(controllers)!=len(set(controllers)):raise ValueError('Different joints have the same controller CRC')
    indices={name:i for i,name in enumerate(order)};table=bytearray(32)
    for name in order:
        p=bytearray(records[name]);i=indices[name];kids=children[name]
        struct.pack_into('<iIi',p,572,indices[parents[name]]-i if parents[name] else 0,
                         len(kids),min(indices[k] for k in kids)-i if kids else 0)
        table.extend(p)
    outputs=[]
    for skin in skins:
        mapping=np.array([indices[b['name']] for b in skin.info['bones']],dtype='<u2');replacements={skin.bones.id:bytes(table)}
        stream=skin.streams[9];p=bytearray(stream.data)
        for o in range(24,len(p),12):
            ids=struct.unpack_from('<4H',p,o)
            struct.pack_into('<4H',p,o,*(int(mapping[i]) for i in ids))
        replacements[stream.id]=bytes(p)
        p=bytearray(skin.internal.data)
        for o in range(32,len(p),64):
            ids=struct.unpack_from('<4H',p,o+36)
            if any(i>=len(mapping) for i in ids):raise ValueError('Internal bone outside palette')
            struct.pack_into('<4H',p,o+36,*(int(mapping[i]) for i in ids))
        replacements[skin.internal.id]=bytes(p)
        for c in skin.chunks:
            if c.kind==0x3004:
                p=bytearray(c.data);joint=struct.unpack_from('<I',p)[0];struct.pack_into('<I',p,0,int(mapping[joint]));replacements[c.id]=bytes(p)
            elif c.kind==0x2001:
                if c.version!=0x800 or (len(c.data)-32)%152:raise ValueError('Unsupported physical bones')
                p=bytearray(c.data)
                for o in range(32,len(p),152):
                    for field in (0,4):
                        value=struct.unpack_from('<i',p,o+field)[0]
                        if value>=0:
                            if value>=len(mapping):raise ValueError('Physical bone outside palette')
                            struct.pack_into('<i',p,o+field,int(mapping[value]))
                replacements[c.id]=bytes(p)
        blob=write_chunks(skin.chunks,replacements);CompiledSkin(blob);outputs.append(blob)
    return outputs


def limit_skin_influences(blob, maximum=4):
    """Pack a source skin for the native four-weight vertex shader path.

    Retain the strongest influences, then normalize with largest-remainder
    quantization so every vertex sums to exactly 255. Never just truncate the
    extra stream: non-unit weights collapse linear-skinned vertices towards the
    model origin. The internal vertices receive the same palette and weights.
    Geometry, UVs, materials, morphs and retained joint names are untouched.
    """
    if maximum!=4:raise ValueError('Only the native four-influence format is supported')
    skin=CompiledSkin(blob)
    if skin.ids.shape[1]==4:
        return blob,dict(source_slots=4,target_slots=4,changed_vertices=0,maximum_discarded_weight=0.,weights_sum_to_255=True)
    source_weights=np.rint(skin.weights*255).astype(np.int64)
    # An exporter may repeat a joint in both blocks. Combine those entries
    # before ranking, so a duplicate cannot waste a native influence slot.
    for row,(joints,values) in enumerate(zip(skin.ids,source_weights)):
        first={}
        for column,(joint,weight) in enumerate(zip(joints,values)):
            if not weight:continue
            if int(joint) in first:
                source_weights[row,first[int(joint)]]+=weight
                source_weights[row,column]=0
            else:first[int(joint)]=column
    order=np.argsort(-source_weights,axis=1,kind='stable')[:,:maximum]
    ids=np.take_along_axis(skin.ids,order,axis=1)
    selected=np.take_along_axis(source_weights,order,axis=1)
    total=selected.sum(axis=1)
    if np.any(total<=0):raise ValueError('Vertex has no retained influences')
    scaled=selected*255/total[:,None]
    weights=np.floor(scaled).astype(np.int64)
    remainder=255-weights.sum(axis=1)
    fractions=np.argsort(-(scaled-weights),axis=1,kind='stable')
    for i in range(maximum):
        rows=np.flatnonzero(remainder>i)
        weights[rows,fractions[rows,i]]+=1
    payload=bytearray(struct.pack('<6I',0,9,skin.n,12,0,0))
    for joints,values in zip(ids,weights):payload.extend(struct.pack('<4H4B',*joints,*values))
    internal=bytearray(skin.internal.data);written={}
    for i,index in enumerate(skin.remap):
        value=struct.pack('<4H4f',*ids[i],*(weights[i]/255))
        index=int(index)
        if index in written and written[index]!=value:
            raise ValueError('Shared internal vertex has inconsistent influences')
        written[index]=value
        internal[32+64*index+36:32+64*index+60]=value
    mesh=bytearray(skin.mesh.data)
    flags=struct.unpack_from('<I',mesh)[0]
    struct.pack_into('<I',mesh,0,flags&~4)
    output=write_chunks(skin.chunks,{skin.streams[9].id:bytes(payload),
                                   skin.internal.id:bytes(internal),skin.mesh.id:bytes(mesh)})
    checked=CompiledSkin(output)
    if checked.ids.shape[1]!=4 or not np.array_equal(checked.vertices,skin.vertices):
        raise AssertionError('Invalid native influence conversion')
    return output,dict(source_slots=8,target_slots=4,
        changed_vertices=int(np.count_nonzero(total<255)),
        maximum_discarded_weight=float((255-total).max()/255),
        weights_sum_to_255=True)
