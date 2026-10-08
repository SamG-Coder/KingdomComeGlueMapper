"""Stage and optionally install a reversible upgrade of registered NPC packages.

Gender is read from each source body, not an NPC name. Existing materials,
effects, item IDs, inventory definitions and unrelated table records survive.
Only the requested component trees, config/preset rows and appearance rules
may change. Installation rejects stale inputs and retains exact backups.
"""
import argparse
import copy
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from audit_character_bodies import Assets
from character_skin_upgrade import upgrade_skin, canonicalize_skin_skeletons, CompiledSkin, matrix, read_morphs, limit_skin_influences
from clothing_assembly import convert_fixed_outfit
from clothing_regions import assign_body_regions, assign_upper_garment_regions, split_skin_regions
from merge_population_package import signature
from upgrade_map import xml


TABLES=dict(components='Libs/Tables/Character/CharacterComponent.xml',
            config='Libs/Tables/Character/ClothingConfig.xml',
            presets='Libs/Tables/item/clothing_preset.xml',
            items='Libs/Tables/item/item__gluenpc.xml',
            appearance='Libs/Storm/appearance/gluemapper.xml')


def digest(data):return hashlib.sha256(data).hexdigest()


def safe_path(root, relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('Path escapes data directory: '+str(relative))
    return path


def unique(root, tag, attribute, value):
    rows=[n for n in root.iter(tag) if n.get(attribute)==value]
    if len(rows)!=1:raise ValueError(f'Expected one {tag} {value}')
    return rows[0]


def covered_source_layers(parts):
    """Read KDC1's authored p(part)/l(layer) asset identifiers, not NPC names.

    A higher source layer on the same part activates the inner garment's
    authored #A_Shrink fit shape. Unlabelled assets require explicit fitting;
    their order in the source inventory is not a layer number.
    """
    layers={}
    for index,part in enumerate(parts):
        match=re.search(r'_p(\d+)_l(\d+)(?:_|\.)',part.get('mesh_path',''),re.I)
        if part['kind']=='cloth' and match:layers[index]=tuple(map(int,match.groups()))
    return {i for i,(part,layer) in layers.items() if any(p==part and l>layer for p,l in layers.values())}


def stage_upgrade(data, native_game, namespaces, revision, output, *, compose_outfit=False,
                  geometry_mode='preserve'):
    if output.exists():raise FileExistsError('Choose a new staging directory')
    if not re.fullmatch(r'[A-Za-z0-9_]+',revision):raise ValueError('Invalid revision')
    if not namespaces or len(namespaces)!=len(set(namespaces)):raise ValueError('Select distinct namespaces')
    if any(not re.fullmatch(r'[A-Za-z0-9_]+',n) for n in namespaces):raise ValueError('Invalid namespace')
    docs={};expected={};prepared={};sources={};people=[]
    def read_input(relative):
        path=safe_path(data,relative);blob=path.read_bytes();sources[relative]=digest(blob);return blob
    for name,relative in TABLES.items():
        blob=read_input(relative);expected[relative]=digest(blob);docs[name]=ET.fromstring(blob)
    before={name:copy.deepcopy(doc) for name,doc in docs.items()}
    allowed={name:set() for name in docs}
    def add_asset(relative,payload):
        if relative in prepared and prepared[relative]!=payload:raise ValueError('Conflicting generated asset')
        if safe_path(data,relative).exists():raise FileExistsError('Upgrade revision already exists: '+relative)
        prepared[relative]=payload;expected[relative]=None
    with ExitStack() as stack:
        native=Assets(native_game,stack)
        for namespace in namespaces:
            prefix='objects/characters/gluenpc/'+namespace+'/'
            report=json.loads(read_input(prefix+'npc-report.json'))
            parts=report['parts'];bodies=[p for p in parts if p['kind']=='body']
            if len(bodies)!=1 or bodies[0].get('gender_id') not in ('1','2'):raise ValueError('Missing source gender')
            sex={'1':'male','2':'female'}[bodies[0]['gender_id']]
            skeleton,provenance=native.get(f'objects/characters/humans/{sex}/skeleton/{sex}.chr')
            component=unique(docs['components'],'Component','Name','Glue_'+namespace)
            if component.get('FilePath','').replace('\\','/').lower()!='gluenpc/'+namespace.lower()+'/':
                raise ValueError('Unexpected component asset root')
            if component.get('Gender','').lower()!=sex:raise ValueError('Component/source gender mismatch')
            config=unique(docs['config'],'ClothingConfig','Name','Glue_'+namespace)
            preset=unique(docs['presets'],'clothing_preset','clothing_preset_name','Glue_'+namespace)
            rule=unique(docs['appearance'],'rule','name','gluemapper_'+namespace+'_appearance')
            allowed['components'].add(id(component));allowed['config'].add(id(config));allowed['presets'].add(id(preset));allowed['appearance'].add(id(rule))
            person=dict(namespace=namespace,npc=report['npc'],gender=sex,rig=provenance[-1],parts=[],composition=None)
            garments=[];cached={};body_triangles=0
            covered=covered_source_layers(parts) if compose_outfit else set()
            for index,part in enumerate(parts):
                nodes=[n for n in component.iter() if n.get('Name')=='Glue_'+namespace+'_'+str(index)]
                if not nodes:
                    if part['kind']=='cloth':continue # Existing assembled outfit is upgraded below.
                    raise ValueError('Missing registered part')
                node=nodes[0];elements=node.find('Elements')
                if elements is None:raise ValueError('Missing part elements')
                part_report=dict(index=index,kind=part['kind'],skins=[])
                original_elements=list(elements.findall('SkinElement'))
                if not original_elements:raise ValueError('No registered skin elements')
                for element_index,e in enumerate(original_elements):
                    model=e.get('Model');material=e.get('Material')
                    if not model or not material:raise ValueError('Explicit model and material required')
                    if model.startswith(revision+'/'):raise ValueError('Revision already selected')
                    source=read_input(prefix+model)
                    # Preserve the exact current material binding, including effects revisions.
                    material_blob=read_input(prefix+material)
                    variant=part.get('morph_target') or None
                    fit={}
                    if index in covered:
                        candidate=CompiledSkin(source)
                        for chunk in candidate.chunks:
                            if chunk.kind==0x2002:
                                fit={m['name']:1. for m in read_morphs(chunk) if m['name'].casefold()=='#a_shrink'}
                    key=(model,variant,part['kind'],tuple(fit.items()),geometry_mode)
                    if key not in cached:
                        cached[key]=upgrade_skin(source,skeleton,variant=variant,
                                                discard_unused_variants=part['kind']=='cloth',morph_weights=fit,
                                                geometry_mode=geometry_mode,
                                                clear_hiding=part['kind'] in ('body','head'))
                    upgraded,evidence=cached[key]
                    upgraded,influences=limit_skin_influences(upgraded)
                    evidence=dict(evidence,native_influences=influences)
                    filename=f'part{index}_{element_index}.skin';destination=revision+'/'+filename
                    add_asset(prefix+destination,upgraded);e.set('Model',destination)
                    part_report['skins'].append(dict(source=model,output=destination,material=material,**evidence))
                    if part['kind']=='body':
                        body_triangles+=evidence['triangles']
                        if sex=='male' and len(original_elements)==1:
                            labels=assign_body_regions(upgraded);splits=split_skin_regions(upgraded,labels)
                            elements.remove(e)
                            for region,blob in splits.items():
                                name=f'body_{region}.skin';add_asset(prefix+revision+'/'+name,blob)
                                a=dict(e.attrib,EquipmentPart=region,Model=revision+'/'+name)
                                ET.SubElement(elements,'SkinElement',a)
                        elif sex=='female' and len(original_elements)==1:e.set('EquipmentPart','torso')
                    elif part['kind']=='cloth':
                        e.set('KeepBodyLayer','true' if sex=='female' else 'false')
                        garments.append(dict(name=part.get('clothing_name',str(index)),skin=upgraded,
                                             material=ET.fromstring(material_blob),region=e.get('EquipmentPart'),
                                             component=node,index=index,underwear=part.get('armor_settings',{}).get('is_underwear','').lower()=='true'))
                person['parts'].append(part_report)
            # An empty Clothing component is not a native underwear candidate.
            # Register authored source underwear with actual layer-8 elements;
            # retain the previous default if the source has no underwear piece.
            source_underwear=[g for g in garments if g['underwear']]
            if source_underwear:
                derived=component.find('DerivedComponents')
                name='Glue_'+namespace+'_source_underwear'
                if any(n.get('Name')==name for n in component.iter()):raise ValueError('Source underwear already registered')
                node=ET.SubElement(derived,'Clothing',Name=name)
                elements=ET.SubElement(node,'Elements')
                for garment in source_underwear:
                    for e in garment['component'].findall('Elements/SkinElement'):
                        attributes=dict(e.attrib,BodyLayerId='8',KeepBodyLayer='true',IsFinalLayer='false')
                        ET.SubElement(elements,'SkinElement',attributes)
                config.set('DefaultUnderwear',name)
                operations=rule.find('operations')
                if operations is None:raise ValueError('Missing appearance operations')
                underwear=operations.find('setUnderwear')
                if underwear is None:underwear=ET.SubElement(operations,'setUnderwear')
                underwear.set('name',name)
                person['source_underwear']=name
            # Preserve prior fixed-outfit materials/features and update its selected skins.
            already_composed=[n for n in component.iter('Clothing') if n.get('Name','').endswith('_outfit')]
            for node in already_composed:
                for i,e in enumerate(node.findall('Elements/SkinElement')):
                    model=e.get('Model');source=read_input(prefix+model)
                    upgraded,evidence=upgrade_skin(source,skeleton,geometry_mode=geometry_mode,clear_hiding=False)
                    upgraded,influences=limit_skin_influences(upgraded)
                    evidence=dict(evidence,native_influences=influences)
                    destination=revision+'/existing_'+str(i)+'_'+Path(model).name
                    add_asset(prefix+destination,upgraded);e.set('Model',destination);e.set('KeepBodyLayer','true')
                    person['parts'].append(dict(kind='existing_outfit',skins=[dict(source=model,output=destination,**evidence)]))
            if compose_outfit and garments and not already_composed:
                # Final outer layers suppress the separate underwear draw. Keep
                # its source surfaces in the composition as well, so exposed
                # neckline/sleeve areas survive. The separate entry is needed
                # when that final outer item is removed.
                torso=[g for g in garments if g['region']=='torso' and not g['underwear']]
                if not torso:raise ValueError('Composition requires an existing torso item')
                # Use the final authored torso garment as the display owner. Its
                # existing item identity, condition and native role are retained.
                # A new zero-value item loses native automatic equipment ranking.
                outer=max(torso,key=lambda g:g['index'])
                owner=unique(docs['items'],'Armor','Clothing',outer['component'].get('Name'))
                item_id=owner.get('Id')
                if item_id not in [n.text for n in preset.findall('Items/Guid')]:
                    raise ValueError('Display owner is absent from the source clothing preset')
                canonical=canonicalize_skin_skeletons([g['skin'] for g in garments])
                bones=CompiledSkin(canonical[0]).info['bones']
                hips=next(b for b in bones if b['name']=='Hips')
                waist_z=float(matrix(hips['bind'])[2,3])
                for g,blob in zip(garments,canonical):
                    g['skin']=blob
                    if g['region']=='torso':g['face_regions']=assign_upper_garment_regions(blob,waist_z=waist_z)
                # Avoid silently losing top-level native effect metadata in a merge.
                for g in garments:
                    if g['material'].find('FeatureSlots') is not None or g['component'].find('Features') is not None:
                        raise ValueError('Compose converted effects through the existing assembled-outfit path')
                assembled=convert_fixed_outfit(garments,prefix+revision+'/outfit_')
                derived=component.find('DerivedComponents')
                if derived is None:raise ValueError('Missing component container')
                aggregate='Glue_'+namespace+'_upgraded_outfit'
                if any(n.get('Name')==aggregate for n in component.iter()):raise ValueError('Outfit upgrade already registered')
                node=ET.SubElement(derived,'Clothing',Name=aggregate,
                    ArmorType=outer['component'].get('ArmorType'),ArmorArchetypeId=outer['component'].get('ArmorArchetypeId'))
                elements=ET.SubElement(node,'Elements')
                for region,value in assembled.items():
                    name='outfit_'+region
                    add_asset(prefix+revision+'/'+name+'.skin',value['skin'])
                    add_asset(prefix+revision+'/'+name+'.mtl',xml(value['material']))
                    ET.SubElement(elements,'SkinElement',EquipmentPart=region,BodyLayerId='9',
                        Model=revision+'/'+name+'.skin',Material=revision+'/'+name+'.mtl',
                        KeepBodyLayer='true' if sex=='female' else 'false',IsFinalLayer='true')
                owner.set('Clothing',aggregate);allowed['items'].add(id(owner))
                person['composition']=dict(component=aggregate,item_id=item_id,
                    display_owner=outer['component'].get('Name'),
                    regions={k:dict(triangles=v['triangles'],sources=v['sources']) for k,v in assembled.items()},
                    source_items_retained=True,source_underwear_separate=bool(source_underwear),
                    limitation='Initial outfit display composition; independent equipment/condition requires recomposition')
            person['body_triangles']=body_triangles;people.append(person)
    # Semantic preservation guard: remove just the authorized edited/new records
    # from copies and require everything else in each table to remain identical.
    for name,doc in docs.items():
        old=before[name];new=copy.deepcopy(doc)
        affected=[]
        for parent in doc.iter():
            for child in parent:
                if id(child) in allowed[name]:
                    field=next((k for k in ('Name','name','Id','clothing_preset_name') if child.get(k)),None)
                    affected.append((child.tag,field,child.get(field)))
        for tree in (old,new):
            for parent in tree.iter():
                for child in list(parent):
                    if any(child.tag==tag and child.get(field)==value for tag,field,value in affected):parent.remove(child)
        if signature(old)!=signature(new):raise AssertionError('Unrelated records changed: '+name)
        prepared[TABLES[name]]=xml(doc)
    output.mkdir(parents=True)
    for relative,payload in prepared.items():
        target=safe_path(output/'Data',relative);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
    plan=dict(schema=1,data_root=str(data.resolve()),revision=revision,geometry_mode=geometry_mode,characters=people,
              sources=sources,files=[dict(path=p,before=expected[p],after=digest(blob)) for p,blob in prepared.items()],
              installed=False,unrelated_table_records_preserved=True,
              validation='Compiled geometry, all influences and table preservation checked; live visual test pending')
    (output/'upgrade-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    return plan


def install_upgrade(output):
    plan=json.loads((output/'upgrade-plan.json').read_text(encoding='utf-8'))
    if plan['installed']:raise ValueError('Upgrade already installed')
    data=Path(plan['data_root']);backup=output/'backup'
    if backup.exists():raise FileExistsError('Backup already exists')
    for relative,expected in plan['sources'].items():
        if digest(safe_path(data,relative).read_bytes())!=expected:raise ValueError('Source changed since staging: '+relative)
    for f in plan['files']:
        target=safe_path(data,f['path']);current=digest(target.read_bytes()) if target.exists() else None
        if current!=f['before']:raise ValueError('Install target changed: '+f['path'])
        if digest(safe_path(output/'Data',f['path']).read_bytes())!=f['after']:raise ValueError('Staged asset changed: '+f['path'])
    backup.mkdir();written=[]
    try:
        for f in plan['files']:
            target=safe_path(data,f['path'])
            if target.exists():
                saved=safe_path(backup,f['path']);saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,saved)
            target.parent.mkdir(parents=True,exist_ok=True)
            written.append(f)
            shutil.copy2(safe_path(output/'Data',f['path']),target)
        for f in written:
            if digest(safe_path(data,f['path']).read_bytes())!=f['after']:raise IOError('Installed hash mismatch')
    except Exception:
        for f in reversed(written):
            target=safe_path(data,f['path'])
            if f['before'] is None:target.unlink()
            else:shutil.copy2(safe_path(backup,f['path']),target)
        raise
    plan['installed']=True;(output/'upgrade-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    return plan


def rollback_upgrade(output):
    plan=json.loads((output/'upgrade-plan.json').read_text(encoding='utf-8'))
    if not plan['installed']:raise ValueError('Upgrade is not installed')
    data=Path(plan['data_root']);backup=output/'backup'
    for f in plan['files']:
        if digest(safe_path(data,f['path']).read_bytes())!=f['after']:
            raise ValueError('Refusing to replace later edits: '+f['path'])
        if f['before'] is not None and digest(safe_path(backup,f['path']).read_bytes())!=f['before']:
            raise ValueError('Backup hash mismatch: '+f['path'])
    for f in reversed(plan['files']):
        target=safe_path(data,f['path'])
        if f['before'] is None:target.unlink()
        else:shutil.copy2(safe_path(backup,f['path']),target)
    plan['installed']=False;plan['rolled_back']=True
    (output/'upgrade-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    return plan


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path);p.add_argument('--native-game',type=Path)
    p.add_argument('--namespace',action='append');p.add_argument('--revision')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--compose-outfit',action='store_true',help='Experimental fixed outfit composition; requires visual fitting checks')
    p.add_argument('--geometry-mode',choices=('preserve','repose'),default=None,
                   help='Keep source body proportions (default), or explicitly repose vertices into native bind space')
    p.add_argument('--install',action='store_true',help='Install an existing validated staging plan with backups')
    p.add_argument('--rollback',action='store_true',help='Restore this installation, refusing to overwrite later edits')
    a=p.parse_args()
    if a.install and a.rollback:p.error('Choose install or rollback')
    if a.install or a.rollback:
        if any((a.data,a.native_game,a.namespace,a.revision,a.compose_outfit,a.geometry_mode)):p.error('Install uses the saved plan only')
        plan=rollback_upgrade(a.output) if a.rollback else install_upgrade(a.output)
    else:
        if not all((a.data,a.native_game,a.namespace,a.revision)):p.error('Staging requires data, native-game, namespace and revision')
        plan=stage_upgrade(a.data,a.native_game,a.namespace,a.revision,a.output,compose_outfit=a.compose_outfit,
                           geometry_mode=a.geometry_mode or 'preserve')
    print(json.dumps(dict(output=str(a.output),characters=[p['namespace'] for p in plan['characters']],
                         files=len(plan['files']),installed=plan['installed'])))


if __name__=='__main__':main()
