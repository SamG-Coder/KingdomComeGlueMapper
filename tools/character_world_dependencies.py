"""Whole-world NPC dependency closure, with typed identities and cycle-safe links.

Discover every placement, including conditional layers. Conversion adapters
create dependencies once; graph edges are bound only after both endpoints exist.
The staging manifest never calls a source record a runtime registration.
"""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from campaign_dependency_adapters import SourceTables
from campaign_sources import RetailSourceReader
from campaign_inventory import resolve_inventory, TABLES as INVENTORY_TABLES
from character_world_services import level_entities
from upgrade_map import read


class WorldDependencies:
    """A graph can have cycles; allocating entities and binding links are separate.

    An adapter must return an actual emitted native registration. Errors remain
    attached to the node and every dependent edge is blocked, never fabricated.
    """
    def __init__(self):
        self.nodes, self.edges, self.roots = {}, [], []
        self._edge_keys = set()

    def add(self, kind, identity, source):
        key = kind + ':' + str(identity).lower()
        value = dict(kind=kind, identity=str(identity), source=source)
        if key in self.nodes and self.nodes[key] != value:
            raise ValueError('Conflicting world dependency: ' + key)
        self.nodes[key] = value
        return key

    def link(self, owner, target, label):
        edge = dict(source=owner, target=target, label=label)
        key = owner, target, label
        if key not in self._edge_keys:
            self._edge_keys.add(key)
            self.edges.append(edge)

    def convert(self, adapters):
        registrations, failures = {}, {}
        # Allocation can handle cyclic entity links because binding happens
        # after this pass. Adapter dependencies must be expressed in the graph.
        for key, node in self.nodes.items():
            adapter = adapters.get(node['kind'])
            if adapter is None:
                failures[key] = 'No native creation adapter for ' + node['kind']
                continue
            try:
                registration = adapter.create(node['identity'], node['source'])
                if not registration: raise ValueError('Adapter produced no native registration')
                registrations[key] = registration
            except (ValueError, KeyError, FileNotFoundError) as error:
                failures[key] = str(error)
        bound, blocked = [], []
        for edge in self.edges:
            owner, target = edge['source'], edge['target']
            if owner not in registrations or target not in registrations:
                blocked.append(dict(edge, reason='An endpoint has no native registration'))
                continue
            try:
                adapter = adapters[self.nodes[owner]['kind']]
                adapter.bind(registrations[owner], registrations[target], edge['label'])
                bound.append(edge)
            except (ValueError, KeyError, FileNotFoundError) as error:
                blocked.append(dict(edge, reason=str(error)))
        # Failure propagates to every dependent root, even when that root's
        # own entity allocation succeeded. Cycles converge through a queue.
        parents = defaultdict(set)
        for edge in self.edges: parents[edge['target']].add(edge['source'])
        incomplete = set(failures) | {e['source'] for e in blocked}
        queue = deque(incomplete)
        while queue:
            for parent in parents[queue.popleft()]:
                if parent not in incomplete:
                    incomplete.add(parent);queue.append(parent)
        return dict(registrations=registrations, failures=failures, bound_edges=bound,
                    blocked_edges=blocked, incomplete_nodes=sorted(incomplete),
                    root_status={root: 'incomplete' if root in incomplete else 'converted' for root in self.roots},
                    complete=not failures and not blocked, runtime_verified=False)

    def report(self):
        return dict(schema=1, roots=self.roots, nodes=self.nodes, edges=self.edges,
                    counts=dict(Counter(n['kind'] for n in self.nodes.values())),
                    complete=False, runtime_verified=False)


