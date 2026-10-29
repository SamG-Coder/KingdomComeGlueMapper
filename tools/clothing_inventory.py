"""Validate imported NPC equipment through gameplay references, not mesh families.

This is a structural check. It does not simulate Storm selector precedence,
runtime inventory changes, fitting, or rendering.
"""
import xml.etree.ElementTree as ET


def relevant_resource(name):
    name = name.replace('\\', '/').lower()
    return name.endswith('.xml') and (name.startswith('libs/storm/equipment/') or
        any('/'+table+'__' in name for table in
            ('inventorypreset', 'clothing_preset', 'item', 'charactercomponent')))


def validate_equipment(resources, people):
    """Return one check per receipt-owned namespace/person.

    Duplicate unrelated loot tables are harmless to this audit. Ambiguous or
    missing definitions reached by an imported actor are explicit errors.
    """
    definitions = {k: {} for k in ('inventory', 'clothing', 'item', 'component')}
    bindings = {}
    parse_errors = []
    for path, blob in resources.items() if isinstance(resources, dict) else resources:
        if not relevant_resource(path):
            continue
        try:
            root = ET.fromstring(blob)
        except ET.ParseError as exc:
            parse_errors.append(dict(code='equipment_xml_invalid', asset=path, message=str(exc)))
            continue
        lower = path.lower()
        if lower.startswith('libs/storm/equipment/'):
            for rule in root.iter('rule'):
                actors = [n.get('name') for n in rule.findall('./selectors//hasName')]
                for operation in rule.findall('./operations/setInventory'):
                    for actor in actors:
                        bindings.setdefault(actor, []).append((operation.get('preset'), path))
            continue
        for node in root.iter():
            kind = identity = None
            if '/inventorypreset__' in lower and node.tag == 'InventoryPreset':
                kind, identity = 'inventory', node.get('Name')
            elif '/clothing_preset__' in lower and node.tag == 'clothing_preset':
                kind, identity = 'clothing', node.get('clothing_preset_name')
            elif '/item__' in lower and node.get('Id'):
                kind, identity = 'item', node.get('Id').lower()
            elif '/charactercomponent__' in lower and node.tag == 'Clothing':
                kind, identity = 'component', node.get('Name')
            if identity:
                definitions[kind].setdefault(identity, []).append((path, node))

    results = []
    for namespace, person in sorted(people.items()):
        issues = list(parse_errors)
        seen_inventory, clothes, components = set(), set(), set()
        def issue(code, message, **extra):
            row = dict(code=code, message=message, **extra)
            if row not in issues:
                issues.append(row)
        def resolve(kind, identity, chain):
            candidates = definitions[kind].get(identity, [])
            if not candidates:
                issue('equipment_reference_missing', f'Missing {kind} definition: {identity}', chain=chain)
                return None
            # Whitespace differences do not constitute another definition.
            def signature(node):
                return (node.tag, tuple(sorted(node.attrib.items())), (node.text or '').strip(),
                        tuple(signature(child) for child in node))
            if len({signature(node) for _, node in candidates}) != 1:
                issue('equipment_reference_ambiguous', f'Conflicting {kind} definition: {identity}',
                      chain=chain, assets=[path for path, _ in candidates])
                return None
            return candidates[0][1]
        def visit_clothing(identity, chain):
            node = resolve('clothing', identity, chain)
            if node is None:
                return
            clothes.add(identity)
            guids = [(n.text or '').strip().lower() for n in node.findall('./Items/Guid')]
            if not guids:
                issue('equipment_outfit_empty', 'Clothing preset has no items: '+identity, chain=chain)
            for guid in guids:
                item = resolve('item', guid, chain+[guid])
                if item is None:
                    continue
                component = item.get('Clothing')
                if not component:
                    issue('equipment_clothing_binding_missing', 'Outfit item has no clothing binding: '+guid, chain=chain)
                elif resolve('component', component, chain+[guid, component]) is not None:
                    components.add(component)
        def visit(identity, chain):
            if identity in chain:
                issue('equipment_inventory_cycle', 'Cyclic inventory reference: '+str(identity), chain=chain+[identity])
                return
            if identity in seen_inventory:
                return
            seen_inventory.add(identity)
            chain = chain+[identity]
            node = resolve('inventory', identity, chain)
            if node is None:
                return
            for ref in node.iter('InventoryPresetRef'):
                visit(ref.get('Name'), chain)
            for ref in node.iter('ClothingPresetRef'):
                visit_clothing(ref.get('Name'), chain+[ref.get('Name')])
        roots = bindings.get(person, [])
        if not roots:
            issue('equipment_storm_binding_missing', 'No named equipment rule for '+person)
        for identity, path in roots:
            visit(identity, [])
        if not clothes:
            issue('equipment_outfit_unreachable', 'No clothing preset reachable from NPC equipment rules')
        results.append(dict(namespace=namespace, person=person,
            status='error' if issues else 'checked', runtime_verified=False,
            bindings=[dict(inventory=n, asset=p) for n, p in roots],
            inventories=sorted(n for n in seen_inventory if n), clothing_presets=sorted(clothes),
            components=sorted(components), issues=issues))
    return results


def require_equipment(resources, people):
    checks = validate_equipment(resources, people)
    failures = [r for r in checks if r['status'] == 'error']
    if failures:
        raise ValueError('Imported NPC equipment validation failed: '+ '; '.join(
            r['person']+': '+r['issues'][0]['message'] for r in failures))
    return checks


def audit_assets(assets, people):
    """Read effective package/overlay tables, retaining conflicting definitions."""
    import zipfile
    selected = {n for n in assets.index if relevant_resource(n)}
    collisions = selected.intersection(assets.collisions)
    grouped = {}
    for name in selected-collisions:
        path, member, crc, size = assets.index[name]
        grouped.setdefault(path, []).append((name, member, crc, size))
    resources = []
    for path, entries in grouped.items():
        with zipfile.ZipFile(path) as archive:
            for name, member, crc, size in entries:
                info = archive.getinfo(member)
                if (info.CRC, info.file_size) != (crc, size):
                    raise ValueError('Package changed during equipment inspection: '+name)
                resources.append((name, archive.read(member)))
    if collisions:
        for path in sorted((assets.root/'Data').glob('*.pak')):
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    if member.replace('\\', '/').lower() in collisions:
                        resources.append((member, archive.read(member)))
    return validate_equipment(resources, people)
