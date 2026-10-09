"""Import the native inventory/shop showroom without borrowing game state."""
import copy
import xml.etree.ElementTree as ET
from upgrade_map import xml


def import_showroom(mission, native):
    root=ET.fromstring(mission)
    source=[e for e in ET.fromstring(native).iter('Entity') if e.get('EditorLayer')=='showroom']
    classes={e.get('EntityClass') for e in source}
    if not {'UIApseLinkNode','UIShopLinkNode','InventoryDummyPlayer','InventoryDummyHorse','InventoryDummyDog'} <= classes:
        raise ValueError('Native inventory showroom is incomplete')
    existing=list(root.iter('Entity'))
    if any(e.get('EntityClass') in ('UIApseLinkNode','UIShopLinkNode') for e in existing):
        raise ValueError('Destination already has inventory UI')
    guids={e.get('EntityGuid') for e in existing}
    if any(e.get('EntityGuid') in guids for e in source):raise ValueError('Showroom GUID collision')
    next_id=max(int(e.get('EntityId','0')) for e in existing)+1
    remap={e.get('EntityId'):str(next_id+i) for i,e in enumerate(source)}
    for e in source:
        e=copy.deepcopy(e)
        e.set('EntityId',remap[e.get('EntityId')])
        for node in e.iter():
            for key in ('TargetId','ParentId'):
                if key in node.attrib:
                    old=node.get(key)
                    if old not in remap:raise ValueError('Showroom has an external entity dependency: '+old)
                    node.set(key,remap[old])
        root.append(e)
    return xml(root),{'native_entities':len(source),'source_layer':'showroom','entity_links_closed':True}
