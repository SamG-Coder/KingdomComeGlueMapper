"""Bind cinematic body presets to registered residents of the destination.

The cast remains in its temporary cinematic layer. Only body/clothing identity
changes: animation tracks, authored props, player variants and timings survive.
"""
from collections import defaultdict
import xml.etree.ElementTree as E

from character_crime_districts import contains
from cinematic_scene import guid_key


def player_variants(index, plan, native):
    """Recognize actor phase variants by their shared player performance.

    cutsceneOnly also contains ordinary extras: that faction alone is not an
    identity binding. Require the same body animation, outfit and model as a
    Hero in this sequence, and preserve ordinary cinematic extras for recasting.
    """
    hero_types={k for k,a in native['archetypes'].items() if a.get('soul_archetype_name')=='Hero'}
    performances={};heroes=set()
    for owner in plan['entity_guids']:
        for node in index.entities[owner].findall('Sequence/Nodes/Node'):
            if not node.get('EntityGUID'):continue
            key=guid_key(node.get('EntityGUID'));e=index.entities[key]
            body=e.find('Properties/Body');props=e.find('Properties')
            if e.get('EntityClass')!='AnimChar' or body is None:continue
            s=native['souls'].get(body.get('guidBodyPresetId'))
            if s is None:continue
            primary=node.find("Track[@paramType='Animation']")
            clips={k.get('anim') for k in primary.findall('Key') if k.get('anim')} if primary is not None else set()
            performances[key]=(clips,body.get('guidClothingPresetId'),props.get('object_Model'),s)
            if s.get('soul_archetype_id') in hero_types:heroes.add(key)
    variants={}
    for key,(clips,outfit,model,soul) in performances.items():
        if key in heroes or soul.get('factionName')!='cutsceneOnly':continue
        for hero in heroes:
            hero_clips,hero_outfit,hero_model,_=performances[hero]
            shared=clips & hero_clips
            if shared and outfit==hero_outfit and model==hero_model:
                variants[key]=dict(player_entity=index.entities[hero].get('Name'),shared_animations=sorted(shared))
                break
    return heroes,variants


def catalog(resources):
    souls, classes, archetypes, outfits, inventories = {}, {}, {}, {}, {}
    equipment = {}
    for path, blob in resources.items():
        if not path.lower().endswith('.xml'): continue
        root = E.fromstring(blob)
        for tag, attr, target in [('soul', 'soul_id', souls),
                ('social_class', 'social_class_id', classes),
                ('soul_archetype', 'soul_archetype_id', archetypes),
                ('clothing_preset', 'clothing_preset_name', outfits),
                ('InventoryPreset', 'Name', inventories)]:
            for node in root.iter(tag):
                key = node.get(attr)
                if not key: continue
                if key in target and E.tostring(node) != E.tostring(target[key]):
                    raise ValueError('Conflicting cinematic cast definition: ' + key)
                target[key] = node
        if '/storm/equipment/' in path.lower():
            for rule in root.findall('rules/rule'):
                selectors = rule.findall('selectors/*')
                ops = rule.findall('operations/setInventory')
                if len(selectors) == 1 and selectors[0].tag == 'hasName' and len(ops) == 1:
                    name = selectors[0].get('name')
                    preset = ops[0].get('preset')
                    if name in equipment and equipment[name] != preset:
                        raise ValueError('Conflicting resident equipment: ' + name)
                    equipment[name] = preset
    return dict(souls=souls, classes=classes, archetypes=archetypes,
                outfits=outfits, inventories=inventories, equipment=equipment)


def outfit_for(soul, data):
    """Follow imported inventory references; do not infer outfit from a name."""
    found = set()
    def walk(name, ancestors):
        if name in ancestors: raise ValueError('Cyclic cinematic inventory: ' + name)
        preset = data['inventories'].get(name)
        if preset is None: raise ValueError('Missing resident inventory: ' + str(name))
        before = set(found)
        for node in preset.findall('ClothingPresetRef'): found.add(node.get('Name'))
        for node in preset.findall('InventoryPresetRef'):
            # Loot tables do not contain clothes unless their reference closure
            # explicitly says so. They still must exist to make selection safe.
            walk(node.get('Name'), ancestors | {name})
        if preset.get('Mode') != 'All' and found != before:
            raise ValueError('Cinematic cast requires a deterministic resident outfit: ' + name)
    walk(data['equipment'].get(soul.get('soul_name')), set())
    if len(found) != 1: raise ValueError('Expected one resident cinematic outfit: ' + soul.get('soul_name'))
    name = found.pop()
    if name not in data['outfits']: raise ValueError('Missing resident clothing preset: ' + name)
    return data['outfits'][name]


