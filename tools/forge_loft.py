"""Authored forge-loft layout using native ladder, bed and shared-stash contracts."""
import copy
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as E
import zipfile

from campaign_entity_links import append_links, guid_value
from cooking_pots import rotate, vector
from entity_visuals import quaternion_product as qmul, world_transform
from region_travel import guid
from seating_animations import HELPERS, ANIMATIONS, install
from upgrade_map import read, xml
from room_layout import geometry
from forge_loft_layout import placements

VERSION=3
PREFIX='gm_forge_loft_'
MARKER='gluemapper/forge_loft.json'
FILES=('objects_mission0.xml','waitinglinks.xml','whdata_0','tables/ai/scheduler.xml',
       HELPERS,ANIMATIONS,'terrain/hlods.dat','terrain/hlods.xml')
BRUSH_EDITS=(
    dict(position=(737.5411376953125,3415.341064453125,63.9006462097168),
         bbox=(736.2040405273438,3414.65478515625,63.892799377441406,738.5612182617188,3416.13232421875,64.4964370727539),delta=(-.15,-2.6,0)),
    dict(position=(737.5648193359375,3415.2861328125,64.23966979980469),
         bbox=(736.9780883789062,3414.99267578125,64.12199401855469,737.7734985351562,3415.72900390625,64.76228332519531),delta=(-.15,-2.6,0)),
    dict(position=(738.6668701171875,3412.25537109375,65.07535552978516),
         bbox=(738.3732299804688,3412.15966796875,63.97458267211914,738.8268432617188,3416.291015625,65.47764587402344)),
)


def one(items,label):
    values=list(items)
    if len(values)!=1: raise ValueError('Missing or ambiguous '+label)
    return values[0]


def layout_hash():
    return hashlib.sha256(json.dumps(placements(),sort_keys=True).encode()).hexdigest()


def repair_interactions(files, native):
    """Update authored furniture in place without duplicating beds or inventory."""
    root=E.fromstring(files['objects_mission0.xml'])
    scheduler=E.fromstring(files['tables/ai/scheduler.xml'])
    rows=scheduler.find('Schedulers')
    names={e.get('Name'):e for e in root}
    for p in placements():
        if p['kind']=='chest':
            names[PREFIX+p['name']].set('Rotate',vector(p['rotation']))
            continue
        name=PREFIX+(p['owner']+'_furniture' if p['kind']=='bed' else p['name'])
        node=names[name]; template=geometry(p)
        node.attrib.pop('Geometry',None)
        node.attrib.update(template.attrib)
        node.remove(node.find('Properties'));node.append(template.find('Properties'))
    for role in ('player','mother','father'):
        bed=names[PREFIX+role+'_bed']
        hub=one((r for r in rows if r.get('EntityGuid')==str(guid_value(bed.get('EntityGuid')))),
                'assigned bed scheduler')
        # A copied native hub position belongs to its original town. Let the
        # placed bed supply its location, as the world/rental bed adapters do.
        hub.attrib.pop('Position',None)
        trigger=names[PREFIX+role+'_trigger']
        click=trigger.find('Properties/Click')
        click.set('UseNotOwnerMessage','@ui_use_sit')
        click.set('esActionType','Stance');click.set('sAction','sitting')
    updated=dict(files,**{'objects_mission0.xml':xml(root),'tables/ai/scheduler.xml':xml(scheduler)})
    updated,_=install(updated,native,prefixes=(PREFIX,))
    return updated


