"""Offline surface channels for bounded mode-1 experiments.

Samples converted geometry/materials; does not invoke the native editor or
change installed game data. Native filter equivalence remains a runtime test.
"""
import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zlib

import numpy as np
from PIL import Image

from geometry_opacity import Assets, cgf_triangles, transform_triangles
from voxel_lighting import INDEX_MAGIC, VOXEL_MAGIC, RECORD, ABSENT, validate_pair


def srgb_to_linear(rgb):
    rgb = np.asarray(rgb, dtype=float)
    return np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)


# Match the existing float32 normalization followed by float64 conversion and
# float32 storage exactly, once for every possible decoded byte value.
_LINEAR_BYTES = srgb_to_linear(np.arange(256,dtype=np.float32)/255).astype(np.float32)
_ALPHA_BYTES = np.arange(256,dtype=np.float32)/255


def decode_rgba_bytes(raw):
    try:
        from voxel_raster_fast import decode_rgba_bytes as native_decode
    except ImportError:
        pass  # Keep the reference importer usable without optional Numba.
    else:
        return native_decode(raw,_LINEAR_BYTES,_ALPHA_BYTES)
    rgba=np.empty(raw.shape,dtype=np.float32)
    rgba[...,:3]=_LINEAR_BYTES[raw[...,:3]]
    rgba[...,3]=_ALPHA_BYTES[raw[...,3]]
    return rgba


def _bounded_map(executor, func, jobs, workers):
    """Ordered results with a bounded window of submitted jobs.

    ``Executor.map(..., buffersize=)`` only exists in Python 3.14, and
    ``itertools.batched`` only exists in 3.12 and newer. Slicing a small window
    keeps the same bounded look-ahead on every supported version, and yields
    results in job order even when ``workers`` exceeds the length of a window.
    """
    jobs = list(jobs)
    if workers > 1:
        for start in range(0, len(jobs), workers):
            yield from executor.map(func, jobs[start:start+workers])
    else:
        yield from executor.map(func, jobs)


