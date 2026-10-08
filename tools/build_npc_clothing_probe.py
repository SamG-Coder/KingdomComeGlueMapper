"""Register a bounded KCD1 outfit with the native KCD2 clothing manager.

Writes local development XML overlays, preserving every native definition.
Mesh partitioning, morph conversion and hiding masks remain separate gates.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile

from upgrade_map import read, xml


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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tools', type=Path, default=Path('D:/SteamLibrary/steamapps/common/KCD2Mod'))
    p.add_argument('--namespace', default='npc_layers_v1')
    args = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.namespace):
        p.error('Invalid namespace')
    data = args.tools/'Data'
    asset_root = data/'objects/characters/gluenpc'/args.namespace
    report = json.loads((asset_root/'npc-report.json').read_text(encoding='utf-8'))
    if report.get('npc') != 'ska_fatherOfHenry':
        raise ValueError('This experimental clothing map supports ska_fatherOfHenry only')
    cdf = ET.parse(asset_root/'character.cdf').getroot()
    clothing = [r for r in report['parts'] if r['kind']=='cloth']
    unsupported = [r['clothing_name'] for r in clothing if r['clothing_name'] not in OUTFIT]
    if unsupported:
        raise ValueError(f'Unmapped native clothing roles: {unsupported}')
    paths = ['Libs/Tables/Character/CharacterComponent.xml',
             'Libs/Tables/Character/ClothingConfig.xml',
             'Libs/Tables/item/clothing_preset.xml',
             'Libs/Tables/item/item__gluenpc.xml',
             'Libs/Tables/rpg/soul__gluenpc.xml']
    if any((data/name).exists() for name in paths):
        raise FileExistsError('Development table overlay already exists; inspect before rebuilding')
    with zipfile.ZipFile(data/'Tables.pak') as archive:
        docs = {name: ET.fromstring(read(archive,name)) for name in paths[:3]}
    docs[paths[3]] = ET.Element('database', name='barbora')
    items = ET.SubElement(docs[paths[3]], 'ItemClasses', version='8')
    soul_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/'+args.namespace+'/body'))
    docs[paths[4]] = ET.Element('database', name='barbora')
    souls = ET.SubElement(docs[paths[4]], 'souls', version='2')
    ET.SubElement(souls, 'soul', soul_id=soul_id, soul_name='Glue_'+args.namespace,
                  soul_archetype_id='0', factionName='cutsceneOnly',
                  digestion_multiplier='0', xp_multiplier='0', initial_clothing_dirt='0')
    root = ET.SubElement(docs[paths[0]].find('CharacterComponents'), 'Component',
                         Name='Glue_'+args.namespace, Race='Human', Gender='Male',
                         FilePath='gluenpc/'+args.namespace+'/')
    derived = ET.SubElement(root, 'DerivedComponents')
    preset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/'+args.namespace+'/outfit'))
    preset = ET.SubElement(docs[paths[2]].find('clothing_presets'), 'clothing_preset',
                           clothing_preset_id=preset_id, clothing_preset_name='Glue_'+args.namespace,
                           gender='Male', prefers_hood_on='false')
    preset_items = ET.SubElement(preset,'Items')
    converted = []
    for number, part in enumerate(report['parts']):
        if part['kind']=='body':
            continue  # Native region-separated body is needed by the manager.
        attachment = cdf.find(f'AttachmentList/Attachment[@AName="{part["kind"]}{number}"]')
        if attachment is None:
            raise ValueError('Missing packaged attachment')
        name = 'Glue_'+args.namespace+'_'+str(number)
        if part['kind']=='cloth':
            armor_type, archetype, region, layer, final = OUTFIT[part['clothing_name']]
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
            tag={'head':'Head','hair':'Hair','beard':'Beard'}[part['kind']]
            node=ET.SubElement(derived,tag,Name=name)
            region={'head':'face','hair':'head','beard':'beard'}[part['kind']]
            layer='0';final=False
        elements=ET.SubElement(node,'Elements')
        ET.SubElement(elements,'SkinElement',EquipmentPart=region,BodyLayerId=layer,
                      Model=Path(attachment.get('Binding')).name,
                      Material=Path(attachment.get('Material')).name+'.mtl',
                      IsFinalLayer=str(final).lower())
    config='Glue_'+args.namespace
    attributes=dict(Name=config,Race='Human',Gender='Male',DefaultBody='male_body_npc',
                    DefaultClothingPreset=preset_id,HeadIsNeeded='true')
    for number,part in enumerate(report['parts']):
        if part['kind'] in ('head','hair','beard'):
            attributes['Default'+part['kind'].title()]='Glue_'+args.namespace+'_'+str(number)
    ET.SubElement(docs[paths[1]].find('ClothingConfigs'),'ClothingConfig',**attributes)
    # CDF supplies the rig/sockets only. Clothing must come from the manager.
    for a in list(cdf.find('AttachmentList')):
        if a.get('Binding'):
            cdf.find('AttachmentList').remove(a)
    (asset_root/'layered.cdf').write_bytes(xml(cdf))
    for name,doc in docs.items():
        destination=data/name;destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('xb') as out:out.write(xml(doc))
    result=dict(namespace=args.namespace,config=config,preset_id=preset_id,soul_id=soul_id,clothing=converted,
                overlay_files=paths,model='objects/characters/gluenpc/'+args.namespace+'/layered.cdf',
                status='Native manager registration probe; garment morphs and region partitioning unverified')
    (asset_root/'clothing-report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