def convert(files,native):
    if MARKER in files:
        report=json.loads(files[MARKER])
        if report.get('version')==1 and report.get('layout_sha256')=='b677cda1bf1f3fa5e8eef8b71e8736158fd5cba1b7ddcf08e01e830a1f703def':
            result=repair_interactions(files,native)
            from home_bed_assignment import apply
            result=apply(result)
            report.update(version=VERSION,layout_sha256=layout_hash(),runtime_verified=False)
            result[MARKER]=json.dumps(report,indent=2).encode()
            validate(result)
            return result,report
        if report.get('version') in (2,VERSION) and report.get('layout_sha256') in (
                layout_hash(),'bce6c03c92417e11a2f81cc9e38af67144ea813120088923fdcf31941366cc0d'):
            from home_bed_assignment import apply
            result=apply(repair_interactions(files,native))
            report.update(version=VERSION,layout_sha256=layout_hash(),runtime_verified=False)
            result[MARKER]=json.dumps(report,indent=2).encode()
            validate(result)
            return result,report
        if report.get('version')!=VERSION or report.get('layout_sha256')!=layout_hash():
            raise ValueError('Loft layout changed; rebuild from the unmodified map package')
        validate(files)
        return files,report
    layout=placements()
    root=E.fromstring(files['objects_mission0.xml'])
    names={e.get('Name'):e for e in root}
    if any(n.startswith(PREFIX) for n in names if n): raise ValueError('Unmarked loft layout already exists')
    scheduler=E.fromstring(files['tables/ai/scheduler.xml']);rows=scheduler.find('Schedulers')
    network=[]; next_id=max(int(e.get('EntityId','0')) for e in root)+1
    def clone(original,name,position,rotation=None):
        nonlocal next_id
        if next_id==0x7777:next_id+=1
        if next_id>=65534:raise ValueError('Loft exceeds available static entity IDs')
        e=copy.deepcopy(original)
        for attr in ('ParentId','ParentGuid','Layer','EditorLayer','Prefab'):e.attrib.pop(attr,None)
        e.set('Name',PREFIX+name);e.set('EntityId',str(next_id));next_id+=1
        e.set('EntityGuid',guid('forge_loft/'+name));e.set('Pos',vector(position))
        e.set('Rotate',vector(rotation or (1,0,0,0)));e.set('Scale',original.get('Scale','1,1,1'))
        root.append(e);return e
    def link(source,target,label):
        links=source.find('EntityLinks')
        if links is None:links=E.SubElement(source,'EntityLinks')
        E.SubElement(links,'Link',Name=label,TargetId=target.get('EntityId'),TargetGuid=target.get('EntityGuid'))
        network.append((source.get('EntityGuid'),target.get('EntityGuid'),label))
    ladder=clone(names['gm_ladder_32403'],'ladder',(737.6,3415.8,63.9089),
                 (0,0,math.sin(math.radians(4)),math.cos(math.radians(4))))
    ladder.find('Properties').set('height','3.25')
    ladder.find('Properties').set('soclass_SmartObjectHelpers','Ladder_325')
    animation=E.fromstring(files[ANIMATIONS]);arows=animation.find('SmartObjectAnimationss')
    row=E.SubElement(arows,'SmartObjectAnimations',EntityGuid=str(guid_value(ladder.get('EntityGuid'))),
                     Hash='0',AllowRuntimeAnimValidationFallback='true')
    E.SubElement(row,'SmartObjectTransform',Position=ladder.get('Pos'),Rotation=ladder.get('Rotate'))
    # Match native interaction contracts to each furniture family's mattress height.
    from world_beds import donors
    templates=donors(native)
    player=one((s for s in E.fromstring(files['whdata_0']).findall('SoulList/Souls/Soul') if s.findtext('Player')=='1'),'player soul')
    owners={'player':player.findtext('EntityGuid'),'mother':names['ska_motherOfHenry'].get('EntityGuid'),
            'father':names['ska_fatherOfHenry'].get('EntityGuid')}
    beds=[]
    # Uneven household groups follow the eaves; keep access on the room-facing side.
    for placement in (p for p in layout if p['kind']=='bed'):
        role,pos,q=placement['owner'],placement['position'],placement['rotation']
        donor,trigger,hub,entities=templates[placement['helper_family']]
        if hub.findall('.//S_ActivityLink'):raise ValueError('Bed donor has external scheduler dependencies')
        dp,dq,_=world_transform(donor,entities)
        delta=qmul(q,(dq[0],-dq[1],-dq[2],-dq[3]));mapping={}
        for kind,original in (('bed',donor),('trigger',trigger)):
            op,oq,_=world_transform(original,entities)
            offset=rotate(delta,tuple(op[i]-dp[i] for i in range(3)))
            node=clone(original,role+'_'+kind,tuple(pos[i]+offset[i] for i in range(3)),qmul(delta,oq))
            if original.get('EntityId'):mapping[original.get('EntityId')]=node
        bed=mapping[donor.get('EntityId')];bt=mapping[trigger.get('EntityId')]
        for node in (bed,bt):
            for edge in node.findall('EntityLinks/Link'):
                dest=mapping.get(edge.get('TargetId'))
                if dest is None:raise ValueError('Bed donor has external entity links')
                edge.set('TargetId',dest.get('EntityId'));edge.set('TargetGuid',dest.get('EntityGuid'))
                network.append((node.get('EntityGuid'),dest.get('EntityGuid'),edge.get('Name')))
        bed.find('Properties').set('bOwnedByHome','0')
        clone(geometry(placement),role+'_furniture',pos,q)
        for action in bt.findall('Properties/*'):
            if action.tag in ('Click','Hold'):
                action.set('bCheckOwner','1');action.set('bAllowNoOwner','0')
        click=bt.find('Properties/Click')
        click.set('UseNotOwnerMessage','@ui_use_sit')
        click.set('esActionType','Stance');click.set('sAction','sitting')
        bh=copy.deepcopy(hub);bh.set('EntityGuid',str(guid_value(bed.get('EntityGuid'))))
        bh.attrib.pop('Position',None);rows.append(bh)
        network.append((owners[role],bed.get('EntityGuid'),'owner'))
        link(names['Ska_SA_HomeForge'],bed,'bed')
        if role!='player':
            actor=names['ska_'+('motherOfHenry' if role=='mother' else 'fatherOfHenry')]
            link(actor,bed,'sleepBed');link(bed,actor,'preferedSleeper')
        beds.append(dict(role=role,name=bed.get('Name'),guid=bed.get('EntityGuid'),owner=owners[role],position=pos))
    chest_source=one((e for e in root if e.get('Name')=='gm_player_chest_32407'),'personal chest donor')
    chests=[]
    for placement in (p for p in layout if p['kind']=='chest'):
        name,owner,shared=placement['name'],placement['owner'],placement['shared']
        chest=clone(chest_source,name,placement['position'],placement['rotation'])
        if shared:
            for edge in chest.findall('EntityLinks/Link'):
                if edge.get('Name')!='masterStash':raise ValueError('Unexpected personal chest dependency')
                network.append((chest.get('EntityGuid'),edge.get('TargetGuid'),'masterStash'))
        else:
            chest.remove(chest.find('EntityLinks'))
        network.append((owners[owner],chest.get('EntityGuid'),'owner'))
        chests.append(dict(name=chest.get('Name'),owner=owners[owner],shared=shared))
    for placement in (p for p in layout if p['kind']=='prop'):
        prop=clone(geometry(placement),placement['name'],placement['position'],placement['rotation'])
        prop.set('Scale',vector(placement['scale']))
    result=dict(files,**{'objects_mission0.xml':xml(root),'tables/ai/scheduler.xml':xml(scheduler),
                        'waitinglinks.xml':append_links(files['waitinglinks.xml'],network,allow_untagged=True),ANIMATIONS:xml(animation)})
    result,_=install(result,native,prefixes=(PREFIX,))
    from world_brush_edits import edit
    result['terrain/hlods.dat'],result['terrain/hlods.xml']=edit(files['terrain/hlods.dat'],files['terrain/hlods.xml'],BRUSH_EDITS)
    from home_bed_assignment import apply
    result=apply(result)
    report=dict(version=VERSION,layout_sha256=layout_hash(),beds=beds,ladder=ladder.attrib,chests=chests,
                moved_props=2,removed_prop_ladders=1,decorations=[p['name'] for p in layout if p['kind']=='prop'],runtime_verified=False)
    result[MARKER]=json.dumps(report,indent=2).encode()
    validate(result)
    return result,report


