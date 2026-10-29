"""Build resumable, audited static-brush surface tiles from a scene plan.

This is an intermediate builder. A plan with pending geometry categories is
not a publishable world replacement. Failed materials/tiles are recorded and
never silently replaced with white surfaces or empty voxel files.
"""
import argparse
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import nullcontext
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import time
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from bounded_pool import available_memory
from geometry_opacity import Assets, transform_triangles
from voxel_surfaces import load_surface, build_tile
from voxel_lighting import validate_pair


class TextureCache(OrderedDict):
    """Bound decoded-texture retention independently of temporary surfaces."""
    def __init__(self,limit=512*1024**2):super().__init__();self.limit=limit
    def __setitem__(self,key,value):
        super().__setitem__(key,value)
        while len(self)>1 and sum(x.nbytes for x in self.values())>self.limit:self.popitem(last=False)
    def __getitem__(self,key):
        value=super().__getitem__(key);self.move_to_end(key);return value


class MeshCache(OrderedDict):
    """Retain decoded shared meshes across tiles within a fixed memory budget."""
    def __init__(self,limit=512*1024**2):super().__init__();self.limit=limit;self.bytes=0
    def __getitem__(self,key):
        value=super().__getitem__(key);self.move_to_end(key);return value
    def __setitem__(self,key,value):
        size=sum(a.nbytes for a in value[1:])
        if key in self:self.bytes-=sum(a.nbytes for a in super().__getitem__(key)[1:])
        super().__setitem__(key,value);self.bytes+=size
        while len(self)>1 and self.bytes>self.limit:
            _,old=self.popitem(last=False);self.bytes-=sum(a.nbytes for a in old[1:])


def code_digest():
    h=hashlib.sha256()
    for name in ('world_surface_tiles.py','voxel_surfaces.py','voxel_raster_fast.py','voxel_raster_cuda.py','voxel_raster_cuda.cu','voxel_optix_shared.h','voxel_optix_device.cu','voxel_optix_host.h','geometry_opacity.py','world_surface_coverage.py'):
        h.update(Path(__file__).with_name(name).read_bytes())
    return h.hexdigest()


def gpu_configuration():
    return {k:os.environ.get(k,default) for k,default in [('GLUEMAPPER_GPU_BATCH','64'),('GLUEMAPPER_GPU_PIPELINE','1'),('GLUEMAPPER_OPTIX_TRIANGLES','0'),('GLUEMAPPER_GPU_ASYNC','0'),('GLUEMAPPER_GPU_GRAPHS','0')]}


def tile_signature(version,asset_digest,plan,job,samples,experimental_base_materials,backend,gpu_config):
    return hashlib.sha256(json.dumps([version,asset_digest,plan['source_packages'],plan.get('vegetation'),job,samples,experimental_base_materials,backend,gpu_config]).encode()).hexdigest()


def _bounded_setting(name,default,low,high):
    value=os.environ.get(name,str(default))
    if not value.isdigit() or not low<=int(value)<=high:raise ValueError(f'{name} must be {low}..{high}')
    return int(value)


GIB=1024**3
# Measured on a 64 GB machine (1,220-tile buildings bake): the setup worker itself holds about
# 10 GB during the bake, a tile process averages about 5 GB, and one preparing a dense tile
# peaked at 17 GB. Two dense tiles at once need room for both beside the worker.
WORKER_RESERVE=12*GIB;TILE_PROCESS=5*GIB;TWO_DENSE_TILES=40*GIB


