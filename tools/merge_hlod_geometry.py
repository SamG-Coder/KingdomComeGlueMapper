"""Bake compound transforms into bounded static render batches.

Groups by the original material binding and stream layout. Keeps UVs, colours,
all subset indices and tangent handedness; does not atlas or invent textures.
"""
from collections import defaultdict
import struct
import numpy as np
from clothing_regions import Chunk,read_chunks,write_chunks


def unit(v):
    length=np.linalg.norm(v,axis=1)
    if np.any(length<1e-10):raise ValueError('Invalid zero tangent or normal')
    return v/length[:,None]


def merge(blob,max_vertices=60000):
    chunks=read_chunks(blob);lookup={c.id:c for c in chunks};out=[];materials={};batches={}
    def append(kind,version,data):
        ident=len(out)+1;out.append(Chunk(kind,version,ident,bytes(data)));return ident
    def stream(kind,data,width):
        return append(0x1016,0x800,struct.pack('<6I',0,kind,len(data)//width,width,0,0)+data)
    def flush(key):
        b=batches.pop(key);attrs={k:np.concatenate(v) for k,v in b['attrs'].items()}
        positions=attrs[0];indices=[];subsets=[];first=0
        for mat,parts in sorted(b['indices'].items()):
            ids=np.concatenate(parts).astype('<u4');indices.append(ids)
            p=positions[np.unique(ids)];center=(p.min(0)+p.max(0))/2;radius=float(np.linalg.norm(p-center,axis=1).max())
            subsets.append(struct.pack('<5I4f',first,len(ids),int(ids.min()),int(ids.max()-ids.min()+1),mat,radius,*center))
            first+=len(ids)
        sub=append(0x1017,0x800,struct.pack('<4I',0,len(subsets),0,0)+b''.join(subsets))
        md=bytearray(264);struct.pack_into('<6I',md,0,8,0,len(positions),first,len(subsets),sub)
        struct.pack_into('<i',md,24,-1)
        for kind,values in attrs.items():
            payload=values.tobytes();width=values.dtype.itemsize*values.shape[1]
            struct.pack_into('<I',md,28+kind*4,stream(kind,payload,width))
        struct.pack_into('<I',md,48,stream(5,np.concatenate(indices).astype('<u4').tobytes(),4))
        struct.pack_into('<6f',md,108,*positions.min(0),*positions.max(0))
        # Recompute in baked coordinates, including nonuniform scale.
        all_ids=np.concatenate(indices).reshape(-1,3)
        p=positions[all_ids]
        areas=np.linalg.norm(np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0]),axis=1)*.5
        positive=areas[areas>=1e-5]
        if not np.isfinite(areas).all() or not np.any(areas>0):raise ValueError('Degenerate merged mesh')
        area=float(np.exp(np.mean(np.log(positive)))) if len(positive) else 1e-5
        struct.pack_into('<f',md,136,max(1e-5,area));mesh=append(0x1000,0x801,md)
        matid=key[0]
        if matid not in materials:
            c=lookup[matid];materials[matid]=append(c.kind,c.version,c.data)
        nd=bytearray(204);nd[:64]=f'batch_{len(out)}'.encode().ljust(64,b'\0')
        struct.pack_into('<4i',nd,64,mesh,-1,0,materials[matid]);struct.pack_into('<16f',nd,84,*np.eye(4).ravel())
        struct.pack_into('<3i',nd,188,-1,-1,-1);append(0x100b,0x824,nd)
    for node in chunks:
        if node.kind!=0x100b:continue
        mid,_,_,matid=struct.unpack_from('<4i',node.data,64);mesh=lookup[mid]
        raw=np.array(struct.unpack_from('<16f',node.data,84)).reshape(4,4)
        linear=raw[:3,:3].T;translation=raw[3,:3]*.01
        streams=struct.unpack_from('<16I',mesh.data,28);attrs={}
        nv=struct.unpack_from('<I',mesh.data,8)[0]
        for kind,ident in enumerate(streams):
            if not ident or kind==5:continue
            c=lookup[ident];_,typ,count,width=struct.unpack_from('<4I',c.data)
            if typ!=kind or count!=nv:raise ValueError('Unsupported vertex stream cardinality')
            data=c.data[24:]
            if kind==15:
                packed=np.frombuffer(data,dtype='u1').reshape(count,16)
                attrs[0]=np.ndarray((count,3),dtype='<f2',buffer=data,strides=(16,2)).astype('<f4')
                attrs[2]=np.ndarray((count,2),dtype='<f2',buffer=data,offset=12,strides=(16,2)).astype('<f4')
                attrs[3]=packed[:,8:12].copy()
            elif kind in (0,1,2,8):
                # WH static meshes use stream 8 for secondary UVs (also used
                # by building_blend_mesh), not a position/deformation vector.
                components={0:3,1:3,2:2,8:2}[kind]
                if width==components*4:attrs[kind]=np.frombuffer(data,dtype='<f4').reshape(count,components).copy()
                elif kind==0 and width==8:attrs[kind]=np.frombuffer(data,dtype='<f2').reshape(count,4)[:,:3].astype('<f4')
                else:raise ValueError(f'Unsupported float stream {kind}/{width}')
            elif kind==6 and width==16:
                attrs[6]=np.frombuffer(data,dtype='<i2').reshape(count,8).copy()
            elif kind==12 and width==8:
                q=np.frombuffer(data,dtype='<i2').reshape(count,4).astype(float)/32767
                q=unit(q);x,y,z,w=q.T
                tangent=np.column_stack((1-2*(y*y+z*z),2*(x*y+z*w),2*(x*z-y*w)))
                bitangent=np.column_stack((2*(x*y-z*w),1-2*(x*x+z*z),2*(y*z+x*w)))
                sign=np.where(w<0,-1,1)
                attrs[6]=np.rint(np.column_stack((tangent,sign,bitangent,sign))*32767).astype('<i2')
            elif kind in (3,4):attrs[kind]=np.frombuffer(data,dtype='u1').reshape(count,width).copy()
            else:raise ValueError(f'Unsupported transformed vertex stream {kind}/{width}')
        attrs[0]=(attrs[0]@linear.T+translation).astype('<f4')
        if not np.isfinite(attrs[0]).all():raise ValueError('Nonfinite baked vertices')
        if 1 in attrs:attrs[1]=unit(attrs[1]@np.linalg.inv(linear)).astype('<f4')
        if 6 in attrs:
            tangents=attrs[6].astype(float)/32767
            tangents[:,:3]=unit(tangents[:,:3]@linear.T)
            tangents[:,4:7]=unit(tangents[:,4:7]@linear.T)
            if np.linalg.det(linear)<0:tangents[:,[3,7]]*=-1
            attrs[6]=np.rint(np.clip(tangents,-1,1)*32767).astype('<i2')
        layout=tuple((k,v.dtype.str,v.shape[1]) for k,v in sorted(attrs.items()));key=(matid,layout)
        if key in batches and batches[key]['count']+nv>max_vertices:flush(key)
        b=batches.setdefault(key,{'attrs':defaultdict(list),'indices':defaultdict(list),'count':0,'triangles':0,'area_log':0.})
        index=lookup[streams[5]];width=struct.unpack_from('<I',index.data,12)[0]
        ids=np.frombuffer(index.data[24:],dtype='<u2' if width==2 else '<u4')
        ns,sid=struct.unpack_from('<2I',mesh.data,16);subset=lookup[sid]
        tri_count=0
        for i in range(ns):
            first,num,_,_,mat=struct.unpack_from('<5I',subset.data,16+i*36)
            selected=ids[first:first+num].astype('<u4')+b['count']
            if np.linalg.det(linear)<0:selected=selected.reshape(-1,3)[:,[0,2,1]].ravel()
            b['indices'][mat].append(selected);tri_count+=num//3
        for k,v in attrs.items():b['attrs'][k].append(v)
        b['count']+=nv;b['triangles']+=tri_count
        scale_area=abs(np.linalg.det(linear))**(2/3)
        b['area_log']+=tri_count*np.log(max(1e-5,struct.unpack_from('<f',mesh.data,136)[0]*scale_area))
    for key in list(batches):flush(key)
    return write_chunks(out,{}),{'merged_render_nodes':sum(c.kind==0x100b for c in out),
                               'source_render_nodes':sum(c.kind==0x100b for c in chunks)}