def bind_cast(index, plans, files, resources, native_resources, selection):
    from world_crime_services import area_geometry, vector
    from campaign_trigger_areas import read_areas
    from character_factions import LEAF
    local, native = catalog(resources), catalog(native_resources)
    mission = E.fromstring(files['objects_mission0.xml'])
    district = next(e for e in mission if e.get('EntityGuid') == selection['district'])
    shape = area_geometry(district, {a.guid: a for a in read_areas(files['triggerareas.fubar'])['areas']})
    registrations = {s.findtext('EntityGuid'): s.findtext('SharedSoulGuid') for s in
                     E.fromstring(files['whdata_0']).findall('SoulList/Souls/Soul')}
    factions = {LEAF + f.get('faction_id') for f in E.fromstring(resources['source/faction.xml']).iter('row')
                if f.get('location_id') == selection['location']}
    def human(soul):
        # Use the shipped archetype semantics rather than source numeric IDs.
        a = native['archetypes'].get(soul.get('soul_archetype_id'))
        return a.get('soul_archetype_name') if a is not None else None
    residents = {}
    for e in mission:
        soul = local['souls'].get(registrations.get(e.get('EntityGuid')))
        if (soul is not None and e.get('Pos') and e.get('EntityClass') in ('NPC','NPC_Female')
                and soul.get('factionName') in factions and contains(shape, vector(e))
                and human(soul) in ('NPC', 'NPC_Female')):
            residents[soul.get('soul_id')] = soul
    if not residents: raise ValueError('No registered destination residents for cinematic cast')
    default_class = next((key for key,c in native['classes'].items() if c.get('social_class_name') == 'none'), None)
    def profession(soul):
        key = soul.get('social_class_id') or default_class
        if key not in native['classes']: raise ValueError('Unclassified cinematic profession')
        return key
    needs, preserved = {}, []
    for plan in plans:
        needed = needs[plan['name']] = {}
        _,variants=player_variants(index,plan,native)
        for key in plan['entity_guids']:
            e = index.entities[key]
            if e.get('EntityClass') != 'AnimChar': continue
            body = e.find('Properties/Body')
            if body is None: continue  # authored animals/props using a model
            identity = body.get('guidBodyPresetId')
            source = native['souls'].get(identity)
            if source is None:
                raise ValueError('Unclassified cinematic body preset: ' + str(identity))
            # These are authored presentation bodies, not ordinary residents.
            # In particular, native player injury variants use the NPC
            # archetype, then replace the Hero body part-way through a scene.
            # Archetype alone must never make them eligible for crowd casting.
            if key in variants:
                preserved.append(dict(scene=plan['name'],entity=e.get('Name'),soul=identity,
                                      reason='native_player_performance_variant',**variants[key]))
                continue
            if human(source) in ('NPC', 'NPC_Female'): needed[identity] = source
    # Reserve exact professions first, so a generic spectator cannot consume
    # the sole local bailiff before the bailiff role is assigned.
    counts = defaultdict(int)
    for s in residents.values(): counts[human(s), profession(s)] += 1
    def order(item):
        key, s = item
        n = counts[human(s), profession(s)]
        return (0 if n else 1, n, key)
    def adapter(assignments):
        def adapt(e):
            body = e.find('Properties/Body')
            if e.get('EntityClass') != 'AnimChar' or body is None: return e
            choice = assignments.get(body.get('guidBodyPresetId'))
            if choice is None: return e  # native player/damage variants and animals
            soul, outfit = choice
            body.set('guidBodyPresetId', soul.get('soul_id'))
            body.set('guidClothingPresetId', outfit.get('clothing_preset_id'))
            # A substitute must never bring a Troskovice face back.
            body.set('guidSubstituteBodyPresetId', soul.get('soul_id'))
            e.find('Properties').attrib.pop('guidBeardHairOverrideId', None)
            return e
        return adapt
    adapters, preferred, reports = {}, {}, []
    for scene, needed in needs.items():
        assignments, used = {}, set()
        for identity, source in sorted(needed.items(), key=order):
            peers = [s for k, s in residents.items() if k not in used and human(s) == human(source)]
            if not peers: raise ValueError('Not enough distinct local cinematic residents for ' + human(source))
            exact = [s for s in peers if profession(s) == profession(source)]
            role = native['classes'][profession(source)].get('soul_crime_role_id')
            if exact: peers = exact
            else:
                peers = [s for s in peers if native['classes'][profession(s)].get('soul_crime_role_id') == role]
                if not peers: raise ValueError('No local resident for cinematic crime role: ' + str(role))
            chosen = min(peers, key=lambda s: (s.get('soul_id') != preferred.get(identity), s.get('soul_id')))
            outfit = outfit_for(chosen, local)
            expected = 'Female' if human(chosen) == 'NPC_Female' else 'Male'
            if outfit.get('gender') != expected: raise ValueError('Resident outfit gender mismatch')
            used.add(chosen.get('soul_id'));preferred[identity]=chosen.get('soul_id')
            assignments[identity] = (chosen, outfit)
            reports.append(dict(scene=scene,source_soul=identity, source_name=source.get('soul_name'),
                soul=chosen.get('soul_id'), name=chosen.get('soul_name'),
                clothing=outfit.get('clothing_preset_id'), exact_profession=bool(exact)))
        adapters[scene] = adapter(assignments)
    return adapters, dict(residents=len(residents), assignments=reports,preserved_variants=preserved,
                       policy='registered_resident_faction_location_gender_and_crime_role', runtime_verified=False)
