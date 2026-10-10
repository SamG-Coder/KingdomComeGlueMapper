"""Create the whole source faction dependency catalog and directed relations."""
import copy
import math
import xml.etree.ElementTree as ET
from character_person_ai import FACTION_PATH
from upgrade_map import xml


def register_catalog(tables, native_tree, level_id=1001):
    root = copy.deepcopy(native_tree); tree = root.find('FactionTree')
    groups = tables.get('rpg/superfaction')['rows']
    factions = tables.get('rpg/faction')['rows']
    relationships = tables.get('rpg/superfaction2superfaction_relationship')['rows']
    # Only engine categories with known equivalents inherit those native
    # selectors. Quest-specific groups are created with their own source graph,
    # never guessed to be civilian/enemy from a substring in a quest's name.
    parents = {'Civilians': 'civilians', 'Soldiers': 'civilians',
               'RatzigSoldiers': 'civilians', 'playersBestFriendsForever': 'civilians',
               'SazavaMonasteryMonks': 'civilians', 'Bandits': 'enemies', 'Cumans': 'enemies'}
    aliases, nodes = {}, {}
    for group in groups:
        key, name = group['superfaction_id'], group['superfaction_name']
        if name == 'Player': aliases[key] = 'player'; continue
        parent = tree
        category = parents.get(name)
        if category:
            ancestor = tree.find(".//Faction[@Name='" + category + "']")
            if ancestor is None: raise ValueError('Missing native faction category: ' + category)
            children = ancestor.find('Children')
            if children is None: children = ET.SubElement(ancestor, 'Children')
            region_name = 'gluemapper_kcd1' + ('_enemies' if category == 'enemies' else '')
            region = children.find("Faction[@Name='" + region_name + "']")
            if region is None: region = ET.SubElement(children, 'Faction', Name=region_name, LevelId=str(level_id))
            parent = region.find('Children')
            if parent is None: parent = ET.SubElement(region, 'Children')
        alias = 'gluemapper_kcd1_superfaction_' + key
        aliases[key] = alias
        nodes[key] = ET.SubElement(parent, 'Faction', Name=alias, LevelId=str(level_id), Comment='KCD1 ' + name)
    for relation in relationships:
        origin, target = relation['from_superfaction_id'], relation['to_superfaction_id']
        if origin not in aliases or target not in aliases: raise ValueError('Missing source superfaction relation endpoint')
        if origin not in nodes: continue  # Don't overwrite the retail player root.
        value = float(relation['relationship'])
        if not math.isfinite(value) or not -1 <= value <= 1: raise ValueError('Invalid faction relation')
        relations = nodes[origin].find('Relations')
        if relations is None: relations = ET.SubElement(nodes[origin], 'Relations')
        ET.SubElement(relations, 'Relation', target=aliases[target], reputation=str(value))
    for faction in factions:
        key = faction['superfaction_id']
        if key not in aliases: raise ValueError('Missing source faction parent')
        if key not in nodes:
            # Source player-owned factions still get source-scoped leaves.
            parent = tree.find(".//Faction[@Name='player']")
        else: parent = nodes[key]
        children = parent.find('Children')
        if children is None: children = ET.SubElement(parent, 'Children')
        attributes = dict(Name='gluemapper_kcd1_faction_' + faction['faction_id'],
                          LevelId=str(level_id), Comment='KCD1 ' + faction['faction_name'])
        if faction.get('location_id'): attributes['LocationId'] = faction['location_id']
        leaf = ET.SubElement(children, 'Faction', **attributes)
        value = float(faction['player_reputation'])
        if not math.isfinite(value) or not -1 <= value <= 1: raise ValueError('Invalid source reputation')
        ET.SubElement(ET.SubElement(leaf, 'Relations'), 'Relation', target='player', reputation=str(value))
    return {FACTION_PATH: xml(root)}
