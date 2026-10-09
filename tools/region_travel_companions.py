"""Import native companion scheduling objects without creating a new horse."""
import copy
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from upgrade_map import xml
from campaign_entity_links import guid_value


def horse_recovery_resources(native_player, road_arrivals=None):
    """Append a bounded lifecycle hook to the installed game's complete script."""
    marker=b'GlueTravelHorseRecovery'
    if marker in native_player or b'function Player:OnInit()' not in native_player or b'function Player:OnLoadAI(' not in native_player:
        raise ValueError('Unexpected or already-patched native Player script')
    def vector(values):
        return '{'+','.join(f'{axis}={float(value):.9f}' for axis,value in zip('xyz',values))+'}'
    rows=[]
    for level,report in (road_arrivals or {}).items():
        points=','.join(vector(c['position']) for c in report['candidates'])
        rows.append('['+json.dumps(level)+']={arrival='+vector(report['arrival'])+',positions={'+points+'}}')
    config=('\nGlueTravelHorseRoadArrivals={'+','.join(rows)+'}\n').encode('utf-8')
    hook=Path(__file__).with_name('region_travel_horse_recovery.lua').read_bytes()
    return {'Scripts/Entities/actor/player.lua':native_player+config+hook}


def import_horse_scheduler_table(destination, native, mission):
    """Preserve native compiled scheduling links, not just editor EntityLinks."""
    entities=list(ET.fromstring(mission).iter('Entity'))
    identities=set()
    for name in ('playerHorseProxy','playerHorseDefaultSmartObject'):
        matches=[e for e in entities if e.get('Name')==name]
        if len(matches)!=1:
            raise ValueError('Missing/ambiguous horse scheduling entity: '+name)
        identities.add(str(guid_value(matches[0].get('EntityGuid'))))
    src=ET.fromstring(native)
    dst=ET.fromstring(destination)
    source_rows=src.find('Schedulers');target_rows=dst.find('Schedulers')
    if source_rows is None or target_rows is None or source_rows.attrib!=target_rows.attrib:
        raise ValueError('Unsupported compiled scheduler schema')
    selected=[e for e in source_rows if e.get('EntityGuid') in identities]
    if len(selected)!=len(identities) or {e.get('EntityGuid') for e in selected}!=identities:
        raise ValueError('Native horse scheduler records are incomplete')
    if any(e.get('EntityGuid') in identities for e in target_rows):
        raise ValueError('Horse compiled scheduler already registered')
    for entry in selected:
        for link in entry.findall('Links/S_ActivityLink'):
            if link.get('TargetGuid') not in identities or link.get('PositioningDelegate','0') not in identities|{'0'}:
                raise ValueError('Unresolved horse scheduler dependency')
        target_rows.append(copy.deepcopy(entry))
    return xml(dst),{'compiled_scheduler_records':len(selected),
                    'native_entity_guids':sorted(identities),'runtime_verified':False}


def import_horse_scheduler(mission, native, arrival, height):
    root=ET.fromstring(mission)
    existing=list(root.iter('Entity'))
    source=list(ET.fromstring(native).iter('Entity'))
    # wh_ai_PlayerHorseSchedulerProxy resolves this authored entity by name.
    names=('playerHorseProxy','playerHorseDefaultSmartObject')
    selected=[]
    for name in names:
        found=[e for e in source if e.get('Name')==name]
        if len(found)!=1:raise ValueError('Missing/ambiguous native horse scheduler: '+name)
        if any(e.get('Name')==name or e.get('EntityGuid')==found[0].get('EntityGuid') for e in existing):
            raise ValueError('Horse scheduler already exists: '+name)
        selected.append(copy.deepcopy(found[0]))
    next_id=max(int(e.get('EntityId','0')) for e in existing)+1
    remap={e.get('EntityId'):str(next_id+i) for i,e in enumerate(selected)}
    by_old={e.get('EntityId'):e for e in selected}
    x,y,_=map(float,arrival.get('Pos').split(','))
    for e in selected:
        e.set('EntityId',remap[e.get('EntityId')]);e.set('Pos',f'{x},{y},{height(x,y)}')
        e.attrib.pop('Layer',None)
        for link in e.findall('EntityLinks/Link'):
            old=link.get('TargetId')
            if old not in remap:raise ValueError('Unresolved native horse scheduler dependency')
            link.set('TargetId',remap[old]);link.set('TargetGuid',by_old[old].get('EntityGuid'))
        root.append(e)
    return xml(root),{'native_scheduler_entities':list(names),'creates_horse':False,
                      'changes_ownership':False,'runtime_verified':False}
