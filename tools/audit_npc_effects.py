"""Inventory legacy NPC masks and the missing native KCD2 effect bindings.

Read-only: finding a texture does not prove its channel semantics or UV layout
match KCD2. This deliberately does not turn on CLOTHING_SYSTEM on legacy maps.
"""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def audit_material(root):
    results = []
    for material in root.iter('Material'):
        shader = material.get('Shader', '').lower()
        if not shader:
            continue
        textures = {t.get('Map'): t.get('File') for t in material.findall('Textures/Texture')}
        flags = material.get('StringGenMask', '').split('%')
        result = dict(name=material.get('Name'), shader=shader, textures=textures,
                      runtime_effects_verified=False)
        if shader == 'humanskin':
            result.update(native_system='HumanSkin blood/grime', native_mask_slot='Emittance',
                          native_mask_bound=bool(textures.get('Emittance')),
                          legacy_dirt_blood_map=textures.get('Decal'),
                          missing=['UV-matched native BG mask and channel conversion',
                                   'Native blood/grime material parameters',
                                   'Live clean/dirty/bloodied/washed comparison'])
        elif shader == 'illum':
            native = 'CLOTHING_SYSTEM' in flags
            result.update(native_system='Illum clothing blood/grime/scratch',
                          clothing_shader_enabled=native,
                          legacy_dirt_blood_map=textures.get('Decal'),
                          legacy_damage_maps={k: textures.get(k) for k in ('Custom', '[1] Custom', 'Environment')},
                          missing=[] if native else [
                              'Native material-feature, macro and decal atlas conversion',
                              'UV-matched BGS mask (native Custom slot)',
                              'Per-feature material and scratch-level bindings',
                              'Live condition/dirt/blood/repair/wash comparison'])
        else:
            result.update(native_system='Not audited for this shader', missing=['Shader-specific audit'])
        results.append(result)
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('package', type=Path)
    p.add_argument('--output', type=Path, default=Path('reports/npc-effects-audit.json'))
    args = p.parse_args()
    report = json.loads((args.package/'npc-report.json').read_text(encoding='utf-8'))
    cdf = ET.parse(args.package/'character.cdf')
    rows = []
    for number, part in enumerate(report['parts']):
        attachment = cdf.find(f'.//Attachment[@AName="{part["kind"]}{number}"]')
        material = args.package/(Path(attachment.get('Material')).name+'.mtl')
        rows.append(dict(kind=part['kind'], name=part.get('clothing_name', part.get('character_'+part['kind']+'_name')),
                         material=material.name, audit=audit_material(ET.parse(material).getroot())))
    result = dict(npc=report['npc'], parts=rows, native_evidence={
        'clothing': 'Engine/Shaders.pak: IllumValidations.cfi, Illum.cfx, WHBlood.cfi, WHGrime.cfi, WHScratch.cfi',
        'skin': 'Engine/Shaders.pak: HumanSkinValidations.cfi and HumanSkin.cfx',
        'blood_zones': 'Data/Tables.pak: Libs/Tables/Character/BloodMask.xml',
        'runtime': 'Data/Scripts.pak: Scripts/Entities/WH/Special/DeadBody/DeadBody_Base_Human.lua'},
        notes=['DIRTLAYER is explicitly unrelated to the native clothing system',
               'Native scratch is visual material wear; geometric tears have not been established',
               'Fixed assembled outfits currently share one inventory item condition'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'Audited {len(rows)} parts; report: {args.output}')


if __name__ == '__main__': main()
