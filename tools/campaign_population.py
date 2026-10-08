"""Resolve NPC appearance dependencies from authored world initialization.

Uses per-instance whdata values rather than substituting table/default outfits.
This emits a conversion manifest; no NPCs or inventories are spawned.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
from build_npc_probe import load_tables, resolve_parts


def child(node, name):
    return next((c for c in node['children'] if c['op'] == name), None)


def values(node):
    return {c['op']: c['text'].strip() for c in node['children']} if node else {}


def resolve_actor(record, rows):
    soul = record['soul']
    if soul is None: raise ValueError('Missing authored soul')
    identity = values(soul)
    static = child(soul, 'StaticData')
    props = values(static)
    body = values(child(static, 'CharacterBodyDescription'))
    clothing = values(child(static, 'InitialClothingDescription'))
    source = {'initial_clothing_preset_id': clothing.get('PresetId', '')}
    for kind in ('body', 'head', 'hair', 'beard'):
        value = body.get(kind.title() + 'Id')
        if value and value != '00000000-0000-0000-0000-000000000000':
            source['character_' + kind + '_id'] = value
    if not source.get('character_body_id'): raise ValueError('Authored body is missing')
    parts = resolve_parts(source, rows)
    return dict(name=record['entity']['attributes']['Name'], layer=record['layer'],
                placement=record['entity']['attributes'], source_identity=identity,
                appearance_values=body, clothing_values=clothing, parts=parts,
                inventory_id=props.get('InventoryId'), voice_id=props.get('VoiceId'),
                ai=child(static, 'InitialAIData'), entity_links=child(record['entity'], 'EntityLinks'),
                converted=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', type=Path, required=True)
    p.add_argument('--kcd1-data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists(): raise ValueError('Choose a new output file')
    world = json.loads(a.profile.read_text(encoding='utf-8'))
    actors = []; unresolved = []
    with ExitStack() as stack:
        lookup = load_tables(stack, a.kcd1_data)
        cache = {}
        def rows(name):
            if name not in cache: cache[name] = lookup(name)
            return cache[name]
        for record in world['npcs']:
            try: actors.append(resolve_actor(record, rows))
            except (KeyError, ValueError, StopIteration) as error:
                unresolved.append(dict(name=record['entity']['attributes']['Name'], error=repr(error)))
    result = dict(actors=actors, unresolved=unresolved, executable=False,
                  limitations=['Asset packaging and native clothing roles remain required',
                               'Inventory and voice IDs are references, not converted inventories or audio',
                               'Source AI and entity links are preserved, not executable'])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(dict(actors=len(actors), unresolved=unresolved,
                         garments=len({p['clothing_id'] for actor in actors for p in actor['parts'] if p['kind']=='cloth'}))))
