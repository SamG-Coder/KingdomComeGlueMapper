"""Source-person AI contracts, explicit native adapters, and coverage receipts.

Identity, appearance, and a brain ID alone are not a complete NPC import. Keep
the original record and every authored link available to world/activity adapters.
Unsupported dependencies remain in the receipt instead of silently disappearing.
"""
import copy
import json
import math
import xml.etree.ElementTree as ET


# Names are verified against the shipped Storm abilities operations. Combat
# level is intentionally absent: KCD1's integer level is not KCD2's 0..1 factor.
STATS = {'str': 'strength', 'agi': 'agility', 'vit': 'vitality', 'spc': 'speech',
         'vision': 'vision', 'hearing': 'hearing', 'courage': 'courage',
         'charisma': 'charisma', 'shadiness': 'shadiness'}
PROPERTIES = ('bWH_PerceptorObject', 'bWH_PerceptibleObject', 'bWH_ListenerObject')
BRAIN_ADAPTERS = {'npc_daycycle': 'npc_basic', 'npc_dummyWait': 'npc_default',
                  'Default': 'Default',
                  'npc_test_base': 'npc_default', 'npc_deadBody': 'kcd1_npc_deadBody',
                  'npc_invisible': 'gluemapper_npc_invisible'}
FACTION_PATH = 'Libs/Tables/rpg/FactionTree__gluemapper_people.xml'


def register_factions(files, ai, native_tree=None, level_id=1001):
    """Create source settlement/faction records under a native semantic parent.

    Source factions and superfactions are separate tables, not a parent chain.
    Retain the entire native tree when extending one of its existing roots.
    """
    if not ai.get('source_factions') and ai.get('source_soul', {}).get('faction') in (None, '', '0'):
        return None  # Source helpers with no faction must not become civilians.
    if ai.get('source_superfaction') is not None:
        if native_tree is None:
            raise ValueError('Native faction tree is required for semantic faction registration')
        root = ET.fromstring(files[FACTION_PATH]) if FACTION_PATH in files else copy.deepcopy(native_tree)
        tree = root.find('FactionTree')
        source, = ai['source_factions']
        name = 'gluemapper_kcd1_faction_' + source['faction_id']
        existing = tree.findall(".//Faction[@Name='" + name + "']")
        if len(existing) > 1: raise ValueError('Duplicate source faction registration: ' + name)
        if existing:
            # The bulk catalog has already created the source relationship
            # graph, including groups with no native counterpart.
            return name
        # A patch of the civilians root must retain ALL its existing children.
        # A partial root replacement would delete the retail regions' factions.
        group = ai['source_superfaction']['superfaction_name']
        parents = {'Civilians': 'civilians', 'Soldiers': 'civilians',
                   'playersBestFriendsForever': 'civilians', 'SazavaMonasteryMonks': 'civilians',
                   'Bandits': 'enemies', 'Cumans': 'enemies'}
        if group not in parents:
            raise ValueError('Native superfaction adapter is required for ' + group)
        parent = tree.find(".//Faction[@Name='" + parents[group] + "']")
        if parent is None: raise ValueError('Native faction ancestor missing')
        children = parent.find('Children')
        if children is None: children = ET.SubElement(parent, 'Children')
        region_name = 'gluemapper_kcd1' + ('_enemies' if parents[group] == 'enemies' else '')
        region = children.find("Faction[@Name='" + region_name + "']")
        if region is None:
            region = ET.SubElement(children, 'Faction', Name=region_name, LevelId=str(level_id))
        children = region.find('Children')
        if children is None: children = ET.SubElement(region, 'Children')
        source, = ai['source_factions']
        location = source.get('location_id')
        if not location: raise ValueError('Source faction has no location')
        settlement_name = region_name + '_location_' + location.replace('-', '_')
        settlement = children.find("Faction[@Name='" + settlement_name + "']")
        if settlement is None:
            settlement = ET.SubElement(children, 'Faction', Name=settlement_name, LocationId=location, Labels='settlement')
            ET.SubElement(settlement, 'Children')
        name = 'gluemapper_kcd1_faction_' + source['faction_id']
        members = settlement.find('Children')
        if members.find("Faction[@Name='" + name + "']") is None:
            node = ET.SubElement(members, 'Faction', Name=name, Comment='KCD1 ' + source['faction_name'])
            reputation = float(source['player_reputation'])
            if not math.isfinite(reputation) or not -1 <= reputation <= 1:
                raise ValueError('Invalid source faction reputation')
            ET.SubElement(ET.SubElement(node, 'Relations'), 'Relation', target='player', reputation=str(reputation))
        files[FACTION_PATH] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        return name
    raise ValueError('Source superfaction record must be resolved before faction registration')


