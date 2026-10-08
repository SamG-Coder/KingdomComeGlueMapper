"""Resolve authored retail profile layers and NPC soul initialization records.

Outputs source data for conversion, not a runnable KCD2 world.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
from upgrade_map import read
from quest_import import ast


def resolve_profile(profiles, souls, documents, profile_name):
    matches = [p for p in profiles.iter('GameProfile') if p.findtext('Name') == profile_name]
    if len(matches) != 1: raise ValueError('Missing or ambiguous world profile')
    profile = matches[0]
    index = {name.replace('\\', '/').lower(): data for name, data in documents.items()}
    soul_index = {}
    for soul in souls.findall('./SoulList/Souls/Soul'):
        soul_index.setdefault(soul.findtext('EntityGuid'), []).append(soul)
    layers = []; npcs = []; missing = []; classes = Counter()
    for layer in profile.findall('./GameLayers/GameLayer'):
        name = layer.text
        path = 'layers/' + name.lower() + '.xml'
        if path not in index:
            missing.append(dict(kind='layer', name=name)); continue
        root = ET.fromstring(index[path])
        layers.append(dict(name=name, path=path, sha256=hashlib.sha256(index[path]).hexdigest(), objects=ast(root)))
        for entity in root.iter('Entity'):
            classes[entity.get('EntityClass')] += 1
            if entity.get('EntityClass') not in ('NPC', 'NPC_Female'): continue
            guid = entity.get('EntityGuid', '').replace('-', '')
            key = str(int(guid, 16)) if guid else None
            candidates = soul_index.get(key, [])
            record = dict(entity=ast(entity), layer=name, soul=None)
            if len(candidates) == 1: record['soul'] = ast(candidates[0])
            else: missing.append(dict(kind='npc_soul', name=entity.get('Name'), matches=len(candidates)))
            npcs.append(record)
    return dict(profile=ast(profile), layers=layers, npcs=npcs, unresolved=missing,
                entity_classes=dict(classes), executable=False,
                limitations=['Base level archive only; patch precedence requires validation',
                             'Profile selection supplied explicitly, not evaluated from startup script',
                             'NPC rig/clothing, inventories, AI, dialogue, voice and sound conversion not performed'])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--level-pak', type=Path, required=True)
    p.add_argument('--profile', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists(): raise ValueError('Choose a new output file')
    with zipfile.ZipFile(a.level_pak) as archive:
        profiles = read(archive, 'whdata_1'); souls = read(archive, 'whdata_0')
        docs = {n: read(archive, n) for n in archive.namelist() if n.lower().startswith('layers/') and n.lower().endswith('.xml')}
    result = resolve_profile(ET.fromstring(profiles), ET.fromstring(souls), docs, a.profile)
    result['source'] = dict(archive=str(a.level_pak), whdata_0_sha256=hashlib.sha256(souls).hexdigest(), whdata_1_sha256=hashlib.sha256(profiles).hexdigest())
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(dict(layers=len(result['layers']), npcs=len(result['npcs']), unresolved=result['unresolved'], classes=result['entity_classes'])))
