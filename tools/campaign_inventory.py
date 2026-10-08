"""Resolve authored inventories without flattening weighted/random selection."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from campaign_sources import retail_sources


TABLES = ('inventory', 'inventory2item', 'inventory2inventory_preset',
          'inventory_preset', 'inventory_preset2item')


def resolve_inventory(identifier, tables):
    matches = [r for r in tables['inventory'] if r['inventory_id'] == identifier]
    if len(matches) != 1: raise ValueError('Missing or ambiguous inventory ' + str(identifier))
    direct = [r for r in tables['inventory2item'] if r['inventory_id'] == identifier]
    references = [r for r in tables['inventory2inventory_preset'] if r['inventory_id'] == identifier]
    presets = []
    for reference in references:
        pid = reference['inventory_preset_id']
        definitions = [r for r in tables['inventory_preset'] if r['inventory_preset_id'] == pid]
        if len(definitions) != 1: raise ValueError('Missing or ambiguous inventory preset ' + pid)
        presets.append(dict(reference=reference, definition=definitions[0],
                            items=[r for r in tables['inventory_preset2item'] if r['inventory_preset_id'] == pid]))
    dependencies = sorted({r['item_id'] for r in direct} |
                          {r['item_id'] for p in presets for r in p['items']})
    return dict(definition=matches[0], direct_items=direct, preset_choices=presets,
                item_dependencies=dependencies, selection_evaluated=False, native_emitted=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--population', type=Path, required=True)
    p.add_argument('--kcd1', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists(): raise ValueError('Choose a new output file')
    population = json.loads(a.population.read_text(encoding='utf-8'))
    sources = retail_sources(a.kcd1, {n: ('Tables.pak', 'Libs/Tables/inventory/' + n + '.xml') for n in TABLES})
    tables = {n: [dict(r.attrib) for r in ET.fromstring(s['data']).iter('row')] for n, s in sources.items()}
    inventories = {}; missing = []
    for identifier in sorted({actor['inventory_id'] for actor in population['actors'] if actor['inventory_id']}):
        try: inventories[identifier] = resolve_inventory(identifier, tables)
        except ValueError as error: missing.append(str(error))
    result = dict(inventories=inventories, unresolved=missing,
                  actors={actor['name']:actor['inventory_id'] for actor in population['actors']},
                  provenance={n:s['candidates'] for n,s in sources.items()}, executable=False)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(dict(inventories=len(inventories), unresolved=missing,
                         items=len({i for inv in inventories.values() for i in inv['item_dependencies']}))))
