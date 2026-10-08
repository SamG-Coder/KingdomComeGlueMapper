"""Register a bounded KCD1 outfit with the native KCD2 clothing manager.

Writes local development XML overlays, preserving every native definition.
Mesh partitioning, morph conversion and hiding masks remain separate gates.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
import zipfile

from upgrade_map import read, xml
from clothing_regions import assign_upper_garment_regions, split_skin_regions
from clothing_assembly import convert_fixed_outfit


# Explicit mappings for the first outfit, from installed native components.
# IDs cannot be copied blindly: KCD1's 67 and 59 changed meaning or disappeared.
OUTFIT = {
    'shirt_001': ('TunicLong', '27', 'torso', '5', False),
    'blacksmith_apron_002': ('Coat', '67', 'torso', '9', False),
    'pants_001_dirty': ('HoseJoined', '47', 'legs', '1', True),
    'pa_gloves_002': ('Gloves', '45', 'hands', '7', True),
    'hat_006': ('Cap', '36', 'head', '7', True),
    'coat_001': ('Coat', '67', 'torso', '4', False),
    'boots_010': ('BootsAnkle', '98', 'feet', '2', True),
}


def native_soul_archetype(archetypes, gender):
    gender_id = {'Male': '1', 'Female': '2'}[gender]
    name = 'NPC_Female' if gender == 'Female' else 'NPC'
    matches = [row for row in archetypes.iter('soul_archetype')
               if row.get('soul_archetype_name') == name and row.get('gender_id') == gender_id]
    if len(matches) != 1 or not matches[0].get('soul_archetype_id'):
        raise ValueError('Missing or ambiguous native NPC soul archetype')
    return matches[0].get('soul_archetype_id')


def registration_profile(report, supplied=None):
    """Require explicit native roles for new outfits; preserve the tested profile."""
    if supplied is None:
        if report.get('npc') != 'ska_fatherOfHenry':
            raise ValueError('Provide a registration profile for this source person')
        supplied = dict(gender='Male', default_body='male_body_npc', underwear='m_underwear01_m01',
                        garments=OUTFIT, assembled_armor_type='Coat', assembled_archetype='67',
                        assembled_layer='9')
    required = ('gender', 'default_body', 'underwear', 'garments', 'assembled_armor_type',
                'assembled_archetype', 'assembled_layer')
    if any(k not in supplied for k in required): raise ValueError('Incomplete registration profile')
    if supplied['gender'] not in ('Male', 'Female'): raise ValueError('Unsupported profile gender')
    bodies = [p for p in report['parts'] if p['kind'] == 'body']
    if len(bodies) != 1: raise ValueError('Expected one source body')
    if bodies[0].get('gender_id') != {'Male': '1', 'Female': '2'}[supplied['gender']]:
        raise ValueError('Profile gender disagrees with source body')
    for part in report['parts']:
        if part['kind'] != 'cloth': continue
        role = supplied['garments'].get(part['clothing_name'])
        if not isinstance(role, (list, tuple)) or len(role) != 5:
            raise ValueError('Missing native clothing role: ' + part['clothing_name'])
        armor_type, archetype, region, layer, final = role
        if not armor_type or not str(archetype).isdigit() or not str(layer).isdigit() or not isinstance(final, bool):
            raise ValueError('Invalid native clothing role')
        if region not in ('torso', 'legs', 'hands', 'head', 'feet', 'face', 'beard'):
            raise ValueError('Unsupported clothing region')
    return supplied


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tools', type=Path, default=Path('D:/SteamLibrary/steamapps/common/KCD2Mod'))
    p.add_argument('--namespace', default='npc_layers_v1')
    p.add_argument('--assemble-outfit', action='store_true',
                   help='Bake fixed source variants and combine overlapping garments per native region')
    p.add_argument('--register-source-person', action='store_true',
                   help='Register the source soul and native Storm appearance/inventory rules')
    p.add_argument('--region-profile', type=Path,
                   help='JSON mapping clothing names to explicit waist_z preview thresholds')
    p.add_argument('--registration-profile', type=Path,
                   help='Explicit native gender, defaults and garment role mappings for any source person')
    p.add_argument('--output-data', type=Path,
                   help='Stage registration and character files in a new directory instead of installing overlays')
    args = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.namespace):
        p.error('Invalid namespace')
    source_data = args.tools/'Data'
    data = args.output_data if args.output_data else source_data
    if args.output_data:
        if data.exists(): raise FileExistsError('Staging destination already exists')
        source_assets = source_data/'objects/characters/gluenpc'/args.namespace
        if not (source_assets/'npc-report.json').is_file(): raise FileNotFoundError('Packaged character is missing')
        shutil.copytree(source_assets, data/'objects/characters/gluenpc'/args.namespace,
                        ignore=shutil.ignore_patterns('clothing-report.json'))
    asset_root = data/'objects/characters/gluenpc'/args.namespace
    report = json.loads((asset_root/'npc-report.json').read_text(encoding='utf-8'))
    registration = registration_profile(report, json.loads(args.registration_profile.read_text(encoding='utf-8'))
                                        if args.registration_profile else None)
    outfit = registration['garments']
    gender = registration['gender']
    cdf = ET.parse(asset_root/'character.cdf').getroot()
    clothing = [r for r in report['parts'] if r['kind']=='cloth']
    profile = json.loads(args.region_profile.read_text(encoding='utf-8')) if args.region_profile else {}
    unknown = set(profile) - {r['clothing_name'] for r in clothing}
    if unknown:
        raise ValueError(f'Region profile contains absent garments: {sorted(unknown)}')
    generated_skins = {}
    generated_materials = {}
    garments = []
    unsupported = [r['clothing_name'] for r in clothing if r['clothing_name'] not in outfit]
    if unsupported:
        raise ValueError(f'Unmapped native clothing roles: {unsupported}')
    paths = ['Libs/Tables/Character/CharacterComponent.xml',
             'Libs/Tables/Character/ClothingConfig.xml',
             'Libs/Tables/item/clothing_preset.xml',
             'Libs/Tables/item/item__gluenpc.xml',
             'Libs/Tables/rpg/soul__gluenpc.xml']
    if args.register_source_person:
        paths += ['Libs/Storm/storm.xml', 'Libs/Storm/appearance/gluemapper.xml',
                  'Libs/Storm/equipment/gluemapper.xml', 'Libs/Tables/item/InventoryPreset__gluenpc.xml']
    if any((data/name).exists() for name in paths):
        raise FileExistsError('Development table overlay already exists; inspect before rebuilding')
    with zipfile.ZipFile(source_data/'Tables.pak') as archive:
        docs = {name: ET.fromstring(read(archive,name)) for name in paths[:3]}
        archetypes = ET.fromstring(read(archive, 'Libs/Tables/rpg/soul_archetype.xml'))
    soul_archetype_id = native_soul_archetype(archetypes, gender)
    docs[paths[3]] = ET.Element('database', name='barbora')
    items = ET.SubElement(docs[paths[3]], 'ItemClasses', version='8')
    soul_id = report['soul_id'] if args.register_source_person else str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/'+args.namespace+'/body'))
    docs[paths[4]] = ET.Element('database', name='barbora')
    souls = ET.SubElement(docs[paths[4]], 'souls', version='2')
    soul_name = report['npc'] if args.register_source_person else 'Glue_'+args.namespace
    ET.SubElement(souls, 'soul', soul_id=soul_id, soul_name=soul_name,
                  soul_archetype_id=soul_archetype_id, factionName='cutsceneOnly',
                  brain_id='4b914d1c-724a-a92d-3e6b-d183d35b8b98',
                  digestion_multiplier='0', xp_multiplier='0', initial_clothing_dirt='0')
    root = ET.SubElement(docs[paths[0]].find('CharacterComponents'), 'Component',
                         Name='Glue_'+args.namespace, Race='Human', Gender=gender,
                         FilePath='gluenpc/'+args.namespace+'/')
    derived = ET.SubElement(root, 'DerivedComponents')
    preset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/'+args.namespace+'/outfit'))
    preset = ET.SubElement(docs[paths[2]].find('clothing_presets'), 'clothing_preset',
                           clothing_preset_id=preset_id, clothing_preset_name='Glue_'+args.namespace,
                           gender=gender, prefers_hood_on='false')
    preset_items = ET.SubElement(preset,'Items')
    converted = []
    for number, part in enumerate(report['parts']):
        if part['kind']=='body' and not args.register_source_person:
            continue  # Native region-separated body is needed by the manager.
        attachment = cdf.find(f'AttachmentList/Attachment[@AName="{part["kind"]}{number}"]')
        if attachment is None:
            raise ValueError('Missing packaged attachment')
        name = 'Glue_'+args.namespace+'_'+str(number)
        if part['kind'] == 'cloth' and args.assemble_outfit:
            region = outfit[part['clothing_name']][2]
            blob = (asset_root/Path(attachment.get('Binding')).name).read_bytes()
            garment = dict(name=part['clothing_name'], skin=blob, region=region,
                           morph=part.get('morph_target'),
                           material=ET.parse(asset_root/(Path(attachment.get('Material')).name+'.mtl')).getroot())
            if region == 'torso':
                if part['clothing_name'] not in profile:
                    raise ValueError('Assembled upper garments require explicit region profiles')
                garment['face_regions'] = assign_upper_garment_regions(blob, **profile[part['clothing_name']])
            garments.append(garment)
            continue
        if part['kind']=='cloth':
            armor_type, archetype, region, layer, final = outfit[part['clothing_name']]
            node = ET.SubElement(derived,'Clothing',Name=name,ArmorType=armor_type,
                                 ArmorArchetypeId=archetype)
            item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/'+args.namespace+'/'+part['armor_id']))
            ET.SubElement(items,'Armor',Id=item_id,Name=name,Clothing=name,MaxStatus='100',
                          Weight='1',Price='1',UIName='ui_nm_warning',UIInfo='ui_in_warning')
            ET.SubElement(preset_items,'Guid').text=item_id
            converted.append(dict(source=part['clothing_name'],component=name,item_id=item_id,
                                  region=region,layer=layer,source_morph=part.get('morph_target'),
                                  partition_verified=False))
        else:
            tag={'head':'Head','hair':'Hair','beard':'Beard','body':'Body'}[part['kind']]
            node=ET.SubElement(derived,tag,Name=name)
            region={'head':'face','hair':'head','beard':'beard','body':'torso'}[part['kind']]
            layer='0';final=False
        elements=ET.SubElement(node,'Elements')
        models = {region: Path(attachment.get('Binding')).name}
        # Preserve the complete source body at initial registration. The character
        # upgrader applies the gender-specific native assembly (female torso;
        # male torso/arms/legs/hands/feet) after converting the bind space.
        if part['kind'] == 'cloth' and part['clothing_name'] in profile:
            if region != 'torso':
                raise ValueError('The upper-garment preview classifier requires a torso garment')
            source_name = models[region]
            blob = (asset_root/source_name).read_bytes()
            labels = assign_upper_garment_regions(blob, **profile[part['clothing_name']])
            partitions = split_skin_regions(blob, labels)
            models = {}
            for part_region, payload in partitions.items():
                filename = Path(source_name).stem+'_'+part_region+'.skin'
                if (asset_root/filename).exists():
                    raise FileExistsError(f'Partition already exists: {filename}')
                generated_skins[filename] = payload
                models[part_region] = filename
            converted[-1]['regions'] = list(models)
            converted[-1]['region_triangle_counts'] = {r: labels.count(r) for r in models}
            converted[-1]['region_assignment'] = 'Unverified bone-weight/waist-plane preview'
        for part_region, model in models.items():
            ET.SubElement(elements,'SkinElement',EquipmentPart=part_region,BodyLayerId=layer,
                          Model=model, Material=Path(attachment.get('Material')).name+'.mtl',
                          IsFinalLayer=str(final).lower(),
                          KeepBodyLayer='true' if part['kind'] == 'cloth' else 'false')
    if args.assemble_outfit:
        regions = convert_fixed_outfit(garments, 'objects/characters/gluenpc/'+args.namespace+'/outfit_')
        name = 'Glue_'+args.namespace+'_outfit'
        node = ET.SubElement(derived, 'Clothing', Name=name, ArmorType=registration['assembled_armor_type'], ArmorArchetypeId=registration['assembled_archetype'])
        elements = ET.SubElement(node, 'Elements')
        item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/'+args.namespace+'/assembled-outfit'))
        ET.SubElement(items, 'Armor', Id=item_id, Name=name, Clothing=name, MaxStatus='100',
                      Weight='1', Price='1', UIName='ui_nm_warning', UIInfo='ui_in_warning')
        ET.SubElement(preset_items, 'Guid').text = item_id
        for region, output in regions.items():
            filename = 'outfit_'+region
            generated_skins[filename+'.skin'] = output['skin']
            generated_materials[filename+'.mtl'] = xml(output['material'])
            ET.SubElement(elements, 'SkinElement', EquipmentPart=region, BodyLayerId=registration['assembled_layer'],
                          Model=filename+'.skin', Material=filename+'.mtl',
                          KeepBodyLayer='true', IsFinalLayer='true')
        converted.append(dict(component=name, item_id=item_id, source=[g['name'] for g in garments],
                              regions={r: dict(sources=o['sources'], triangles=o['triangles']) for r,o in regions.items()},
                              variant_mode='Selected source variant baked at weight 1',
                              partition_verified=False))
    config='Glue_'+args.namespace
    attributes=dict(Name=config,Race='Human',Gender=gender,DefaultBody=registration['default_body'],
                    DefaultClothingPreset=preset_id,HeadIsNeeded='true')
    for number,part in enumerate(report['parts']):
        if part['kind'] in ('head','hair','beard') or (part['kind']=='body' and args.register_source_person):
            attributes['Default'+part['kind'].title()]='Glue_'+args.namespace+'_'+str(number)
    ET.SubElement(docs[paths[1]].find('ClothingConfigs'),'ClothingConfig',**attributes)
    if args.register_source_person:
        # Empty Storm prefixes perform a failing lookup, not a clear. Preserve
        # the source's lack of a separate beard and use explicit native underwear.
        ET.SubElement(derived, 'Beard', Name='Glue_'+args.namespace+'_empty_beard')
        attributes.setdefault('DefaultBeard', 'Glue_'+args.namespace+'_empty_beard')
        attributes['DefaultUnderwear'] = registration['underwear']
        with zipfile.ZipFile(source_data/'IPL_GameData.pak') as archive:
            storm = ET.fromstring(read(archive, 'Libs/Storm/storm.xml'))
        docs[paths[5]] = storm
        for task_name, path, operations in (
            ('appearance', paths[6], [('set'+kind.title(), {'name': attributes['Default'+kind.title()]})
                                     for kind in ('body', 'head', 'hair', 'beard', 'underwear')
                                     if attributes.get('Default'+kind.title())]),
            ('equipment', paths[7], [('setInventory', {'preset': 'inventory_Glue_'+args.namespace})])):
            task = storm.find(f'tasks/task[@name="{task_name}"]')
            if task is None: raise ValueError('Missing native Storm task')
            ET.SubElement(task, 'source', path=path.removeprefix('Libs/Storm/'))
            document = ET.Element('storm'); rules = ET.SubElement(document, 'rules')
            rule = ET.SubElement(rules, 'rule', name='gluemapper_'+args.namespace+'_'+task_name)
            ET.SubElement(ET.SubElement(rule, 'selectors'), 'hasName', name=soul_name)
            ops = ET.SubElement(rule, 'operations')
            for operation, fields in operations: ET.SubElement(ops, operation, **fields)
            docs[path] = document
        docs[paths[8]] = ET.Element('database', name='barbora')
        inventories = ET.SubElement(docs[paths[8]], 'InventoryPresets', version='2')
        inventory = ET.SubElement(inventories, 'InventoryPreset', Name='inventory_Glue_'+args.namespace, Health='1')
        ET.SubElement(inventory, 'ClothingPresetRef', Name='Glue_'+args.namespace)
    # CDF supplies the rig/sockets only. Clothing must come from the manager.
    for a in list(cdf.find('AttachmentList')):
        if a.get('Binding'):
            cdf.find('AttachmentList').remove(a)
    (asset_root/'layered.cdf').write_bytes(xml(cdf))
    for filename, payload in {**generated_skins, **generated_materials}.items():
        target = asset_root/filename
        if args.output_data and target.exists() and target.read_bytes() == payload:
            continue
        with target.open('xb') as out:
            out.write(payload)
    for name,doc in docs.items():
        destination=data/name;destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('xb') as out:out.write(xml(doc))
    result=dict(namespace=args.namespace,config=config,preset_id=preset_id,soul_id=soul_id,soul_name=soul_name,clothing=converted,
                source_person_registered=args.register_source_person,
                overlay_files=paths,model='objects/characters/gluenpc/'+args.namespace+'/layered.cdf',
                generated_skins=list(generated_skins),
                fixed_outfit=args.assemble_outfit,
                status='Fixed outfit region assembly; visual validation required' if args.assemble_outfit else
                       'Native manager registration probe; garment morphs and region partitioning unverified')
    (asset_root/'clothing-report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
