"""Exhaustive registration matrix, exact constraint counts, and geometry cases.

Counts over a Cartesian space are algebraic checks of explicit constraints.
They never certify rendering of outfits which were not actually inspected.
"""
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from itertools import combinations, product
import gzip
import json
import math
from pathlib import Path

from .validation import validate


CONTRACT_REVIEW = {'native_rules_unavailable', 'native_layer_difference',
                   'shared_armor_archetype', 'cross_outfit_masks', 'native_region_selection'}
VALIDATION_CONTRACT = 7


def contract_passed(result):
    return not any(f['severity']=='error' or f['code'] in CONTRACT_REVIEW
                   for f in result['findings'])


def signature(part):
    return tuple(sorted({(e.get('EquipmentPart',''), e.get('BodyLayerId') or '')
                         for e in part['elements']}))


def count_layer_space(parts, eligible, equipment=None):
    """Exact weighted DP over slot/archetype occupancy, not a sampled outfit run.

    Every item alternative has weight one, including distinct material variants.
    Equal state choices can be aggregated because subsequent constraints depend
    only on the occupied slots/archetypes. Unary checks are supplied by caller.
    """
    groups=defaultdict(list)
    for p in parts:
        groups[signature(p)].append(p)
    all_slots=sorted({slot for group in groups for slot in group})
    all_roles=sorted({p['attributes']['ArmorArchetypeId'] for p in parts})
    slots={s:1<<i for i,s in enumerate(all_slots)}
    roles={s:1<<i for i,s in enumerate(all_roles)}
    # Mesh regions/layers are not the inventory's exclusive slots. Different
    # archetypes can reserve the same native subpart without their declared
    # mesh signatures overlapping (e.g. a cap and a helmet).
    native_slots=sorted({slot for p in parts for slot in
                        equipment.exclusive.get(p['attributes']['ArmorArchetypeId'],set())}) if equipment else []
    native_bits={slot:1<<(len(slots)+i) for i,slot in enumerate(native_slots)}
    draw_slots = sorted({(e.get('EquipmentPart', ''), e.get('element_type', 'SkinElement'))
                         for p in parts for e in p['elements']})
    draw_bits = {s: 1 << (len(slots) + len(native_bits) + i) for i, s in enumerate(draw_slots)}
    states={(0,0):1}
    for group,items in sorted(groups.items()):
        mask=sum(slots[s] for s in group)
        choices=Counter((roles[p['attributes']['ArmorArchetypeId']],
            (sum(native_bits[s] for s in equipment.exclusive.get(p['attributes']['ArmorArchetypeId'],set()))
             if equipment else 0) | sum(draw_bits[s] for s in {
                 (e.get('EquipmentPart', ''), e.get('element_type', 'SkinElement')) for e in p['elements']}))
            for p in items if p['id'] in eligible)
        updated=defaultdict(int)
        for (occupied,equipped),weight in states.items():
            updated[occupied,equipped]+=weight  # empty choice
            if occupied & mask:
                continue
            for (role,native_mask),count in choices.items():
                if not equipped & role and not occupied & native_mask:
                    updated[occupied|mask|native_mask,equipped|role]+=weight*count
        states=dict(updated)
    total=math.prod(len(items)+1 for items in groups.values())
    accepted=sum(states.values())
    return dict(cartesian_total=total, registration_constraints_pass=accepted,
                registration_constraints_reject=total-accepted, exact=True, final_states=len(states),
                geometry_verified=False, native_exclusive_slots_checked=equipment is not None,
                constraints=['Unary native role/region/layer and asset checks',
                             'No repeated equipment-part/layer slot', 'No repeated armor archetype',
                             'No lower clothing mesh discarded by native region selection']+
                             (['No overlapping exclusive native body-subpart/layer slots'] if equipment else []),
                exclusions=['Body-dependent masks', 'Geometry and cloth physics',
                            'Native inventory arbitration', 'Other engine equipment exclusions'])