def validate(files):
    root=E.fromstring(files['objects_mission0.xml']);rows=E.fromstring(files['tables/ai/scheduler.xml']).find('Schedulers')
    names={e.get('Name'):e for e in root};ids={e.get('EntityId'):e for e in root if e.get('EntityId')}
    from character_person_activity import validate_scheduler_targets
    validate_scheduler_targets(E.fromstring(files['tables/ai/scheduler.xml']))
    from home_bed_assignment import choices
    for role in ('player','mother','father'):
        bed=one((e for e in root if e.get('Name')==PREFIX+role+'_bed'),'assigned bed')
        hub=one((r for r in rows if r.get('EntityGuid')==str(guid_value(bed.get('EntityGuid')))),'assigned bed scheduler')
        if hub.get('Position') is not None:raise ValueError('Bed scheduler retains a copied position')
        if not hub.findall('.//EffectHeal'):raise ValueError('Bed healing contract missing')
        if bed.find('Properties').get('soclass_SmartObjectHelpers')!='Bed_1Place_High':
            raise ValueError('Bed animation family does not match furniture')
        visual=names[PREFIX+role+'_furniture']
        if visual.get('Pos')!=bed.get('Pos') or visual.get('Rotate')!=bed.get('Rotate'):
            raise ValueError('Bed furniture and helper transform mismatch')
        if visual.find('Properties/Physics').get('bPhysicalize')!='1':
            raise ValueError('Bed furniture lacks collision')
        if visual.get('EntityClass')!='DestroyableObject' or visual.find('Properties').get('ModelSubObject')!='WH_MergeNode007':
            raise ValueError('Furniture must load its mesh before physicalization')
        if visual.find('Properties/Health').get('bInvulnerable')!='1' or visual.find('Properties').get('bExplode')!='0':
            raise ValueError('Static furniture must not accept damage or explode')
        trigger=names[PREFIX+role+'_trigger']
        if trigger.find('Properties/Click').get('UseNotOwnerMessage')!='@ui_use_sit':
            raise ValueError('Bed inherits an unrelated theft label')
        for e in (bed,trigger):
            for edge in e.findall('EntityLinks/Link'):
                target=ids.get(edge.get('TargetId'))
                if target is None or target.get('EntityGuid')!=edge.get('TargetGuid'):
                    raise ValueError('Broken bed/trigger link')
        if role!='player':
            actor=names['ska_'+('motherOfHenry' if role=='mother' else 'fatherOfHenry')]
            if bed.get('Name') not in {c['entity_name'] for c in choices(root,actor)}:
                raise ValueError('Parent has no assigned loft bed')
    personal=names[PREFIX+'personal_chest'];family=names[PREFIX+'family_chest']
    if len(personal.findall("EntityLinks/Link[@Name='masterStash']"))!=1:
        raise ValueError('Personal chest lost shared storage')
    if family.findall("EntityLinks/Link[@Name='masterStash']"):
        raise ValueError('Family chest incorrectly uses player shared storage')
    ladder=one((e for e in root if e.get('Name')==PREFIX+'ladder'),'loft ladder')
    if ladder.get('EntityClass')!='Ladder' or ladder.find('Properties').get('bUsable')!='true':
        raise ValueError('Loft ladder is not usable')
    from seating_animations import collection_hash
    helpers={h.get('HelperSetName'):h for h in E.fromstring(files[HELPERS])}
    animations={a.get('EntityGuid'):a for a in E.fromstring(files[ANIMATIONS]).find('SmartObjectAnimationss')}
    for role in ('player','mother','father'):
        bed=names[PREFIX+role+'_bed']
        row=animations[str(guid_value(bed.get('EntityGuid')))]
        family=bed.find('Properties').get('soclass_SmartObjectHelpers')
        if row.get('Hash')!=collection_hash(helpers[family]):
            raise ValueError('Bed is missing its native animation collection reference')