def tile_workers(workers,available=None):
    """Concurrent tile processes, each running `workers` brick threads (24 threads: 6 tiles x 2 bricks),
    limited by the memory free when the bake starts."""
    default=max(1,min(6,(os.cpu_count() or 1)//(workers+1)))
    free=available_memory() if available is None else available
    if free is not None:default=max(1,min(default,int((free-WORKER_RESERVE)//TILE_PROCESS)))
    return _bounded_setting('GLUEMAPPER_BAKE_TILE_WORKERS',default,1,32)


def gpu_lanes():
    """Tiles rasterized on the GPU at the same time."""
    return _bounded_setting('GLUEMAPPER_GPU_LANES',1,1,4)


def heavy_slots(available=None):
    """Dense tiles held in memory at once. Gathering a dense tile's triangles stages several GB,
    so the limit covers preparation as well as rasterization; one slot unless two fit."""
    free=available_memory() if available is None else available
    return _bounded_setting('GLUEMAPPER_BAKE_HEAVY_TILES',2 if free is None or free>=TWO_DENSE_TILES else 1,1,8)


HEAVY_TRIANGLES=1_000_000  # the hybrid crossover; estimates at or above it take a heavy slot
# Measured on the KCD1 scene (RTX 5090, one tile at a time): OptiX tiles took 2.0 GB more GPU memory
# at 1.0M triangles and 12.3 GB at 18.6M, about 2 GB of textures plus 690 bytes per triangle.
OPTIX_BASE=2*GIB;OPTIX_TRIANGLE=704;VRAM_RESERVE=GIB


def gpu_free_memory():
    """Free memory of the NVIDIA GPU in bytes from the driver's NVML library, or None when unknown.
    With several GPUs the smallest free amount is used, since CUDA may order devices differently."""
    import ctypes
    try:
        nvml=ctypes.CDLL('nvml.dll' if os.name=='nt' else 'libnvidia-ml.so.1')
        # Resolve every entry point before initialization. A partial/older driver
        # must retain the normal try-GPU/fallback path, not abort the whole bake.
        initialize=nvml.nvmlInit_v2;shutdown=nvml.nvmlShutdown
        get_count=nvml.nvmlDeviceGetCount_v2;get_handle=nvml.nvmlDeviceGetHandleByIndex_v2
        get_memory=nvml.nvmlDeviceGetMemoryInfo
    except (OSError,AttributeError):return None
    class Memory(ctypes.Structure):_fields_=[('total',ctypes.c_ulonglong),('free',ctypes.c_ulonglong),('used',ctypes.c_ulonglong)]
    if initialize():return None
    try:
        count=ctypes.c_uint()
        if get_count(ctypes.byref(count)) or not count.value:return None
        free=[]
        for index in range(count.value):
            device=ctypes.c_void_p();memory=Memory()
            if get_handle(index,ctypes.byref(device)) or get_memory(device,ctypes.byref(memory)):return None
            free.append(memory.free)
        return min(free)
    finally:shutdown()


def gpu_budget(lanes,free=None):
    """GPU memory one lane may use, from the memory free when the bake starts; None when unknown."""
    free=gpu_free_memory() if free is None else free
    return None if free is None else max(0,free-VRAM_RESERVE)//lanes


def select_backend(backend,triangles,budget):
    """Backend for one tile and why OptiX was skipped. Dense tiles go to OptiX only when their
    estimated GPU memory fits the lane's budget, so small cards do not fail late and redo the tile."""
    if backend!='auto':return backend,None
    if triangles<1_000_000:return 'compiled',None
    need=OPTIX_BASE+OPTIX_TRIANGLE*triangles
    if budget is not None and need>budget:
        return 'compiled',f'needs about {need/GIB:.1f} GiB of GPU memory; {budget/GIB:.1f} GiB free per GPU lane'
    return 'optix',None


class _TriangleEstimate:
    """Mesh-header triangle counts for a scene; shared by the bake workers and the submitting process."""
    def __init__(self,scene,plan):
        self.scene=Path(scene);self.plan=plan;self.records=json.loads((self.scene/'placements.json').read_text())
        self.vegetation=None;self.mesh_triangles={}
        if plan.get('vegetation'):
            with np.load(self.scene/'vegetation.npz') as data:self.vegetation=(None,data['groups'])

    def estimated_triangles(self,job):
        """Upper bound on a tile's triangles (whole meshes, before clipping), read from array headers only."""
        def count(key):
            if key not in self.mesh_triangles:
                with zipfile.ZipFile(self.scene/'meshes'/(key+'.npz')) as archive,archive.open('triangles.npy') as member:
                    version=np.lib.format.read_magic(member)
                    header=np.lib.format.read_array_header_1_0 if version==(1,0) else np.lib.format.read_array_header_2_0
                    self.mesh_triangles[key]=int(header(member)[0][0])
            return self.mesh_triangles[key]
        total=sum(count(self.records[record_id]['cache_key']) for record_id in job['placements'])
        if self.vegetation is not None:
            groups=self.plan['vegetation']['groups']
            total+=sum(count(groups[str(int(self.vegetation[1][record_id]))]['cache_key']) for record_id in job.get('vegetation',[]))
        return total


def interleaved(dense):
    """Submission order that spreads dense tiles evenly among light ones, each kind keeping plan order.

    Plan order clusters dense tiles (towns), so processes queue behind the heavy slots and idle.
    Results are recorded by plan index, so tile bytes and the report do not depend on this order."""
    key={}
    for group in ([i for i,d in enumerate(dense) if d],[i for i,d in enumerate(dense) if not d]):
        for rank,index in enumerate(group):key[index]=(rank+.5)/len(group)
    return sorted(range(len(dense)),key=lambda index:(key[index],index))


class _TileBaker(_TriangleEstimate):
    """Per-process baking state. Decoded meshes, materials and textures are caches only, so a tile's
    bytes do not depend on which process bakes it or what it baked before."""
    def __init__(self,scene,output,samples,experimental_base_materials,backend,workers,gpu_budget=None,lanes=None,heavy=None):
        self.scene=Path(scene);self.plan=json.loads((self.scene/'plan.json').read_text())
        self.records=json.loads((self.scene/'placements.json').read_text())
        self.assets=Assets(self.plan['pak_root'],self.plan['overrides']);self.texture_cache=TextureCache();self.roots={};self.mesh_cache=MeshCache()
        self.destination=Path(output) if output is not None else self.scene/'tiles'
        self.vegetation=None
        if self.plan.get('vegetation'):  # digest verified once by build()
            with np.load(self.scene/'vegetation.npz') as data:self.vegetation=(data['transforms'],data['groups'])
        self.version=code_digest();self.asset_digest=self.assets.catalog_digest()
        self.samples=samples;self.experimental_base_materials=experimental_base_materials;self.backend=backend
        self.workers=workers;self.gpu_budget=gpu_budget;self.lanes=lanes;self.heavy=heavy;self.mesh_triangles={}

    def bake(self,number,total,job):
        if self.heavy is None or self.estimated_triangles(job)<HEAVY_TRIANGLES:return self._bake(number,total,job)
        with self.heavy:return self._bake(number,total,job)

    def _bake(self,number,total,job):
        scene,plan,records,assets=self.scene,self.plan,self.records,self.assets
        texture_cache,roots,mesh_cache,destination,vegetation=self.texture_cache,self.roots,self.mesh_cache,self.destination,self.vegetation
        version,asset_digest=self.version,self.asset_digest
        samples,experimental_base_materials,backend,workers=self.samples,self.experimental_base_materials,self.backend,self.workers
        origin=job['origin'];stem='_'.join(f'{v:04d}' for v in [*(x+128 for x in origin),256])
        report_path=destination/(stem+'.json'); start=time.time()
        # Include the scene bytes, placement IDs and conversion policy in each
        # resume signature. A changed decoder or instance invalidates the tile.
        gpu_config=gpu_configuration()
        digest=tile_signature(version,asset_digest,plan,job,samples,experimental_base_materials,backend,gpu_config)
        if report_path.exists():
            try:
                old=json.loads(report_path.read_text())
                if old.get('signature')==digest and old.get('status')=='built':
                    idx=(destination/(stem+'.idx')).read_bytes();vox=(destination/(stem+'.vox')).read_bytes()
                    validate_pair(idx,vox)
                    if hashlib.sha256(idx+vox).hexdigest()==old['sha256']:
                        print(f'Tile {number+1}/{total} {stem}: verified existing',flush=True);return old
            except (OSError,ValueError,KeyError):
                print(f'Tile {number+1}/{total} {stem}: incomplete checkpoint; rebuilding',flush=True)
        audit={'tile':stem,'origin':origin,'signature':digest,'status':'failed','placements':len(job['placements']),'requested_backend':backend,'workers':workers}
        audit['gpu_config']=gpu_config
        try:
            triangles=[];uvs=[];ids=[];materials=[];mapping={};material_audit=[]
            def selected_records():
                for record_id in job['placements']:yield records[record_id]
                for record_id in job.get('vegetation',[]):
                    if vegetation is None:raise ValueError('Missing vegetation placement data')
                    matrices,groups=vegetation
                    group=plan['vegetation']['groups'][str(int(groups[record_id]))]
                    yield {'cache_key':group['cache_key'],'transform':matrices[record_id]}
            audit['vegetation_instances']=len(job.get('vegetation',[]))
            for record in selected_records():
                key=record['cache_key']
                if key not in mesh_cache:
                    meta=json.loads((scene/'meshes'/(key+'.json')).read_text())
                    with np.load(scene/'meshes'/(key+'.npz')) as data:
                        mesh_cache[key]=(meta,data['triangles'],data['uv'],data['ids'])
                meta,tris,uv,local_ids=mesh_cache[key]
                world=transform_triangles(tris,record['transform'])
                low=world.min(1); high=world.max(1)
                hit=np.all(high>=origin,axis=1)&np.all(low<np.asarray(origin)+256,axis=1)
                if not hit.any():continue
                remap=np.zeros(len(meta['bindings']),dtype=np.int32)
                for local in np.unique(local_ids[hit]):
                    name,subset=meta['bindings'][local];identity=(name,subset)
                    if identity not in mapping:
                        if name not in roots:
                            roots[name]=ET.fromstring(assets.get(name if name.endswith('.mtl') else name+'.mtl'))
                        root=roots[name];subs=root.find('SubMaterials');selected=subset if subs is not None else 0
                        subs=list(subs) if subs is not None else [root]
                        if selected>=len(subs):raise ValueError('Subset outside material: '+str(identity))
                        try:material,limits=load_surface(subs[selected],assets,texture_cache,experimental_base=experimental_base_materials)
                        except ValueError as e:raise ValueError(str(identity)+': '+str(e)) from e
                        if limits and material.coverage and not experimental_base_materials:
                            raise ValueError('Layered shader requires explicit base-material policy: '+str(identity))
                        mapping[identity]=len(materials);materials.append(material)
                        material_audit.append({'material':name,'subset':subset,'limitations':limits})
                    remap[local]=mapping[identity]
                triangles.append(world[hit]);uvs.append(uv[hit]);ids.append(remap[local_ids[hit]])
            triangles=np.concatenate(triangles) if triangles else np.empty((0,3,3))
            uvs=np.concatenate(uvs) if uvs else np.empty((0,3,2))
            ids=np.concatenate(ids) if ids else np.empty(0,dtype=np.int32)
            audit['preparation_seconds']=time.time()-start
            raster_start=time.perf_counter()
            # Conservative crossover between the measured small house (25k)
            # and dense scene (10.5m). It is a policy, not universal autotuning.
            selected,skipped=select_backend(backend,len(triangles),self.gpu_budget)
            if skipped:audit['gpu_skipped_reason']=skipped
            fallback=None
            try:
                with self.lanes if self.lanes is not None and selected in ('cuda','optix') else nullcontext():
                    idx,vox,validation=build_tile(triangles,uvs,ids,materials,origin,samples=samples,backend=selected,allow_empty=True,workers=workers if selected=='compiled' else 1)
            except (RuntimeError,MemoryError,OSError) as error:
                if backend!='auto' or selected!='optix':raise
                fallback=f'{type(error).__name__}: {error}'
            if fallback is not None:
                # Leave the exception scope first so failed GPU staging arrays
                # can be released before allocating the CPU fallback.
                import gc
                gc.collect();selected='compiled';audit['gpu_fallback_reason']=fallback
                print('OptiX unavailable for '+stem+'; using CPU: '+fallback,flush=True)
                idx,vox,validation=build_tile(triangles,uvs,ids,materials,origin,samples=samples,backend=selected,allow_empty=True,workers=workers)
            audit['backend']=selected
            audit['voxel_seconds']=time.perf_counter()-raster_start
            for suffix,blob in (('.idx',idx),('.vox',vox)):
                temporary=destination/(stem+suffix+'.tmp');temporary.write_bytes(blob);temporary.replace(destination/(stem+suffix))
            audit.update(status='built',triangles=len(triangles),materials=material_audit,validation=validation,
                         empty=not validation['blocks'],sha256=hashlib.sha256(idx+vox).hexdigest())
        except (ValueError,KeyError,FileNotFoundError,RuntimeError,MemoryError) as e:
            audit['error']=str(e)
        audit['seconds']=round(time.time()-start,3)
        temporary=report_path.with_suffix('.json.tmp');temporary.write_text(json.dumps(audit,indent=2)+'\n');temporary.replace(report_path)
        print(f'Tile {number+1}/{total} {stem}: {audit["status"]} ({audit["seconds"]}s) '+audit.get('error',''),flush=True)
        return audit


_BAKER=None


def _start_baker(arguments,lanes,heavy):
    global _BAKER
    _BAKER=_TileBaker(*arguments,lanes=lanes,heavy=heavy)


def _bake_in_worker(task):
    number,total,job=task
    return _BAKER.bake(number,total,job)


def build(scene, *, only_origin=None, samples=2, experimental_base_materials=False, backend='compiled', output=None,workers=1,start_index=1,progress=None,cancelled=lambda:False):
    if os.environ.get('GLUEMAPPER_OPTIX_TRIANGLES')=='1' and output is None:
        raise ValueError('Experimental hardware triangles require an isolated --output directory; boundary parity is not established')
    scene=Path(scene); plan=json.loads((scene/'plan.json').read_text())
    if plan['errors']:
        details='; '.join(f'{name}: {reason}' for name,reason in list(plan['errors'].items())[:3])
        raise ValueError(f"Resolve scene geometry errors before baking: {details}. See {scene/'plan.json'}")
    for name,digest in plan['source_packages'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest:raise ValueError('Scene packages changed; regenerate plan')
    assets=Assets(plan['pak_root'],plan['overrides'])
    destination=Path(output) if output is not None else scene/'tiles';destination.mkdir(parents=True,exist_ok=True)
    if plan.get('vegetation'):
        path=scene/'vegetation.npz'
        if hashlib.sha256(path.read_bytes()).hexdigest()!=plan['vegetation']['sha256']:raise ValueError('Vegetation placement data changed')
    jobs=plan['tiles']
    if not 1<=start_index<=len(jobs):raise ValueError('Start index outside scene plan')
    jobs=jobs[start_index-1:]
    if only_origin is not None:jobs=[j for j in jobs if j['origin']==list(only_origin)]
    if not jobs:raise ValueError('No tiles selected')
    if plan.get('asset_catalog_digest')!=assets.catalog_digest():
        raise ValueError('Asset catalog changed or was not fingerprinted; regenerate scene and landscape plan')
    arguments=(scene,output,samples,experimental_base_materials,backend,workers,gpu_budget(gpu_lanes()) if backend=='auto' else None)
    finished={}

    def summary():
        done=[finished[n] for n in sorted(finished)]
        return done,{'status':('incomplete category coverage; not installable' if plan['pending_categories'] else 'experimental complete-category bake; validation required'),
                     'pending_categories':plan['pending_categories'],'selected_tiles':len(jobs),'start_index':start_index,
                     'total_scene_tiles':len(plan['tiles']),'processed_tiles':len(done),'built':sum(r['status']=='built' for r in done),
                     'failed':[{'tile':r['tile'],'error':r.get('error','Unknown failure')} for r in done if r['status']!='built']}

    def record(number,audit):
        finished[number]=audit
        ((destination if output is not None else scene)/'build-progress.json').write_text(json.dumps(summary()[1],indent=2)+'\n')

    processes=tile_workers(workers) if len(jobs)>1 else 1
    if processes==1:
        baker=_TileBaker(*arguments)
        for number,job in enumerate(jobs):
            if cancelled():raise InterruptedError('Bake cancelled between tiles; completed tiles retained.')
            if progress:progress('Bake lighting tiles',number,len(jobs),'Preparing tile '+str(number+1))
            record(number,baker.bake(number,len(jobs),job))
    else:
        # Tiles are independent: each process keeps its own caches. Dense tiles take a
        # heavy slot for their whole lifetime (bounding memory) and a GPU lane to rasterize.
        if cancelled():raise InterruptedError('Bake cancelled between tiles; completed tiles retained.')
        context=multiprocessing.get_context('spawn')
        slots=heavy_slots();lanes=context.BoundedSemaphore(gpu_lanes());heavy=context.BoundedSemaphore(slots)
        order=range(len(jobs))
        if slots<processes:
            estimate=_TriangleEstimate(scene,plan)
            dense=[]
            for number,job in enumerate(jobs):
                if cancelled():raise InterruptedError('Bake cancelled while planning tile order; completed tiles retained.')
                if progress:progress('Plan lighting tile order',number,len(jobs),f'Checking tile {number+1} of {len(jobs)}')
                dense.append(estimate.estimated_triangles(job)>=HEAVY_TRIANGLES)
            order=interleaved(dense);del estimate
        print(f'Baking {len(jobs)} tiles in {processes} processes ({workers} brick threads each, '
              f'{slots} dense tile(s) in memory, {gpu_lanes()} GPU lane(s))',flush=True)
        with ProcessPoolExecutor(max_workers=processes,mp_context=context,initializer=_start_baker,initargs=(arguments,lanes,heavy)) as pool:
            # Submit one tile per process and refill as tiles finish, checking for
            # cancellation before each start, so a cancelled bake starts no new tile
            # and shutdown only waits for the tiles already running.
            queued=((number,jobs[number]) for number in order);pending={}
            def refill():
                while len(pending)<processes:
                    if cancelled():raise InterruptedError('Bake cancelled between tiles; completed tiles retained.')
                    item=next(queued,None)
                    if item is None:return
                    number,job=item;pending[pool.submit(_bake_in_worker,(number,len(jobs),job))]=number
            try:
                refill()
                while pending:
                    done,_=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
                    for future in done:record(pending.pop(future),future.result())
                    if pending and cancelled():raise InterruptedError('Bake cancelled between tiles; completed tiles retained.')
                    if progress:progress('Bake lighting tiles',len(finished),len(jobs),f'{len(finished)} of {len(jobs)} tiles')
                    refill()
            except BaseException:
                for future in pending:future.cancel()
                raise
    results,final=summary()
    ((destination if output is not None else scene)/'build-progress.json').write_text(json.dumps(final,indent=2)+'\n')
    if progress:progress('Bake lighting tiles',len(jobs),len(jobs),'All tile results recorded')
    return results

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scene',type=Path,required=True)
    p.add_argument('--origin',nargs=3,type=int)
    p.add_argument('--samples',type=int,choices=(1,2,4,8),default=2)
    p.add_argument('--experimental-base-materials',action='store_true')
    p.add_argument('--backend',choices=('compiled','cuda','optix','auto'),default='compiled')
    p.add_argument('--workers',type=int,choices=(1,2,3,4),default=1,help='Concurrent CPU bricks (bounded queue; shared textures)')
    p.add_argument('--start-index',type=int,default=1,help='One-based plan tile to start a separately recorded continuation')
    p.add_argument('--output',type=Path,help='Separate tile/report destination for backend comparisons')
    args=p.parse_args();results=build(args.scene,only_origin=args.origin,samples=args.samples,
                            experimental_base_materials=args.experimental_base_materials,backend=args.backend,output=args.output,workers=args.workers,start_index=args.start_index)
    if any(r['status']!='built' for r in results):raise SystemExit(1)


if __name__=='__main__':main()
