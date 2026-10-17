"""Stage the full initial-world Stash family with native inventories and keys."""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from bounded_pool import ordered_map, worker_count
from build_horse_trader_test import digest, rewrite
from campaign_dependency_adapters import StaticAssetAdapter
from container_audit import audit
from container_loot import compile_inventory
from door_keys import build as build_keys
from merchant_stash import stash_assets
from ordinary_containers import register
from region_travel import guid
from upgrade_map import read


def package(output, paths):
    """Keep every asset shard below 1 GiB and avoid ZIP64 game packages."""
    archives=[]; current=None; size=0
    try:
        for name,path in sorted(paths.items()):
            length=path.stat().st_size
            if length > 1024**3-1024**2: raise ValueError('Oversized container resource '+name)
            if current is None or size+length > 1024**3-1024**2:
                if current is not None: current.close()
                dest=output/'Data'/f'gluemapper_containers_{len(archives):02d}.pak'
                dest.parent.mkdir(parents=True,exist_ok=True)
                current=zipfile.ZipFile(dest,'x',zipfile.ZIP_STORED,allowZip64=False)
                archives.append(dest);size=0
            current.write(path,name);size+=length
    finally:
        if current is not None:current.close()
    return [dict(path=str(p.relative_to(output)),sha256=digest(p)) for p in archives]


# Each worker opens the source asset packs for one model; leave room for that.
MODEL_WORKER_BYTES = 512 * 1024**2


def _model_assets(job):
    source,model=job
    prefix='gluebuild/containers/'+hashlib.sha256(model.encode()).hexdigest()[:16]+'/'
    node=ET.Element('Entity');ET.SubElement(node,'Properties',object_Model=model)
    try:
        return prefix,stash_assets(source,node,prefix)
    except (ValueError,KeyError) as error:
        raise ValueError('Container asset '+model+': '+str(error)) from error


def build(source,target,baseline_level,item_output,output):
    if output.exists():raise FileExistsError('Choose a fresh staging output')
    report=audit(source,target)
    dependencies=json.loads((item_output/'report.json').read_text(encoding='utf-8'))
    if dependencies['unresolved_items']:raise ValueError('Resolve all loot item dependencies first')
    registered=dependencies['registered_items']
    with zipfile.ZipFile(baseline_level) as archive:
        files={n:read(archive,n) for n in ('objects_mission0.xml','waitinglinks.xml','whdata_0')}
    output.mkdir(parents=True)
    paths={}; strings=ET.Element('Table'); models={'':''}
    def emit(name,data):
        if name.lower().startswith('localization/english/'):
            strings.extend(ET.fromstring(data.read_bytes() if isinstance(data,Path) else data));return
        dest=output/'converted'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        payload=data.read_bytes() if isinstance(data,Path) else data
        if name in paths and paths[name].read_bytes()!=payload:raise ValueError('Resource collision '+name)
        dest.write_bytes(payload)
        paths[name]=dest
    for p in (item_output/'converted').rglob('*'):
        if p.is_file():
            name=p.relative_to(item_output/'converted').as_posix()
            if name.lower().startswith('localization/english/'):emit(name,p)
            else:paths[name]=p
    # Shared player inventories already have their native master/slave links.
    records=[r for r in report['records'] if 'personal_storage' not in r['protected_roles']]
    keys={r['lock'].get('guidItemClassId','').lower() for r in records}-{ '', '0','00000000-0000-0000-0000-000000000000'}
    with ExitStack() as stack:
        cache=output/'key-cache';cache.mkdir()
        assets=StaticAssetAdapter(stack,source.parent,'containerkeys',cache)
        # Preserve native/previously imported item types. Some unlocked source
        # baskets retain cabbage in their unused key property. It is still food.
        known_keys=set(dependencies['registered']['items'])
        key_files,key_strings,recovered=build_keys(source,keys-known_keys,assets,namespace='containerkeys')
        for name,data in key_files.items():emit(name,data)
        strings.extend(ET.fromstring(key_strings))
    policies={}; preset_names={}
    for identifier,inventory in report['inventories'].items():
        xml,name,policy=compile_inventory(inventory,registered,dependencies['item_names'])
        emit('Libs/Tables/item/InventoryPreset__'+name+'.xml',ET.tostring(xml,encoding='utf-8'))
        preset_names[identifier]=name;policies[identifier]=policy
    required={r['model'] for r in records if r['model']}
    required.update(r['phase'].get('object_ModelAfterInteract','').replace('\\','/').lower()
        for r in records if r['phase'].get('bChangeAfterPlayerInteract')=='1' and r['phase'].get('object_ModelAfterInteract'))
    # Each model opens its own asset pack (2 s); they are independent, so convert
    # them in worker processes and emit in the same sorted order as before.
    ordered=sorted(required)
    workers=worker_count(len(ordered),MODEL_WORKER_BYTES,'GLUEMAPPER_CONTAINER_WORKERS')
    for i,(model,(prefix,assets)) in enumerate(zip(ordered,ordered_map(_model_assets,((source,m) for m in ordered),workers))):
        for name,data in assets.items():emit(name,data)
        models[model]=prefix+Path(model).name
        if i%20==0:print('Container models',i,'/',len(required),flush=True)
    contracts={}
    for record in records:
        identifier=record['inventory_id']
        phase=record['phase'].get('object_ModelAfterInteract','').replace('\\','/').lower()
        contracts[record['source_id']]=dict(policies[identifier],preset=preset_names[identifier],
            registered_keys=keys,phase_model=models.get(phase,''))
    updated,world=register(files,records,models,guid,contracts)
    destination=output/'Data/Levels/kcd1_travel/level.pak'
    rewrite(baseline_level,destination,updated)
    packaged=package(output,paths)
    localized=output/'Localization/English_xml.pak';localized.parent.mkdir(parents=True,exist_ok=True)
    # Distinct file avoids replacing existing mod localisation tables.
    with zipfile.ZipFile(localized,'x',zipfile.ZIP_STORED,allowZip64=False) as archive:
        unique={}
        for row in strings:
            key=row.findtext('Cell')
            if key in unique and ET.tostring(unique[key])!=ET.tostring(row):raise ValueError('Conflicting container localisation '+str(key))
            unique[key]=row
        strings[:]=[unique[k] for k in sorted(unique)]
        archive.writestr('text_ui_container_items.xml',ET.tostring(strings,encoding='utf-8'))
    world.update(source_active_stashes=report['active_stashes'],preserved_shared_player_chests=report['protected_roles'].get('personal_storage',0),
        inventories=len(policies),items=len(registered),keys=len(keys),recovered_source_keys=recovered,
        loot_policies=policies,asset_packages=packaged,baseline_sha256=digest(baseline_level),
        level_sha256=digest(destination),deployment='staged',source_role_counts=report['protected_roles'],
        limitation='Temporary unowned access. NPC/home generation and campaign/service link activation remain separate from physical storage conversion.',
        runtime_tested=False)
    (output/'containers-report.json').write_text(json.dumps(world,indent=2)+'\n',encoding='utf-8')
    (output/'source-audit.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in world.items() if k not in ('records','loot_policies')},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source','target','baseline-level','item-output','output'):p.add_argument('--'+n,type=Path,required=True)
    build(**vars(p.parse_args()))
