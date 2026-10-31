"""Explicit item acquisition stimuli for class/count tracking.

This models acquisition notifications, not native loot, ownership or transfer.
Snapshot replacement alone never synthesizes an acquisition.
"""
import copy
from . import Unsupported
from .expressions import boolean
from .objects import INVENTORY
from .pure import descriptor


def acquire(graph, inventory, item, amount):
    if not isinstance(item, str) or not item or type(amount) is not int or amount <= 0:
        raise ValueError('Acquisition requires an item class and positive integer amount')
    facets = copy.deepcopy(graph.objects.records.get(inventory, {}))
    queries = facets.get(INVENTORY, {}).get('queries', [])
    matches = [q for q in queries if q['method'] == 'Items' and q['arguments'] == {}]
    if len(matches) != 1:
        raise Unsupported('Acquisition requires an explicit inventory Items snapshot')
    items = matches[0]['result']
    before = items.get(item, 0)
    events = []
    for name, node in graph.nodes.items():
        if node['kind'] != 'ItemDescriptorTrigger': continue
        args = graph._args(name)
        if set(args) != {'Inventory', 'ItemDescriptor', 'IsActive'}:
            raise Unsupported('Incomplete item tracking interface: ' + name)
        if not boolean(args['IsActive']) or args['Inventory'] != inventory: continue
        target = args['ItemDescriptor']
        if not isinstance(target, dict) or set(target) != {'item_class', 'amount'}:
            raise Unsupported('Only class/count acquisition descriptors are modeled')
        descriptor(dict(ItemClass=target['item_class'], Amount=target['amount']))
        if target['item_class'] != item: continue
        events.append(name + '.OnAcquire')
        if before < target['amount'] <= before + amount:
            events.append(name + '.OnTargetAmountAcquire')
    items[item] = before + amount
    graph.objects.replace(inventory, facets)
    graph.record('item_acquired', inventory=inventory, item=item, amount=amount,
                 before=before, after=items[item], boundary='explicit_acquisition_stimulus')
    for event in reversed(events): graph.emit(event)
    graph.drain()
