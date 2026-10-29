"""Native player-context and voice contracts for the converted Henry campaign.

Retail Storm does not run the character/voice assignment tasks used by the
native authoring pipeline. Materialize those identities before packaging.
"""
from collections import defaultdict
from functools import lru_cache
import hashlib
from pathlib import Path
import xml.etree.ElementTree as E
import zipfile

from upgrade_map import read, xml

TRAVEL = 'Data/gluemappertravel.pak'


def henry_context(blob):
    """Keep the native Henry branch active for this Henry-only campaign."""
    root = E.fromstring(blob)
    nodes = root.find('./Skald/Project/Nodes')
    if nodes is None:
        raise ValueError('Missing converted campaign project nodes')
    expected = E.fromstring('''<Nodes>
      <State Name="gm_player_henry_active" TypeT="bool">
        <Constant Name="DefaultValue" Value="true"/>
      </State>
      <GameContextTrigger Name="gm_player_henry_context">
        <Constant Name="Context" Value="player_henry"/>
        <Edge From="gm_player_henry_active.State" To="IsActive"/>
      </GameContextTrigger>
    </Nodes>''')
    names = {n.get('Name') for n in expected}
    existing = [n for n in nodes if n.get('Name') in names]
    def signature(n):
        return n.tag, n.attrib, [signature(c) for c in n]
    if existing:
        if ([signature(n) for n in sorted(existing, key=lambda n: n.get('Name'))]
                != [signature(n) for n in sorted(expected, key=lambda n: n.get('Name'))]):
            raise ValueError('Converted player context has conflicting nodes')
        return blob
    if any(n.find("Constant[@Name='Context'][@Value='player_henry']") is not None
           for n in nodes.findall('GameContextTrigger')):
        raise ValueError('Converted project already has another Henry context owner')
    nodes.extend(expected)
    return xml(root)


@lru_cache(maxsize=2)
def _catalog(path, size, modified):
    # One read per native table revision, not per imported person.
    from character_person import native_xml
    with zipfile.ZipFile(path) as z:
        archetypes = {a.get('soul_archetype_id'): a.attrib
                      for a in native_xml(read(z, 'Libs/Tables/rpg/soul_archetype.xml')).iter('soul_archetype')}
        characters = {c.get('skald_character_name'): c.attrib
                      for c in native_xml(read(z, 'Libs/Tables/skald/skald_character.xml')).iter('skald_character')}
        voices = {v.get('voice_id'): v.attrib
                  for v in native_xml(read(z, 'Libs/Tables/skald/voice.xml')).iter('voice')}
        pools = defaultdict(set)
        required = ('age', 'body_type', 'gender', 'unique_assets', 'voice_id')
        generic = {name: c for name, c in characters.items()
                   if name.startswith('char_GENERIC_') and not c.get('unique_assets')
                   and all(k in c for k in required) and c['voice_id'] in voices}
        for name in z.namelist():
            lower = name.replace('\\', '/').lower()
            filename = lower.rsplit('/', 1)[-1]
            if not (lower.startswith('libs/tables/rpg/') and lower.endswith('.xml')
                    and (filename == 'soul.xml' or filename.startswith('soul__'))):
                continue
            for soul in native_xml(read(z, name)).iter('soul'):
                character = soul.get('skald_character_name')
                if character in generic:
                    pools[soul.get('soul_archetype_id'), soul.get('social_class_id')].add(
                        (character, soul.get('voice_group_name', '')))
    return archetypes, generic, voices, pools


def missing_voices(resources, target):
    """Assign absent voices using native peers of the same archetype/class.

    Existing explicit dubbing bindings are authoritative. Animals are untouched.
    A profession with only named native actors uses the shipped commoner voice
    pool of the same gender; it never borrows a named actor's identity.
    """
    changed = {}
    assignments = []
    missing = []
    for name, blob in resources.items():
        lower = name.lower()
        if not (lower.startswith('libs/tables/rpg/soul__') and lower.endswith('.xml')):
            continue
        root = E.fromstring(blob)
        for soul in root.iter('soul'):
            if not soul.get('skald_character_name'):
                missing.append((name, root, soul))
    if not missing:
        return changed, assignments
    if target is None:
        raise ValueError('Native game tables required to assign imported voices')
    path = (Path(target)/'Data/Tables.pak').resolve()
    stat = path.stat()
    archetypes, characters, voices, pools = _catalog(str(path), stat.st_size, stat.st_mtime_ns)
    for name, root, soul in missing:
        archetype = archetypes.get(soul.get('soul_archetype_id'))
        if archetype is None:
            raise ValueError('Unknown imported voice archetype: ' + str(soul.get('soul_name')))
        kind = archetype['soul_archetype_name']
        if kind not in ('NPC', 'NPC_Female'):
            continue
        prefix = 'char_GENERIC_' + {'NPC': 'MAN', 'NPC_Female': 'WOMAN'}[kind] + '_'
        peers = pools.get((soul.get('soul_archetype_id'), soul.get('social_class_id')), set())
        if any(not c.startswith(prefix) for c, group in peers):
            raise ValueError('Native voice pool has mismatched gender')
        policy = 'native_archetype_and_social_class'
        if not peers:
            peers = {(c, '') for c in characters if c.startswith(prefix + 'COMMONER_')}
            policy = 'native_generic_commoner_for_named_profession'
        if not peers:
            raise ValueError('No native generic voice for ' + str(soul.get('soul_name')))
        candidates = sorted(peers)
        identity = soul.get('soul_id')
        if not identity:
            raise ValueError('Imported voice assignment requires stable soul identity')
        choice = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], 'little') % len(candidates)
        character, group = candidates[choice]
        # Generic Skald characters are intentionally shared by native souls and
        # have no unique appearance assets. Soul, equipment and Storm names stay
        # owned by the imported actor.
        soul.set('skald_character_name', character)
        if group:
            soul.set('voice_group_name', group)
        else:
            soul.attrib.pop('voice_group_name', None)
        changed[name] = root
        voice = voices[characters[character]['voice_id']]
        assignments.append(dict(soul=soul.get('soul_name'), character=character,
                                voice=voice['voice_technical_name'], voice_id=voice['voice_id'],
                                voice_group=group, policy=policy))
    return {name: xml(root) for name, root in changed.items()}, assignments


def upgrade(package, target):
    """Shared finalization for clean setup and both saved-build paths."""
    from setup_gameplay import PROJECT
    from setup_package_upgrade import rewrite
    from setup_campaign import digest
    package = Path(package)
    with zipfile.ZipFile(package/TRAVEL) as z:
        project = read(z, PROJECT)
        resources = {n: read(z, n) for n in z.namelist()
                     if n.lower().startswith('libs/tables/rpg/soul__') and n.lower().endswith('.xml')}
    changes, voices = missing_voices(resources, target)
    repaired = henry_context(project)
    if repaired != project:
        changes[PROJECT] = repaired
    if changes:
        rewrite(package, TRAVEL, changes)
    return ({TRAVEL: digest(package/TRAVEL)} if changes else {}), dict(
        version=1, player_context='player_henry', assigned_voices=voices, runtime_verified=False)