def stage(mod,target,output):
    from build_horse_trader_test import rewrite,digest
    mod,target,output=map(Path,(mod,target,output))
    if output.exists():raise FileExistsError('Choose a new loft staging folder')
    level=mod/'Data/Levels/kcd1_travel/level.pak';travel=mod/'Data/gluemappertravel.pak'
    with zipfile.ZipFile(level) as z:
        files={n:read(z,n) for n in (*FILES,MARKER) if n in z.namelist()}
    with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as native:
        updated,report=convert(files,native)
    changed={n:b for n,b in updated.items() if files.get(n)!=b}
    if 'terrain/hlods.dat' in changed:
        from lod_ratio_metadata import MARKER as LOD_MARKER
        from lod_ratio_repair import mark_geometry_edit
        with zipfile.ZipFile(level) as archive:
            if LOD_MARKER in archive.namelist():
                marker=json.loads(read(archive,LOD_MARKER))
                with zipfile.ZipFile(level.with_name('terrain.pak')) as terrain:
                    marker=mark_geometry_edit(read(terrain,'terrain/terrain.dat'),
                        files['terrain/hlods.dat'],updated['terrain/hlods.dat'],
                        files['terrain/hlods.xml'],marker,BRUSH_EDITS)
                changed[LOD_MARKER]=json.dumps(marker,indent=2).encode()
    rewrite(level,output/'Data/Levels/kcd1_travel/level.pak',changed)
    modid=E.parse(mod/'mod.manifest').findtext('info/modid')
    mounted={f'Mods/{modid}/Data/Levels/kcd1_travel/{n}':updated[n]
             for n in (ANIMATIONS,'tables/ai/scheduler.xml')}
    rewrite(travel,output/'Data/gluemappertravel.pak',mounted)
    report.update(baseline_sha256=digest(level),travel_baseline_sha256=digest(travel),changed_entries=sorted(changed))
    (output/'report.json').write_text(json.dumps(report,indent=2))
    return report


def upgrade(package,target):
    import tempfile
    from build_recipe import apply_overlay
    package=Path(package)
    with tempfile.TemporaryDirectory(prefix='forge-loft-',dir=package.parent) as tmp:
        out=Path(tmp)/'stage';report=stage(package,target,out)
        # Reports stay outside the installed package.
        overlay=Path(tmp)/'overlay';overlay.mkdir();(out/'Data').rename(overlay/'Data')
        return apply_overlay(package,overlay,consume=True),report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('mod','target','output'):p.add_argument('--'+name,type=Path,required=True)
    print(json.dumps(stage(**vars(p.parse_args())),indent=2))
