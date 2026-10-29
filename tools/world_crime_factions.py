"""Give imported settlement factions the native witness/authority contracts.

KCD2's guard and bailiff selectors gate professions by native regional faction
membership. Merely preserving soul_crime_role_id does not satisfy that gate.
Keep imported identities/relations and extend only the regional predicate.
"""
from pathlib import Path
import xml.etree.ElementTree as E
import zipfile

from character_factions import LEAF
from upgrade_map import read, xml

BASE = 'Libs/Storm/common/base.xml'
LEGACY_LABEL = 'gluemapper_imported_settlement'


def authority_selectors(blob, factions):
    root = E.fromstring(blob)
    changed = False
    if not factions or any(not n.startswith(LEAF) for n in factions):
        raise ValueError('Authority regions must be imported faction identities')
    for name in ('isGuard', 'isBailiff'):
        matches = root.findall("customSelectors/customSelector[@name='" + name + "']")
        if len(matches) != 1 or matches[0].get('mode') != 'and':
            raise ValueError('Unsupported native authority selector: ' + name)
        # Do not broaden the profession predicate or bypass any other condition.
        branches = [b for b in matches[0].findall('or') if len(b) and
                    all(c.tag == 'hasFaction' or
                        (c.tag == 'hasFactionLabel' and c.get('name') == LEGACY_LABEL) for c in b)]
        if len(branches) != 1 or not any(c.tag == 'hasFaction' for c in branches[0]):
            raise ValueError('Missing native authority region predicate: ' + name)
        # Version 6 used an undeclared custom label. Replace that owned marker
        # with registered faction names; do not require a new label enum.
        for old in branches[0].findall("hasFactionLabel[@name='" + LEGACY_LABEL + "']"):
            branches[0].remove(old); changed = True
        existing = {c.get('name') for c in branches[0] if c.tag == 'hasFaction'}
        for faction in sorted(set(factions) - existing):
            E.SubElement(branches[0], 'hasFaction', name=faction); changed = True
    return xml(root) if changed else blob


def prepare(resources, mission, metadata, source_factions, classes, base):
    """Select actual residents using source location and native crime roles.

    No names, faction IDs or coordinates are special-cased. Unplaced cinematic
    actors, foreign visitors, animals and factions represented only by renegades
    cannot turn an unrelated faction into a settlement police force.
    """
    mission, metadata = E.fromstring(mission), E.fromstring(metadata)
    source = {LEAF + r.get('faction_id'): r for r in E.fromstring(source_factions).iter('row')
              if r.get('faction_id')}
    roles = {r.get('social_class_id'): r.get('soul_crime_role_id')
             for r in E.fromstring(classes).iter() if r.get('social_class_id')}
    locations, souls = set(), {}
    for path, blob in resources.items():
        lower = path.lower()
        if '/location__' in lower:
            locations.update(r.get('location_id') for r in E.fromstring(blob).iter('location')
                             if r.get('ui_type') == 'Common')
        if '/soul__' in lower:
            for r in E.fromstring(blob).iter('soul'):
                key = r.get('soul_id')
                if key in souls and souls[key] != r.attrib:
                    raise ValueError('Conflicting imported soul: ' + str(key))
                souls[key] = dict(r.attrib)
    placed_locations = {p.get('guidLocationId') for p in mission.findall('Entity/Properties')}
    locations &= placed_locations
    registrations = {r.findtext('EntityGuid'): r.findtext('SharedSoulGuid')
                     for r in metadata.findall('SoulList/Souls/Soul')}
    selected, actors = {}, []
    for entity in mission:
        if entity.get('EntityClass') not in ('NPC', 'NPC_Female'):
            continue
        soul = souls.get(registrations.get(entity.get('EntityGuid')), {})
        faction = soul.get('factionName')
        location = source.get(faction, {}).get('location_id')
        role = roles.get(soul.get('social_class_id'))
        if location in locations and role in ('1', '2'):
            selected[faction] = location
            actors.append(dict(entity=entity.get('EntityGuid'), name=entity.get('Name'),
                               faction=faction, crime_role=role))
    if not selected:
        raise ValueError('No registered imported settlement residents for crime reporting')
    changes, found = {}, set()
    for path, blob in resources.items():
        if '/factiontree' not in path.lower():
            continue
        root = E.fromstring(blob)
        dirty = False
        for node in root.iter('Faction'):
            name = node.get('Name')
            if name not in selected:
                continue
            found.add(name)
            # Both faction tables may contain the same leaf. Update every copy
            # so archive/table load order cannot disable witness broadcasting.
            # Retail FactionTree uses whitespace-separated registered labels,
            # e.g. "city settlement", not SmartAreaShape's semicolon format.
            labels = set(node.get('Labels', '').replace(';', ' ').split()) - {LEGACY_LABEL}
            expected = ' '.join(sorted(labels | {'settlement'}))
            if node.get('Labels') != expected:
                node.set('Labels', expected)
                dirty = True
            existing = node.get('LocationId')
            if existing and existing != selected[name]:
                raise ValueError('Imported faction location conflicts with source: ' + name)
            if not existing:
                node.set('LocationId', selected[name]); dirty = True
        if dirty:
            changes[path] = xml(root)
    if found != selected.keys():
        raise ValueError('Missing imported settlement faction: ' + ', '.join(sorted(selected.keys() - found)))
    patched = authority_selectors(base, selected)
    changes[BASE] = patched
    return changes, dict(version=2, factions=selected, actors=actors,
                        authority_selectors=['isGuard', 'isBailiff'],
                        settlement_information_broadcast=True, runtime_verified=False)


def package_resources(package, source, target, level_pak):
    """Shared by fresh Setup and saved-package migration; no appearance rebuild."""
    from campaign_sources import RetailSourceReader
    from character_person import native_xml
    package, source, target = map(Path, (package, source, target))
    with zipfile.ZipFile(package/'Data/gluemappertravel.pak') as z:
        resources = {n: read(z, n) for n in z.namelist()
                     if n.lower().startswith('libs/tables/rpg/') and n.lower().endswith('.xml')
                     and any(s in n.lower() for s in ('/soul__', '/location__', '/factiontree'))}
        base = read(z, BASE) if BASE in z.namelist() else None
    if base is None:
        with zipfile.ZipFile(target/'Data/IPL_GameData.pak') as z:
            base = read(z, BASE)
    with zipfile.ZipFile(target/'Data/Tables.pak') as z:
        classes = xml(native_xml(read(z, 'Libs/Tables/rpg/social_class.xml')))
    with RetailSourceReader(source) as reader:
        factions = reader(source, {'faction': ('Tables.pak', 'Libs/Tables/rpg/faction.xml')})['faction']['data']
    with zipfile.ZipFile(package/level_pak) as z:
        return prepare(resources, read(z, 'objects_mission0.xml'), read(z, 'whdata_0'), factions, classes, base)