def load_resume(directory, catalog):
    directory=Path(directory)
    manifest=json.loads((directory/'summary.json').read_text(encoding='utf-8'))
    if manifest['signature']!=catalog.assets.signature() or manifest['rule_evidence']!=catalog.rule_evidence:
        raise ValueError('Resume evidence belongs to different assets or native rules')
    if manifest.get('validation_contract')!=VALIDATION_CONTRACT:
        raise ValueError('Resume validator contract does not match')
    rows={}
    with (directory/'cases.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            # Only an interrupted final write may be ignored, never corruption
            # within a completed line or anywhere earlier in the report.
            if not line.endswith('\n'):
                break
            row=json.loads(line)
            if row['case'] in rows:raise ValueError('Duplicate resume case')
            rows[row['case']]=row
    return rows


def run_matrix(catalog, destination, geometry=True, progress=print, resume_from=None, pair_range=None):
    destination=Path(destination)
    if destination.exists():
        raise FileExistsError('Choose a new matrix report directory')
    destination.mkdir(parents=True)
    summary=dict(schema=1, validation_contract=VALIDATION_CONTRACT, created=datetime.now(timezone.utc).isoformat(),
                 package=str(catalog.assets.root),overlay=str(catalog.assets.overlay) if catalog.assets.overlay else None,
                 signature=catalog.assets.signature(),rule_evidence=catalog.rule_evidence,
                 runtime_verified=False, all_geometry_verified=False, genders={}, catalog_errors=catalog.errors)
    sources = ([resume_from] if isinstance(resume_from,(str,Path)) else resume_from) or []
    previous={}
    for source in sources:
        for case,row in load_resume(source,catalog).items():
            if case in previous and previous[case]!=row:
                raise ValueError('Conflicting completed evidence for '+case)
            previous[case]=row
    summary['resumed_from']=[str(p) for p in sources]
    summary['pair_range']=pair_range
    summary['reusable_cases']=len(previous)
    (destination/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (destination/'catalog.json').write_text(json.dumps(catalog.summary(),indent=2),encoding='utf-8')
    checked=[]
    with (destination/'cases.jsonl').open('w',encoding='utf-8') as detail, \
            (destination/'checklist.csv').open('w',newline='',encoding='utf-8') as checklist:
        writer=csv.writer(checklist)
        writer.writerow(['case','gender','body_preset','components','native_table_contract','geometry','runtime','findings'])

        def record(case,gender,body,selected,result):
            contract=contract_passed(result)
            geo='checked' if result.get('geometry_checked') and result['status']=='checked' else 'review' if result.get('geometry_checked') else 'not_run'
            row=dict(case=case,gender=gender,body_preset=body,selected=selected,contract_checked=contract,
                     **{k:v for k,v in result.items() if k not in ('selected','case','gender','body_preset','contract_checked')})
            detail.write(json.dumps(row,separators=(',',':'))+'\n')
            writer.writerow([case,gender,body,';'.join(selected), 'checked' if contract else 'rejected_or_review',geo,'not_run',
                             ';'.join(sorted({f['code'] for f in result['findings']}))])
            if contract and geo=='checked':
                checked.append(dict(case=case,gender=gender,body=body,selected=selected))
            return row

        for gender in ('Female','Male'):
            presets=sorted((p for p in catalog.presets.values() if p['gender']==gender),key=lambda p:p['id'])
            if not presets:
                raise ValueError('No body available for '+gender)
            body=presets[0]
            base=[p for p in body['parts'] if catalog.parts[p]['kind']!='Clothing']
            parts=sorted((p for p in catalog.parts.values() if p['kind']=='Clothing' and p['gender']==gender
                          and p['attributes'].get('ArmorType')),key=lambda p:p['id'])
            rows={}
            counts=Counter(singles=0,pairs=0,presets=0)
            eligible=set()
            for index,part in enumerate(parts):
                selected=base+[part['id']]
                # Assets are inspected once per garment; body-independent unary
                # contracts do not include cross-outfit mask compatibility.
                case='single:'+part['id']
                result=previous.get(case)
                if result and result['selected']!=selected:raise ValueError('Resume selection differs')
                if result is None or (geometry and not result.get('geometry_checked')):
                    result=validate(catalog,selected,geometry=geometry)
                unary=dict(result,findings=[f for f in result['findings'] if f['code']!='cross_outfit_masks'
                    and (f['components']==[part['id']] or f['code']=='native_rules_unavailable')])
                if contract_passed(unary):eligible.add(part['id'])
                rows[part['id']]=result
                record('single:'+part['id'],gender,body['id'],selected,result)
                counts['singles']+=1;counts['single_'+result['status']]+=1
                if index%10==0:progress(f'{gender} single {index+1}/{len(parts)}')
            summary['genders'][gender]=dict(garments=len(parts),body_preset=body['id'],
                full_layer_space=count_layer_space(parts,eligible,getattr(catalog,'equipment',None)),counts=dict(counts))
            progress(f'{gender} starting {math.comb(len(parts),2)} pairs')
            for index,(a,b) in enumerate(combinations(parts,2)):
                if pair_range and not pair_range[0] <= index < pair_range[1]:
                    continue
                selected=base+[a['id'],b['id']]
                case='pair:'+a['id']+'+'+b['id']
                result=previous.get(case)
                if result and result['selected']!=selected:raise ValueError('Resume selection differs')
                if result is None:
                    result=validate(catalog,selected,inspect_assets=False)
                    for p in (a,b):
                        result['findings'].extend(f for f in rows[p['id']]['findings'] if f['code'] in ('asset_error','opacity_unverified'))
                    if any(f['severity']=='error' for f in result['findings']):result['status']='error'
                if geometry and contract_passed(result) and not result.get('geometry_checked'):
                    result=validate(catalog,selected,geometry=True)
                if result.get('geometry_checked'):
                    counts['pair_geometry_runs']+=1
                if contract_passed(result):counts['pair_contract_checked']+=1
                record('pair:'+a['id']+'+'+b['id'],gender,body['id'],selected,result)
                counts['pairs']+=1;counts['pair_'+result['status']]+=1
                if index%100==0:
                    detail.flush();checklist.flush();progress(f'{gender} pair {index+1}/{math.comb(len(parts),2)}')
            for preset in presets:
                result=previous.get('preset:'+preset['id'])
                if result and result['selected']!=preset['parts']:raise ValueError('Resume selection differs')
                if result is None or (geometry and not result.get('geometry_checked')):
                    result=validate(catalog,preset['parts'],geometry=geometry)
                record('preset:'+preset['id'],gender,preset['id'],preset['parts'],result)
                counts['presets']+=1;counts['preset_'+result['status']]+=1
            summary['genders'][gender]['counts']=dict(counts)
            (destination/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    summary['enumerated_cases']=sum(sum(v['counts'].get(k,0) for k in ('singles','pairs','presets')) for v in summary['genders'].values())
    summary['confirmed_geometry_cases']=len(checked)
    summary['matrix_enumeration_complete']=all(v['counts'].get('pairs',0)==math.comb(v['garments'],2)
                                             for v in summary['genders'].values())
    summary['limitations']=['Every single and pair is enumerated. Geometry runs only on pairs passing native table contracts.',
        'Singles/pairs use the named representative body for each gender; all converted presets use their own bodies.',
        'Full-layer Cartesian counts prove only the listed registration constraints, not arbitrary full-outfit geometry.',
        'All generated reports are bind-pose LOD 0 diagnostics, not native-game rendering acceptance.']
    (destination/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (destination/'confirmed.json').write_text(json.dumps(checked,indent=2),encoding='utf-8')
    progress('Completed: '+str(destination))
    return summary


def run_full_layers(catalog, gender, destination, maximum=1_000_000, progress=print):
    """Render-test every table-compatible stack in a tractable Cartesian space.

    Larger spaces fail explicitly; callers can use the exact symbolic counts
    without labelling those as rendered or full-outfit geometry verification.
    """
    parts=sorted((p for p in catalog.parts.values() if p['gender']==gender and p['kind']=='Clothing'
                  and p['attributes'].get('ArmorType')),key=lambda p:p['id'])
    groups=defaultdict(list)
    for p in parts:groups[signature(p)].append(p['id'])
    total=math.prod(len(g)+1 for g in groups.values())
    if total>maximum:raise ValueError(f'{total} full outfits exceed the explicit geometry-run limit {maximum}')
    body=min((p for p in catalog.presets.values() if p['gender']==gender),key=lambda p:p['id'])
    base=[p for p in body['parts'] if catalog.parts[p]['kind']!='Clothing']
    destination=Path(destination)
    if destination.exists():raise FileExistsError('Choose a new full-layer report directory')
    destination.mkdir(parents=True)
    summary=dict(validation_contract=VALIDATION_CONTRACT,gender=gender,body=body['id'],total=total,signature=catalog.assets.signature(),
                 overlay=str(catalog.assets.overlay) if catalog.assets.overlay else None,
                 rule_evidence=catalog.rule_evidence,geometry_scope='bind-pose LOD 0 on the named representative body',
                 runtime_verified=False,complete=False,counts={})
    counts=Counter();confirmed=[]
    with gzip.open(destination/'cases.jsonl.gz','wt',encoding='utf-8') as report:
        for i,group in enumerate(product(*([None]+g for g in groups.values()))):
            selected=base+[g for g in group if g]
            result=validate(catalog,selected,inspect_assets=False)
            if contract_passed(result):
                result=validate(catalog,selected,geometry=True)
                counts['geometry_runs']+=1
                if result['status']=='checked':
                    confirmed.append(dict(case=i,selected=selected))
            counts[result['status']]+=1;counts['visited']+=1
            report.write(json.dumps(dict(case=i,selected=selected,**{k:v for k,v in result.items() if k not in ('selected','meshes')}),separators=(',',':'))+'\n')
            if i%1000==0:
                summary['counts']=dict(counts)
                (destination/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
                progress(f'{gender} full stack {i+1}/{total}')
    summary.update(counts=dict(counts),enumeration_complete=counts['visited']==total,
                   complete=counts['checked']==total,
                   confirmed_without_findings=len(confirmed),all_geometry_verified=counts['geometry_runs']==total)
    (destination/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (destination/'confirmed.json').write_text(json.dumps(confirmed,indent=2),encoding='utf-8')
    return summary