def diffuse_texture(name, assets):
    """Decode the declared top mip, including CryEngine's numbered DDS chunks."""
    blob = bytearray(assets.get(name))
    if blob[:4] != b'DDS ' or len(blob)<128:
        raise ValueError('Expected DDS diffuse texture: '+name)
    header=128;fmt=None
    if blob[84:88]==b'DX10':
        if len(blob)<148:raise ValueError('Truncated DX10 texture')
        header=148
        fmt, dimension, flags, array, _ = struct.unpack_from('<5I',blob,128)
        if dimension != 3 or flags & 4 or array != 1:
            raise ValueError('Expected a single 2D texture: '+name)
        block = {71:8,72:8,74:16,75:16,77:16,78:16,98:16,99:16}.get(fmt)
    else:
        if struct.unpack_from('<I',blob,112)[0]&0x200200:raise ValueError('Expected 2D DDS')
        block={b'DXT1':8,b'DXT3':16,b'DXT5':16}.get(bytes(blob[84:88]))
    h,w=struct.unpack_from('<II',blob,12)
    if not 0 < h <= 16384 or not 0 < w <= 16384:raise ValueError('Invalid DDS dimensions')
    if block is None:
        bits=struct.unpack_from('<I',blob,88)[0]
        flags=struct.unpack_from('<I',blob,80)[0]
        if header==128 and flags&0x40 and bits in (24,32):
            size=w*h*(bits//8)
        elif fmt in (28,29):size=w*h*4
        else:raise ValueError('Unsupported diffuse compression: '+str(fmt or bytes(blob[84:88])))
    else:size=((w+3)//4)*((h+3)//4)*block
    if len(blob)-header >= size:
        payload=blob[header:header+size]
    else:
        candidates=[]
        for suffix in range(1,16):
            key=name+'.'+str(suffix)
            if key in assets.entries:
                part=assets.get(key)
                if len(part)==size:candidates.append(part)
        if len(candidates)!=1:raise ValueError('Missing/ambiguous top mip: '+name)
        payload=candidates[0]
    struct.pack_into('<I',blob,28,1)
    # Pillow lacks sRGB BC1/2/3 enum aliases; their compression is identical.
    if fmt in (72,75,78):struct.pack_into('<I',blob,128,fmt-1)
    with Image.open(io.BytesIO(blob[:header]+payload)) as image:
        return decode_rgba_bytes(np.asarray(image.convert('RGBA')))


@dataclass
class Surface:
    texture: np.ndarray
    diffuse: np.ndarray
    opacity: float = 1.
    alpha_test: float = 0.
    coverage: float = 1.
    tile: tuple = (1., 1.)
    offset: tuple = (0., 0.)
    repeat: tuple = (True, True)

    def __post_init__(self):
        self.texture = np.asarray(self.texture)
        self.diffuse = np.asarray(self.diffuse, dtype=float)
        values = np.r_[self.diffuse, self.opacity, self.alpha_test, self.coverage, self.tile, self.offset]
        if self.texture.ndim != 3 or self.texture.shape[2] != 4 or min(self.texture.shape[:2]) < 1:
            raise ValueError('Expected RGBA texture')
        if not np.isfinite(self.texture).all() or np.any((self.texture < 0) | (self.texture > 1)):
            raise ValueError('Invalid texture values')
        if self.diffuse.shape != (3,) or not np.isfinite(values).all():
            raise ValueError('Invalid material values')
        if np.any((self.diffuse < 0) | (self.diffuse > 1)) or not all(0 <= x <= 1 for x in (self.opacity, self.alpha_test, self.coverage)):
            raise ValueError('Material values outside supported range')

    def sample(self, uv):
        """Repeat-addressed bilinear sampling; filter colour in linear space."""
        uv = np.asarray(uv, dtype=float)*self.tile+self.offset
        h, w = self.texture.shape[:2]
        q = uv*np.array([w, h])-.5
        lo = np.floor(q).astype(np.int64)
        frac = q-lo
        rgba = np.zeros((*uv.shape[:-1], 4))
        for dx, dy in ((0,0), (1,0), (0,1), (1,1)):
            weight = (frac[...,0] if dx else 1-frac[...,0])*(frac[...,1] if dy else 1-frac[...,1])
            ix=(lo[...,0]+dx)%w if self.repeat[0] else np.clip(lo[...,0]+dx,0,w-1)
            iy=(lo[...,1]+dy)%h if self.repeat[1] else np.clip(lo[...,1]+dy,0,h-1)
            rgba += self.texture[iy,ix]*weight[...,None]
        alpha = np.where(rgba[...,3] >= self.alpha_test, rgba[...,3]*self.opacity*self.coverage, 0)
        return rgba[...,:3]*self.diffuse, alpha


def load_surface(element, assets, cache, experimental_base=False):
    """Base diffuse/alpha policy. Unsupported shader layers are reported."""
    flags = element.get('StringGenMask','')
    limitations = [s for s in ('BLENDLAYER','SNDUVS','DETAIL_MAPPING','DECAL') if '%'+s in flags]
    if element.get('Shader','Illum').lower() == 'nodraw':
        return Surface(np.ones((1,1,4)),[0,0,0],coverage=0), ['non-rendered material excluded']
    if experimental_base and element.get('Shader','').lower() in ('glass','water','waterfall','geometrybeam'):
        return Surface(np.ones((1,1,4)),[0,0,0],coverage=0), ['transparent/effect shader excluded from static diffuse voxelization']
    if element.get('Shader','Illum') not in ('Illum','Vegetation','Terrain.Layer','Humanskin','Hair','Eye'):
        raise ValueError('Unsupported voxel material shader: '+element.get('Shader',''))
    if element.get('Shader') in ('Humanskin','Hair','Eye','Terrain.Layer'):
        limitations.append('base diffuse approximation for '+element.get('Shader'))
    texture = element.find("Textures/Texture[@Map='Diffuse']")
    tile, offset, repeat = (1.,1.), (0.,0.), (True,True)
    if texture is None:
        rgba = np.ones((1,1,4))
    else:
        repeat=tuple(texture.get('IsTile'+a,'1')!='0' for a in 'UV')
        name = texture.get('File','').replace('\\','/').lower()
        if name not in cache:
            cache[name] = diffuse_texture(name, assets)
        rgba = cache[name]
        mod = texture.find('TexMod')
        if mod is not None:
            permitted = {'TexMod_RotateType','TexMod_TexGenType','TexMod_bTexGenProjected','TileU','TileV','OffsetU','OffsetV'}
            unsupported=any(k not in permitted and float(v) != 0 for k,v in mod.attrib.items())
            generated=any(float(mod.get(k,0)) for k in ('TexMod_RotateType','TexMod_TexGenType','TexMod_bTexGenProjected'))
            if unsupported and not experimental_base:
                raise ValueError('Unsupported texture modifier: '+str(mod.attrib))
            if generated and not experimental_base:
                raise ValueError('Projected/animated UVs require conversion')
            if unsupported or generated:
                limitations.append('unverified UV generation/animation evaluated at base mesh UVs')
            tile = tuple(float(mod.get('Tile'+a,1)) for a in 'UV')
            offset = tuple(float(mod.get('Offset'+a,0)) for a in 'UV')
    params = element.find('PublicParams')
    if params is not None and any(float(params.get(k,0)) != 0 for k in ('Emittance','EmittanceIntensity')):
        raise ValueError('Emissive material needs a verified scalar emission conversion')
    return Surface(rgba, [float(x) for x in element.get('Diffuse','1,1,1').split(',')],
                   float(element.get('Opacity',1)), float(element.get('AlphaTest',0)),
                   float(element.get('VoxelCoverage',255))/255., tile, offset, repeat), limitations


def load_scene(records, assets):
    triangles, uvs, ids, materials, report = [], [], [], [], []
    cache = {}
    for record in records:
        blob = assets.get(record['mesh'])
        tris, info = cgf_triangles(blob, with_surface=True)
        triangles.append(transform_triangles(tris, record['transform']))
        audit = {'mesh':record['mesh'], 'sha256':hashlib.sha256(blob).hexdigest(),
                 'transform':record['transform'], 'materials':[], 'excluded_nodes':info['excluded_nodes']}
        for group in info['surfaces']:
            name = record.get('material') or group['material']
            root = ET.fromstring(assets.get(name if name.endswith('.mtl') else name+'.mtl'))
            subs = root.find('SubMaterials')
            single = subs is None
            subs = list(subs) if subs is not None else [root]
            mapping = {}
            for index in np.unique(group['material_ids']):
                selected=0 if single else index
                if selected >= len(subs):raise ValueError('Subset material out of range')
                material, limitations = load_surface(subs[selected],assets,cache)
                mapping[index] = len(materials)
                materials.append(material)
                audit['materials'].append({'binding':name,'subset':int(index), 'name':subs[selected].get('Name'),
                    'unverified_shader_features':limitations, 'voxel_coverage':material.coverage,
                    'alpha_test':material.alpha_test, 'triangles':int(np.count_nonzero(group['material_ids']==index))})
            uvs.append(group['uv'])
            ids.append(np.array([mapping[i] for i in group['material_ids']]))
        report.append(audit)
    if not triangles:raise ValueError('Empty scene')
    return np.concatenate(triangles), np.concatenate(uvs), np.concatenate(ids), materials, report


def rasterize(triangles, uv, material_ids, materials, origin, shape, cell_size, samples=4):
    """Projected coverage and alpha-weighted surface attributes, [z,y,x,...].

    Overlap uses maximum per-ray alpha; equal-coverage ties retain the first
    surface. This avoids duplicate triangle energy, but is not layered glass.
    Geometry normals retain winding and opposing normals may cancel.
    """
    triangles, uv, origin = (np.asarray(x,dtype=float) for x in (triangles,uv,origin))
    ids = np.asarray(material_ids)
    dimensions = np.asarray(shape)
    if dimensions.shape != (3,) or not np.isfinite(dimensions).all() or np.any(dimensions != np.floor(dimensions)):
        raise ValueError('Expected integer grid dimensions')
    shape = tuple(int(v) for v in dimensions)
    count = int(np.prod(shape))
    if min(shape)<1 or max(shape)>256 or count*samples*samples>16_000_000:
        raise ValueError('Grid/sample allocation exceeds bounded conversion budget')
    if samples not in (1,2,4,8) or not np.isfinite(cell_size) or cell_size<=0:
        raise ValueError('Invalid cell size/sample count')
    if triangles.shape != (len(triangles),3,3) or uv.shape != (len(triangles),3,2) or origin.shape != (3,):
        raise ValueError('Invalid geometry/UV/origin shape')
    if not all(np.isfinite(a).all() for a in (triangles,uv,origin)):
        raise ValueError('Non-finite geometry')
    if ids.shape != (len(triangles),) or not np.issubdtype(ids.dtype,np.integer) or (len(ids) and (ids.min()<0 or ids.max()>=len(materials))):
        raise ValueError('Invalid material indices')
    local = (triangles-origin)/cell_size
    normals = np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    lengths = np.linalg.norm(normals,axis=1)
    normals /= np.maximum(lengths[:,None],1e-30)
    colours = np.zeros((count,3)); normal_sum=np.zeros((count,3)); weights=np.zeros(count)
    opacity=np.zeros((count,3))
    for axis in range(3):
        u,v=[i for i in range(3) if i!=axis]
        alpha_grid=np.zeros((count,samples*samples),dtype=np.float32)
        for ti,triangle in enumerate(local):
            material=materials[ids[ti]]
            if not material.coverage or not material.opacity:continue
            a,b,c=triangle
            det=(b[u]-a[u])*(c[v]-a[v])-(c[u]-a[u])*(b[v]-a[v])
            if abs(det)<1e-12:continue
            low=np.maximum(np.ceil(triangle[:,[u,v]].min(0)*samples-.5).astype(int),0)
            high=np.minimum(np.floor(triangle[:,[u,v]].max(0)*samples-.5).astype(int),np.array([shape[u],shape[v]])*samples-1)
            if np.any(high<low):continue
            for start in range(low[1],high[1]+1,64):
                iu,iv=np.meshgrid(np.arange(low[0],high[0]+1),np.arange(start,min(start+64,high[1]+1)))
                du,dv=(iu+.5)/samples-a[u],(iv+.5)/samples-a[v]
                beta=(du*(c[v]-a[v])-dv*(c[u]-a[u]))/det
                gamma=((b[u]-a[u])*dv-(b[v]-a[v])*du)/det
                depth=a[axis]+beta*(b[axis]-a[axis])+gamma*(c[axis]-a[axis])
                hit=(beta>=-1e-10)&(gamma>=-1e-10)&(beta+gamma<=1+1e-10)&(depth>=0)&(depth<shape[axis])
                if not hit.any():continue
                xyz=[None]*3
                xyz[u],xyz[v],xyz[axis]=iu[hit]//samples,iv[hit]//samples,np.floor(depth[hit]).astype(int)
                flat=(xyz[2]*shape[1]+xyz[1])*shape[0]+xyz[0]
                ray=(iu[hit]%samples)+samples*(iv[hit]%samples)
                st=uv[ti,0]+beta[hit,None]*(uv[ti,1]-uv[ti,0])+gamma[hit,None]*(uv[ti,2]-uv[ti,0])
                rgb,alpha=material.sample(st)
                gain=np.maximum(0,alpha-alpha_grid[flat,ray])
                alpha_grid[flat,ray]+=gain
                np.add.at(colours,flat,rgb*gain[:,None])
                np.add.at(normal_sum,flat,normals[ti]*gain[:,None])
                np.add.at(weights,flat,gain)
        opacity[:,axis]=alpha_grid.mean(axis=1)
    divisor=np.maximum(weights[:,None],1e-30)
    return {'opacity':opacity.reshape(*shape[::-1],3),
            'albedo':(colours/divisor).reshape(*shape[::-1],3),
            'normals':(normal_sum/divisor).reshape(*shape[::-1],3)}


def encode_brick(fields):
    """Experimental mask-7 surface payload; BGRA texture pool convention.

    The sampled native files use planar opacity/albedo/normal arrays. Normal
    alpha and exact coverage filtering still require a matched native fixture.
    """
    if any(np.asarray(fields[k]).shape != (16,16,16,3) for k in ('opacity','albedo','normals')):
        raise ValueError('A brick must contain 16 cubed RGB surface values')
    opacity,albedo,normals=[np.asarray(fields[k]) for k in ('opacity','albedo','normals')]
    # Weighted double-precision averages can exceed a closed interval by a
    # few ULPs. Reject invalid data first; saturate only roundoff at encoding.
    tolerance=32*np.finfo(np.float64).eps
    for name,array,low in (('opacity',opacity,0.),('albedo',albedo,0.),('normals',normals,-1.)):
        if not np.isfinite(array).all() or np.any(array<low-tolerance) or np.any(array>1+tolerance):
            raise ValueError(f'Invalid surface channel {name}: min={np.nanmin(array)!r}, max={np.nanmax(array)!r}')
    opacity=np.clip(opacity,0,1);albedo=np.clip(albedo,0,1);normals=np.clip(normals,-1,1)
    occupied=opacity.max(axis=-1)
    arrays=np.zeros((3,16,16,16,4),np.uint8)
    arrays[0,...,:3]=np.rint(opacity[...,::-1]*255).astype(np.uint8)
    arrays[0,...,3]=np.rint(occupied*255).astype(np.uint8)
    arrays[1,...,:3]=np.rint(albedo[...,::-1]*255).astype(np.uint8)
    arrays[2,...,:3]=((normals[...,::-1]*.5+.5)*255).astype(np.uint8)
    arrays[2,...,3]=arrays[0,...,3]
    return bytes([16,16,16,0])+bytes(8)+struct.pack('<II',7,int(arrays[0,...,3].max()))+arrays.tobytes()


def build_tile(triangles,uv,ids,materials,origin,extent=256,min_edge=8,samples=2,backend='reference',allow_empty=False,workers=1):
    """Rasterize intersecting scene geometry, clipped to this tile's bounds.

    Cross-boundary geometry must be supplied to every intersected tile. The
    rasterizer owns its half-open depth interval, preventing seams/duplicates.
    """
    origin=np.asarray(origin)
    if origin.shape!=(3,) or np.any(origin<0) or np.any(origin!=np.floor(origin)):
        raise ValueError('Native tile origin must contain nonnegative integers')
    if any(not isinstance(x,int) or x<1 or x&(x-1) for x in (extent,min_edge)) or min_edge>extent:
        raise ValueError('Octree edges must be powers of two')
    if backend not in ('reference','compiled','cuda','optix'):raise ValueError('Unknown raster backend')
    if workers not in (1,2,3,4) or (workers!=1 and backend!='compiled'):raise ValueError('Parallel bricks require compiled CPU backend and 1..4 workers')
    if backend in ('compiled','cuda','optix'):
        if backend=='optix':
            from voxel_raster_cuda import prepare_optix as prepare, rasterize as compiled_raster
        elif backend=='cuda':
            from voxel_raster_cuda import prepare, rasterize as compiled_raster
        else:
            from voxel_raster_fast import prepare, rasterize as compiled_raster
        packed=prepare(materials)
        if backend=='optix':packed.set_scene(triangles,uv,ids)
        render=lambda t,u,i,p,e:compiled_raster(t,u,i,packed,p,(16,16,16),e/16,samples)
    else:
        render=lambda t,u,i,p,e:rasterize(t,u,i,materials,p,(16,16,16),e/16,samples)
    bounds_lo,bounds_hi=triangles.min(1),triangles.max(1)
    if backend!='reference':
        from voxel_raster_fast import filter_bounds
    def walk(pos,edge,candidates):
        if backend=='reference':
            hit=candidates[np.all(bounds_hi[candidates]>=pos,axis=1)&np.all(bounds_lo[candidates]<pos+edge,axis=1)]
        else:
            hit=filter_bounds(bounds_lo,bounds_hi,candidates,pos,edge)
        if not len(hit):return
        yield pos,edge,hit
        if edge>min_edge:
            for child in range(8):yield from walk(pos+edge//2*np.array([(child>>2)&1,(child>>1)&1,child&1]),edge//2,hit)
    def render_node(job):
        pos,edge,hit=job
        fields=packed.raster_scene(pos,(16,16,16),edge/16,samples) if backend=='optix' else render(triangles[hit],uv[hit],ids[hit],pos,edge)
        payload=pack_fields(fields)
        return tuple(int(x) for x in pos)+(edge,),payload
    def pack_fields(fields):
        if not fields['opacity'].any():return None
        raw=encode_brick(fields)
        return struct.pack('<I',len(raw))+zlib.compress(raw)
    def batched_nodes(jobs,batch_size):
        from concurrent.futures import ThreadPoolExecutor
        from itertools import islice
        # Discard candidate arrays before queuing batches; only coordinates cross
        # threads. At most one preparation and one encoding job are outstanding.
        coordinates=(tuple(int(v) for v in pos)+(edge,) for pos,edge,_ in jobs)
        def next_batch():return list(islice(coordinates,batch_size))
        def encode_batch(boxes,output):
            return [(box,pack_fields({key:output[n,...,start:start+3] for key,start in [('opacity',0),('albedo',3),('normals',6)]})) for n,box in enumerate(boxes)]
        pipelined=os.environ.get('GLUEMAPPER_GPU_PIPELINE','1')=='1'
        nodes=[]
        if not pipelined:
            while boxes:=next_batch():
                b=np.asarray(boxes,dtype=float);b[:,3]/=16
                nodes.extend(encode_batch(boxes,packed.raster_batch(b,samples)))
            return nodes
        with ThreadPoolExecutor(max_workers=2) as pool:
            prepared=pool.submit(next_batch);encoded=None
            while boxes:=prepared.result():
                prepared=pool.submit(next_batch)
                b=np.asarray(boxes,dtype=float);b[:,3]/=16
                output=packed.raster_batch(b,samples)
                if encoded is not None:nodes.extend(encoded.result())
                encoded=pool.submit(encode_batch,boxes,output)
            if encoded is not None:nodes.extend(encoded.result())
        return nodes
    try:
        jobs=walk(origin,extent,np.arange(len(triangles)))
        batch_size=int(os.environ.get('GLUEMAPPER_GPU_BATCH','64'))
        if not 1<=batch_size<=64:raise ValueError('GPU batch must be 1..64')
        if backend=='optix' and batch_size>1:nodes=batched_nodes(jobs,batch_size)
        elif workers==1:nodes=list(map(render_node,jobs))
        else:
            from concurrent.futures import ThreadPoolExecutor
            # Bound queued geometry instead of materializing the entire world
            # octree. Ordered results preserve native file bytes.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                nodes=list(_bounded_map(pool,render_node,jobs,workers))
    finally:
        if backend in ('cuda','optix'):packed.close()
    if not nodes and allow_empty:nodes.append((tuple(int(v) for v in origin)+(extent,),None))
    nodes[1:]=sorted(nodes[1:],key=lambda n:n[0])
    idx,vox=bytearray(INDEX_MAGIC),bytearray(VOXEL_MAGIC)
    for box,payload in nodes:
        off=size=ABSENT
        if payload is not None:
            off=len(vox);size=len(payload);vox.extend(payload)
        idx.extend(RECORD.pack(*box,1,0,off,size))
    validation=validate_pair(idx,vox)
    if not validation['blocks'] and not allow_empty:raise ValueError('No occupied surface blocks generated')
    return bytes(idx),bytes(vox),validation


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('manifest','pak_root','output'):p.add_argument('--'+n.replace('_','-'),type=Path,required=True)
    p.add_argument('--samples',type=int,choices=(1,2,4,8),default=2)
    p.add_argument('--experimental-base-materials',action='store_true',help='Allow reported unverified layered shader features')
    a=p.parse_args()
    if a.output.exists():p.error('Use a fresh output folder')
    tris,uv,ids,mats,audit=load_scene(json.loads(a.manifest.read_text()),Assets(a.pak_root))
    unresolved=[m for r in audit for m in r['materials'] if m['unverified_shader_features']]
    if unresolved and not a.experimental_base_materials:
        p.error('Unverified layered material features; use explicit experimental option only for inspection')
    origin=np.floor(tris.min((0,1))/256)*256
    idx,vox,check=build_tile(tris,uv,ids,mats,origin.astype(int),samples=a.samples)
    a.output.mkdir(parents=True)
    stem='_'.join(f'{int(x):04d}' for x in (* (origin+128),256))
    (a.output/(stem+'.idx')).write_bytes(idx);(a.output/(stem+'.vox')).write_bytes(vox)
    report={'status':'experimental offline surface conversion; not installed', 'native_filter_parity':False,
      'runtime_verified':False,'channel_layout':'opacity, linear albedo, encoded geometry normals; BGRA',
      'limits':['normal alpha/filter equivalence unverified','maximum-alpha overlap, not layered transmission',
               'base diffuse only for explicitly opted-in layered shaders','house manifest omits surrounding scene'],
      'tile':stem,'validation':check,'triangles':len(tris),'materials':len(mats),'assets':audit}
    (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='assets'},indent=2))


if __name__=='__main__':main()