def register_guard_selector(blob):
    """Extend the native guard region gate; retain its class gate and all rules."""
    root = ET.fromstring(blob)
    selectors = root.findall(".//customSelector[@name='isGuard']")
    if len(selectors) != 1: raise ValueError('Ambiguous native guard selector')
    gates = [node for node in selectors[0].findall('or') if node.find('hasFaction') is not None]
    if len(gates) != 1 or not selectors[0].findall('.//hasSocialClass'):
        raise ValueError('Native guard selector contract changed')
    gate = gates[0]
    if not any(n.get('name') == 'gluemapper_kcd1' for n in gate):
        ET.SubElement(gate, 'hasFaction', name='gluemapper_kcd1')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def capture_person_ai(tables, soul, actor, instance, entities):
    by_id = entities if isinstance(entities, dict) else {e.get('EntityId'): e for e in entities}
    links = []
    for link in actor.findall('EntityLinks/Link'):
        target = by_id.get(link.get('TargetId'))
        if target is None:
            raise ValueError('Unresolved source person link: ' + repr(link.attrib))
        links.append(dict(label=link.get('Name'), target=dict(target.attrib),
                          source_xml=ET.tostring(target, encoding='unicode'), status='pending'))
    brain = tables.row('ai/brain', 'brain_id', soul['brain_id'])
    social = (tables.row('rpg/social_class', 'social_class_id', soul['social_class_id'])
              if soul.get('social_class_id') not in (None, '', '-1') else None)
    faction = (tables.row('rpg/faction', 'faction_id', soul['faction'])
               if soul.get('faction') not in (None, '', '0') else None)
    superclass = tables.row('rpg/superfaction', 'superfaction_id', faction['superfaction_id']) if faction else None
    schedule = []
    indices = sorted({int(k.split('_')[1]) for k in soul if k.startswith('activity_') or k.startswith('time_')})
    for index in indices:
        activity, clock = soul.get(f'activity_{index}'), soul.get(f'time_{index}')
        if not activity and not clock: continue
        if not activity or not clock: raise ValueError('Incomplete source daycycle entry')
        hour, minute = map(int, clock.split(':'))
        # Authored overnight jobs use 24:xx, e.g. a tavern patron sleeping at
        # 24:14. Retain the source day offset while normalizing the clock.
        if not (0 <= hour < 48 and 0 <= minute < 60): raise ValueError('Invalid daycycle time')
        schedule.append(dict(activity=activity, start_minute=(hour % 24) * 60 + minute,
                             source_time=clock, source_day_offset=hour // 24, status='pending'))
    return dict(source_brain=brain, source_social_class=social, source_factions=[faction] if faction else [], source_superfaction=superclass,
                source_soul=dict(soul), source_instance=ET.tostring(instance, encoding='unicode'),
                source_properties=ET.tostring(actor.find('Properties'), encoding='unicode')
                    if actor.find('Properties') is not None else None,
                links=links, schedule=schedule)


def native_brain_and_class(ai, brains, classes):
    name = ai['source_brain']['brain_name']
    target_name = BRAIN_ADAPTERS.get(name)
    if target_name is None:
        raise ValueError('No native person brain adapter for ' + name)
    matches = [e for e in brains.iter('brain') if e.get('brain_name') == target_name]
    social = ai.get('source_social_class') or {'social_class_name': 'none'}
    social_name = social['social_class_name']
    if social_name == 'soldier' and social.get('soul_crime_role_id') == '2':
        social_name = 'soldier_crimeAuthority'
    socials = [e for e in classes.iter('social_class') if e.get('social_class_name') == social_name]
    if len(matches) != 1 or len(socials) != 1:
        raise ValueError('Missing/ambiguous native brain or social class contract')
    return matches[0].get('brain_id'), socials[0].get('social_class_id')


def stat_operations(soul):
    operations = []
    for old, new in STATS.items():
        value = soul.get(old)
        if value in ('', None): continue
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError('Invalid source NPC stat: ' + old)
        operations.append(('setAttribute', dict(stat=new, value=value)))
    return operations


def apply_person_properties(npc, actor):
    source = actor.find('Properties')
    if source is None: return
    props = ET.SubElement(npc, 'Properties')
    for name in PROPERTIES:
        value = source.get(name)
        if value is not None:
            if value not in ('0', '1', 'true', 'false'):
                raise ValueError('Invalid source perception flag: ' + name)
            props.set(name, value)
    # Legacy bIdleUntilFirstPatch and quest script contexts are NOT copied into
    # KCD2. They require converted quest ownership, not a permanently idle actor.


def receipt(person, target_soul, namespace):
    report = copy.deepcopy(person['ai'])
    coverage = {}
    for field, value in person['soul'].items():
        status, adapter = 'pending', None
        if value in ('', None): status = 'unset_in_source'
        elif field in STATS: status, adapter = 'mapped', 'Storm.setAttribute:' + STATS[field]
        elif field in ('soul_id', 'soul_name', 'xp_multiplier', 'digestion_multiplier', 'initial_clothing_dirt'):
            status, adapter = 'mapped', 'native soul record'
        elif field == 'brain_id':
            status, adapter = 'mapped', report['source_brain']['brain_name'] + ' -> ' + BRAIN_ADAPTERS[report['source_brain']['brain_name']]
        elif field == 'social_class_id': status, adapter = 'mapped', 'native social class by name'
        elif field == 'faction': status, adapter = 'partial', 'native faction ancestry and initial player relation'
        elif field.startswith('character_') or field in ('initial_clothing_preset_id', 'soul_archetype_id'):
            status, adapter = 'delegated', 'character_person_appearance and character_source_clothing receipts'
        elif field in ('computer_name', 'timestamp', 'level_name'):
            status, adapter = 'source_metadata', 'retained in this receipt'
        coverage[field] = dict(status=status, adapter=adapter, source_value=value)
    report.update(person=person['name'], namespace=namespace,
        native_brain=target_soul.get('brain_id'), native_social_class=target_soul.get('social_class_id'),
        native_faction=target_soul.get('factionName'),
        mapped_stats=STATS, perception_properties=PROPERTIES,
        appearance='character_person_appearance', equipment='character_source_clothing',
        field_coverage=coverage, complete=False, runtime_verified=False,
        pending=['inter-faction relationships, location and faction UI', 'daycycle and home/work graph',
                 'combat-level and skills scale conversion', 'source inventory beyond outfit',
                 'quest contexts and original dialogue/voice', 'save/load and reaction verification'])
    return json.dumps(report, indent=2, ensure_ascii=True).encode('utf-8')