def discover(source, level='rataje'):
    """Read all NPCs by persistent GUID; names are labels, not unique identities."""
    source = Path(source)
    from character_person import effective_soul
    with zipfile.ZipFile(source / 'Data/Levels' / level / 'level.pak') as archive:
        entities = level_entities(archive)
        wh = ET.fromstring(read(archive, 'whdata_0'))
    by_id, by_guid = {}, {}
    for entity in entities:
        if not entity.get('EntityGuid') or not entity.get('EntityId'): continue
        key = entity.get('EntityGuid').lower()
        if key in by_guid and ET.tostring(entity) != ET.tostring(by_guid[key]):
            raise ValueError('Ambiguous source entity GUID: ' + key)
        by_guid[key] = entity
        by_id[entity.get('EntityId')] = entity
    instances = defaultdict(list)
    for soul in wh.findall('./SoulList/Souls/Soul'):
        if soul.findtext('EntityGuid'):
            instances[int(soul.findtext('EntityGuid'))].append(soul)
    graph, missing, pending = WorldDependencies(), [], deque()
    people = sorted((e for e in by_guid.values() if e.get('EntityClass') in ('NPC', 'NPC_Female')),
                    key=lambda e: e.get('EntityGuid'))
    for actor in people:
        identity = actor.get('EntityGuid').lower()
        graph.roots.append('entity:' + identity)
        pending.append(actor)
    visited = set()
    while pending:
        entity = pending.popleft()
        identity = entity.get('EntityGuid').lower()
        if identity in visited: continue
        visited.add(identity)
        owner = graph.add('entity', identity, dict(attributes=dict(entity.attrib),
                           xml=ET.tostring(entity, encoding='unicode')))
        for link in entity.findall('EntityLinks/Link'):
            target = by_id.get(link.get('TargetId'))
            if target is None or not target.get('EntityGuid'):
                missing.append(dict(owner=owner, label=link.get('Name'), target_id=link.get('TargetId')))
                continue
            graph.link(owner, 'entity:' + target.get('EntityGuid').lower(), link.get('Name', ''))
            pending.append(target)

    with RetailSourceReader(source) as reader:
        tables = SourceTables(source, reader)
        # Keep a typed key for each SQL table: faction 6 and superfaction 6 are
        # different records. The old person importer confused these spaces.
        def table_node(table, field, identity, owner, label):
            if not identity or identity == '00000000-0000-0000-0000-000000000000': return None
            try: row = tables.row(table, field, identity)
            except ValueError as error:
                missing.append(dict(owner=owner, label=label, table=table, identity=identity, reason=str(error)))
                return None
            key = graph.add(table, identity, row)
            graph.link(owner, key, label)
            return key, row

        for actor in people:
            owner = 'entity:' + actor.get('EntityGuid').lower()
            found = instances[int(actor.get('EntityGuid'), 16)]
            if len(found) != 1:
                missing.append(dict(owner=owner, label='soul_instance', reason='Expected one SoulList record', matches=len(found)))
                continue
            instance = found[0]
            inst = graph.add('soul_instance', instance.findtext('Guid'),
                             dict(xml=ET.tostring(instance, encoding='unicode'), layer=actor.get('Layer')))
            graph.link(owner, inst, 'soul_instance')
            shared = instance.findtext('SharedSoulGuid')
            if shared:
                value = table_node('rpg/soul', 'soul_id', shared, inst, 'soul_definition')
                if value is None: continue
                soul_key, definition = value
            else:
                definition = None
            soul = effective_soul(definition, instance)
            if not shared:
                soul_key = graph.add('inline_soul', instance.findtext('Guid'), soul)
                graph.link(inst, soul_key, 'soul_definition')
            static = instance.find('StaticData')
            for table, field, old, path in (
                ('ai/brain','brain_id','brain_id','InitialAIData/BrainId'),
                ('rpg/social_class','social_class_id','social_class_id','SocialClassId'),
                ('rpg/faction','faction_id','faction','FactionId'),
                ('inventory/inventory','inventory_id','inventory_id','InventoryId'),
                ('item/weapon_preset','weapon_preset_id','initial_weapon_preset_id','InitialWeaponPresetId'),
                ('item/clothing_preset','clothing_preset_id','initial_clothing_preset_id','InitialClothingDescription/PresetId')):
                identity = static.findtext(path) if static is not None else None
                value = table_node(table, field, identity or soul.get(old), inst, old)
                if value and table == 'rpg/faction':
                    key, row = value
                    table_node('rpg/superfaction', 'superfaction_id', row['superfaction_id'], key, 'superfaction')
                    table_node('rpg/location', 'location_id', row.get('location_id'), key, 'location')
            for kind in ('body', 'head', 'hair', 'beard'):
                table = 'character_' + kind
                value = static.findtext('CharacterBodyDescription/' + kind.title() + 'Id') if static is not None else None
                table_node(table, table + '_id', value or soul.get(table + '_id'), inst, kind)
            # Preserve every source schedule, voice and instance override in a
            # dedicated dependency; there is no generic "idle means done" path.
            for path, kind in [('InitialAIData/Activities','schedule'), ('InitialWeaponPresetId','weapon_equipment'),
                               ('VoiceId','voice'), ('CharacterBodyDescription','appearance_parameters')]:
                element = static.find(path) if static is not None else None
                if element is not None:
                    blob = ET.tostring(element, encoding='unicode')
                    key = graph.add(kind, hashlib.sha256(blob.encode()).hexdigest(), dict(xml=blob))
                    graph.link(inst, key, kind)
        # Expand all item selections without rolling random choices at import
        # time. A preset and its probabilities are gameplay dependencies too.
        clothing = tables.get('item/armor2clothing_preset')['rows']
        weapons = tables.get('item/weapon2weapon_preset')['rows']
        inventories = {name: tables.get('inventory/' + name)['rows'] for name in INVENTORY_TABLES}
        for owner, node in list(graph.nodes.items()):
            kind, identity = node['kind'], node['identity']
            if kind in ('item/clothing_preset', 'item/weapon_preset'):
                field = kind.split('/')[-1] + '_id'
                references = [r for r in (clothing if kind == 'item/clothing_preset' else weapons) if r[field] == identity]
                for row in references:
                    key = graph.add('equipment_selection', hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest(), row)
                    graph.link(owner, key, 'equipment_selection')
                    table_node('item/item', 'item_id', row.get('armor_id') or row.get('item_id'), key, 'item')
            elif kind == 'inventory/inventory':
                inventory = resolve_inventory(identity, inventories)
                key = graph.add('inventory_selection', identity, inventory)
                graph.link(owner, key, 'selection')
                for item in inventory['item_dependencies']:
                    table_node('item/item', 'item_id', item, key, 'item')
        item_nodes = [(k, n) for k, n in graph.nodes.items() if n['kind'] == 'item/item']
        for owner, node in item_nodes:
            # Source SQL inheritance is retained explicitly instead of treating
            # an item UUID as a complete equipped weapon, garment or food item.
            identity = node['identity']
            for table in ('player_item','pickable_item','equippable_item','armor','weapon'):
                row = tables.row('item/' + table, 'item_id', identity, optional=True)
                if not row: continue
                key = graph.add('item/' + table, identity, row)
                graph.link(owner, key, 'inherits')
                if table == 'armor' and row.get('character_cloth_id'):
                    table_node('character_cloth', 'character_cloth_id', row['character_cloth_id'], key, 'clothing_component')
    report = graph.report()
    report.update(level=level, people=len(people), resident=sum(not e.get('Layer') for e in people),
                  conditional=sum(bool(e.get('Layer')) for e in people), unresolved_source_dependencies=missing,
                  note='Source dependency closure; these are not yet native runtime registrations.')
    return graph, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kcd1', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--level', default='rataje')
    args = parser.parse_args()
    if args.output.exists(): raise FileExistsError('Choose a fresh dependency manifest')
    graph, report = discover(args.kcd1, args.level)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('people', 'resident', 'conditional', 'counts')}))
    print('Unresolved source references:', len(report['unresolved_source_dependencies']))


if __name__ == '__main__': main()
