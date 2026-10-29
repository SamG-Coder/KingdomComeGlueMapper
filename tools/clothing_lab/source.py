"""Enumerate source souls, both garment slots and armour-linked clothing details."""
from campaign_dependency_adapters import SourceTables
from campaign_sources import RetailSourceReader


def soul_records(source, catalog):
    with RetailSourceReader(source) as reader:
        tables = SourceTables(source,reader)
        souls = tables.get('rpg/soul')['rows']
        armor = {r['item_id']:r for r in tables.get('item/armor')['rows']}
        clothing = {r['clothing_id']:r for r in tables.get('item/clothing')['rows']}
        members={}
        for row in tables.get('item/armor2clothing_preset')['rows']:
            members.setdefault(row['clothing_preset_id'],[]).append(row['armor_id'])
        presets={r['clothing_preset_id']:r for r in tables.get('item/clothing_preset')['rows']}
        anatomy={kind:{r['character_'+kind+'_id']:r for r in tables.get('character_'+kind)['rows']}
                 for kind in ('body','head','hair','beard')}
        attachments={}
        for row in tables.get('item/attachment2clothing_preset')['rows']:
            attachments.setdefault(row['clothing_preset_id'],[]).append(row)
        detail_rows = {r['clothing_attachment_id']:r for r in tables.get('item/clothing_attachment')['rows']}
        detail_links = {}
        for row in tables.get('item/armor2clothing_attachment')['rows']:
            detail_links.setdefault(row['item_id'], []).append(row)
        by_actor={p['label']:p for p in catalog.presets.values()}
        records=[]
        nulls={None,'','00000000-0000-0000-0000-000000000000'}
        for soul in souls:
            preset_id=soul.get('initial_clothing_preset_id');parts=[];issues=[]
            body_parts=[]
            for kind,lookup in anatomy.items():
                identity=soul.get('character_'+kind+'_id')
                if identity in nulls:continue
                if identity not in lookup:issues.append('Missing source '+kind+' record: '+identity)
                else:body_parts.append(dict(kind=kind,**lookup[identity]))
            if preset_id not in nulls and preset_id not in presets: issues.append('Missing source clothing preset: '+preset_id)
            for item in members.get(preset_id,[]):
                if item not in armor: issues.append('Missing armor record: '+item);continue
                for field in ('clothing_id','clothing2_id'):
                    cid=armor[item].get(field)
                    if cid in nulls: continue
                    if cid not in clothing: issues.append('Missing clothing record: '+cid);continue
                    parts.append(dict(armor_id=item,slot=field,**clothing[cid]))
            details = []
            for item in sorted({p['armor_id'] for p in parts}):
                genders = {p.get('gender_id') for p in parts if p['armor_id']==item}
                for link in detail_links.get(item, []):
                    identity = link['clothing_attachment_id']
                    if identity not in detail_rows:
                        issues.append('Missing clothing attachment record: '+identity)
                        continue
                    row = detail_rows[identity]
                    if None not in genders and row.get('gender_id') not in genders:
                        continue
                    details.append(dict(armor_id=item, armor_settings=link, **row))
            converted=by_actor.get(soul.get('soul_name'))
            records.append(dict(id=soul['soul_id'],label=soul.get('soul_name',soul['soul_id']),
                body={k:v for k,v in soul.items() if k.startswith('character_')},source_body_parts=body_parts,source_preset=preset_id,
                source_clothing=parts,source_attachments=attachments.get(preset_id,[]),
                source_clothing_attachments=details,
                source_issues=issues,converted_preset=converted['id'] if converted else None,
                selected=converted['parts'] if converted else [],
                status='source_error' if issues else 'ready' if converted else 'not_converted'))
        return records
