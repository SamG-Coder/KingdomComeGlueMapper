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
BRAIN_ADAPTERS = {'npc_daycycle': 'npc_basic'}
FACTION_PATH = 'Libs/Tables/rpg/FactionTree__gluemapper_people.xml'


def register_factions(files, ai):
    """Merge source ancestry without assigning everyone the template's faction.

    Only ancestry and the source initial relation to the player are represented
    here. Location membership, UI discovery and inter-faction relations need
    their own adapters and remain reported as pending.
    """
    root = ET.fromstring(files[FACTION_PATH]) if FACTION_PATH in files else ET.Element('database', name='barbora')
    tree = root.find('FactionTree')
    if tree is None: tree = ET.SubElement(root, 'FactionTree', version='1')
    container = tree
    for source in reversed(ai['source_factions']):
        name = 'gluemapper_kcd1_faction_' + source['faction_id']
        node = container.find(f"Faction[@Name='{name}']")
        if node is None:
            if any(n.get('Name') == name for n in tree.iter('Faction')):
                raise ValueError('Conflicting source faction ancestry: ' + name)
            node = ET.SubElement(container, 'Faction', Name=name,
                                 Comment='KCD1 ' + source['faction_name'])
            reputation = source.get('player_reputation')
            if reputation not in ('', None):
                if not math.isfinite(float(reputation)) or not 0 <= float(reputation) <= 1:
                    raise ValueError('Invalid source faction reputation')
                ET.SubElement(ET.SubElement(node, 'Relations'), 'Relation',
                              target='player', reputation=reputation)
        container = node.find('Children')
        if container is None: container = ET.SubElement(node, 'Children')
    files[FACTION_PATH] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    return name


def capture_person_ai(tables, soul, actor, instance, entities):
    by_id = {e.get('EntityId'): e for e in entities}
    links = []
    for link in actor.findall('EntityLinks/Link'):
        target = by_id.get(link.get('TargetId'))
        if target is None:
            raise ValueError('Unresolved source person link: ' + repr(link.attrib))
        links.append(dict(label=link.get('Name'), target=dict(target.attrib),
                          source_xml=ET.tostring(target, encoding='unicode'), status='pending'))
    brain = tables.row('ai/brain', 'brain_id', soul['brain_id'])
    social = tables.row('rpg/social_class', 'social_class_id', soul['social_class_id'])
    faction, chain, seen = soul['faction'], [], set()
    while faction and faction not in seen:
        seen.add(faction)
        row = tables.row('rpg/faction', 'faction_id', faction)
        chain.append(row)
        parent = row['superfaction_id']
        if parent == faction: break  # Source uses a self-parent for root factions.
        if parent in seen: raise ValueError('Cyclic source faction ancestry')
        faction = parent
    schedule = []
    for index in range(9):
        activity, clock = soul.get(f'activity_{index}'), soul.get(f'time_{index}')
        if not activity and not clock: continue
        if not activity or not clock: raise ValueError('Incomplete source daycycle entry')
        hour, minute = map(int, clock.split(':'))
        if not (0 <= hour < 24 and 0 <= minute < 60): raise ValueError('Invalid daycycle time')
        schedule.append(dict(activity=activity, start_minute=hour * 60 + minute, status='pending'))
    return dict(source_brain=brain, source_social_class=social, source_factions=chain,
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
    social_name = ai['source_social_class']['social_class_name']
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
        elif field == 'brain_id': status, adapter = 'mapped', 'npc_daycycle -> npc_basic; daycycle activities pending'
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
