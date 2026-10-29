"""Discover and relocate authored in-world scenes without dropping dependencies.

Both games serialize SequenceVersion 3 / paramIdVersion 8. This is a scene
container adapter, not a character/animation converter. KCD1 assets must pass
through an explicit adapter before a scene can be emitted for KCD2.
"""
import copy
import math
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

from upgrade_map import read, xml


def guid_key(value):
    value = (value or '').replace('-', '').replace('{', '').replace('}', '').lower()
    if not 1 <= len(value) <= 32 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Invalid scene entity GUID: ' + value)
    # This is an archive-local lookup key, not a cross-game GUID conversion.
    # Some KCD1 sequence tracks retain 128-bit editor references. Keep those
    # distinct and report unresolved references; never truncate to a guessed
    # runtime identity and accidentally animate another actor.
    return value.zfill(16 if len(value)<=16 else 32)


class SceneIndex:
    """One level read, reused by a campaign-wide audit and all scene builders."""
    def __init__(self, documents, profiles):
        self.documents = documents
        self.entities = {}
        self.ids = defaultdict(list)
        self.sequences = defaultdict(list)
        self.locations = {}
        self.profiles = defaultdict(list)
        self.unidentified_entities = 0
        for path, root in documents.items():
            for entity in root.findall('Entity'):
                if not entity.get('EntityGuid'):
                    if entity.find('Sequence') is not None:
                        raise ValueError('Sequence owner has no GUID: ' + entity.get('Name', ''))
                    self.unidentified_entities += 1
                    continue
                key = guid_key(entity.get('EntityGuid'))
                if key in self.entities:
                    raise ValueError('Duplicate source scene entity GUID: ' + key)
                self.entities[key] = entity
                self.locations[key] = path
                self.ids[entity.get('EntityId')].append(key)
                sequence = entity.find('Sequence')
                if sequence is not None:
                    self.sequences[sequence.get('Name')].append(key)
        for profile in profiles.findall('.//GameProfile'):
            for layer in profile.findall('GameLayers/GameLayer'):
                self.profiles[layer.text.lower()].append(profile.findtext('Name'))

    @classmethod
    def from_archive(cls, archive):
        docs = {n: ET.fromstring(read(archive, n)) for n in archive.namelist()
                if n.lower() == 'objects_mission0.xml' or
                n.lower().startswith('layers/') and n.lower().endswith('.xml')}
        names = {n.lower(): n for n in archive.namelist()}
        profiles = (ET.fromstring(read(archive, names['whdata_1']))
                    if 'whdata_1' in names else ET.Element('Root'))
        result = cls(docs, profiles)
        result.has_profile_metadata = 'whdata_1' in names
        return result

    def references(self, entity):
        """Typed references only; numbers in animation keys are not entity IDs."""
        found, missing = set(), []
        for node in entity.iter():
            if node.tag=='Link' and node.get('TargetGuid') not in (None,'0') and node.get('TargetId') not in (None,'0'):
                id_matches=self.ids.get(node.get('TargetId'),[])
                if len(id_matches)==1 and guid_key(node.get('TargetGuid'))!=id_matches[0]:
                    missing.append(dict(kind='inconsistent_link_identity',value=node.get('TargetGuid'),
                                        target_id=node.get('TargetId')))
            for attr in ('TargetGuid', 'EntityGUID'):
                value = node.get(attr)
                if not value or value == '0': continue
                key = guid_key(value)
                if key in self.entities: found.add(key)
                else: missing.append(dict(kind=attr, value=value))
            for attr in (('TargetId',) if node.tag == 'Link' else
                         ('ID',) if node.tag == 'CleanupArea' else
                         ('Id',) if node.tag == 'Entity' and node is not entity else
                         ('ParentId',) if node is entity else ()):
                value = node.get(attr)
                if not value or value == '0': continue
                matches = self.ids.get(value, [])
                if len(matches) == 1: found.add(matches[0])
                else: missing.append(dict(kind=attr, value=value, matches=len(matches)))
        return found, missing

    def plan(self, name):
        candidates = self.sequences.get(name, [])
        if len(candidates) != 1:
            raise ValueError('Missing or ambiguous scene: ' + name)
        owner = candidates[0]
        path = self.locations[owner]
        # A cinematic layer owns lights, audio, props and actors which may not
        # have tracks. A mission-level sequence has no exclusive layer.
        seeds = ({guid_key(e.get('EntityGuid')) for e in self.documents[path].findall('Entity')}
                 if path.lower().startswith('layers/') else {owner})
        selected, pending, missing = set(), list(sorted(seeds)), []
        while pending:
            key = pending.pop()
            if key in selected: continue
            selected.add(key)
            refs, absent = self.references(self.entities[key])
            missing.extend(dict(entity=self.entities[key].get('Name'), **item) for item in absent)
            pending.extend(sorted(refs - selected))
        scene = self.entities[owner].find('Sequence')
        parameters = Counter(n.get('paramType') for n in scene.iter('Track'))
        assets = sorted({v for key in selected for n in self.entities[key].iter()
                         for k, v in n.attrib.items()
                         if k in ('Geometry', 'object_Model', 'Material', 'material_Material') and v})
        layers = sorted({self.entities[key].get('Layer') for key in selected
                         if self.entities[key].get('Layer')})
        return dict(name=name, sequence_guid=owner, source_document=path,
                    entity_guids=sorted(selected), layer_owned_entities=len(seeds),
                    external_entities=sorted(selected-seeds), missing_references=missing,
                    entity_classes=dict(Counter(self.entities[k].get('EntityClass') for k in selected)),
                    profiles=sorted({p for layer in layers for p in self.profiles.get(layer.lower(), [])}),
                    owner_profiles=self.profiles.get(self.entities[owner].get('Layer', '').lower(), []),
                    layers=layers, assets=assets, tracks=dict(parameters),
                    sequence_version=scene.get('SequenceVersion'),
                    parameter_versions=sorted({n.get('paramIdVersion') for n in scene.iter('Node')
                                               if n.get('paramIdVersion')}),
                    runtime_verified=False)


def _vector(value):
    values = tuple(map(float, value.split(',')))
    if len(values) != 3 or not all(map(math.isfinite, values)):
        raise ValueError('Invalid scene position: ' + value)
    return values


def _numbers(values):
    return ','.join(format(v, '.12g') for v in values)


def translate_position_track(track, offset):
    """Translate absolute spline ordinates; preserve times, tangents and flags.

    We deliberately do not approximate a yaw rotation by mixing independently
    keyed X/Y curves. Scene orientation remains native until a full curve
    transform is supplied.
    """
    subs = list(track)
    if len(subs) != 3 or any(n.tag != 'NewSubTrack' for n in subs):
        raise ValueError('Unsupported cinematic position track encoding')
    for axis, sub in enumerate(subs):
        for node, attribute in [(sub, 'defaultValue'), *((k, 'value') for k in sub.findall('Key'))]:
            raw = node.get(attribute)
            if raw is None: raise ValueError('Missing cinematic spline ordinate')
            pair = raw.split(',')
            if len(pair) != 2 or not all(math.isfinite(float(x)) for x in pair):
                raise ValueError('Invalid cinematic spline ordinate')
            node.set(attribute, pair[0] + ',' + format(float(pair[1])+offset[axis], '.12g'))


def convert(index, plan, *, offset, identity, first_id, layer, source_game,
            entity_adapter=None, bindings=None, static_bindings=()):
    """Emit a private, closed scene; reject missing assets/references up front.

    bindings maps source GUIDs to already-registered destination entities.
    They are referenced but never moved or overwritten. KCD1 entity adapters
    must convert models, materials, clothing and animation contracts; there is
    intentionally no 'copy it and hope' default for KCD1.
    """
    if source_game not in ('kcd1', 'kcd2'):
        raise ValueError('Unknown scene source game')
    if source_game == 'kcd1' and entity_adapter is None:
        raise ValueError('KCD1 scene requires a character/material/animation adapter')
    if plan['missing_references']:
        raise ValueError('Scene has unresolved entity references: ' + plan['name'])
    if plan['sequence_version'] != '3' or set(plan['parameter_versions']) - {'8'}:
        raise ValueError('Unsupported cinematic sequence format')
    offset = tuple(offset)
    if len(offset) != 3 or not all(math.isfinite(v) for v in offset):
        raise ValueError('Invalid scene translation')
    bindings = {guid_key(k): v for k, v in (bindings or {}).items()}
    static_bindings = {guid_key(k) for k in static_bindings}
    if not static_bindings <= set(bindings): raise ValueError('Static scene property must be an existing binding')
    selected = plan['entity_guids']
    if not set(bindings) <= set(selected): raise ValueError('Binding outside scene closure')
    ids, guids, records = {}, {}, []
    for key in selected:
        source = index.entities[key]
        if key in bindings:
            target = bindings[key]
            ids[source.get('EntityId')] = target.get('EntityId')
            guids[key] = target.get('EntityGuid')
        else:
            ids[source.get('EntityId')] = str(first_id+len(records))
            guids[key] = identity('scene/'+layer+'/'+key)
            records.append(key)
    if len(set(guids.values())) != len(guids) or len(set(ids.values())) != len(ids):
        raise ValueError('Destination cinematic identity collision')
    parented = {k for k in selected if index.entities[k].get('ParentId') not in (None, '0')}
    for key in parented:
        seen, current = {key}, key
        while current in parented:
            current = index.ids[index.entities[current].get('ParentId')][0]
            if current in seen: raise ValueError('Cyclic cinematic parent chain')
            seen.add(current)
    root = ET.Element('Objects')
    areas, sequence_areas = [], []
    for key in records:
        entity = copy.deepcopy(index.entities[key])
        if entity_adapter is not None:
            entity = entity_adapter(entity)
            if not isinstance(entity, ET.Element):
                raise ValueError('Scene adapter did not return a converted entity')
        entity.set('EntityId', ids[index.entities[key].get('EntityId')])
        entity.set('EntityGuid', guids[key])
        entity.set('Layer', layer)
        entity.attrib.pop('EditorLayer', None)
        if entity.get('Pos') and key not in parented:
            entity.set('Pos', _numbers(a+b for a,b in zip(_vector(entity.get('Pos')), offset)))
        if key in parented:
            # Child transforms and animation curves are parent-local. Moving
            # both the parent and these values would apply the offset twice.
            entity.set('ParentId', ids[entity.get('ParentId')])
        for node in entity.findall('Sequence/Nodes/Node'):
            reference = guid_key(node.get('EntityGUID')) if node.get('EntityGUID') else None
            if reference in bindings:
                for track in list(node.findall('Track')):
                    kind = track.get('paramType')
                    # Prefab members can retain unused, parent-local defaults
                    # even though their exported entity transform is absolute.
                    # An existing destination prop owns its static transform.
                    if kind in ('Position', 'Rotation') and not track.findall('.//Key'):
                        node.remove(track)
                    elif reference in static_bindings:
                        if kind in ('Position', 'Rotation', 'Animation', 'Event') and track.findall('.//Key'):
                            raise ValueError('Static scene foundation has animated behaviour')
                        if kind == 'Visibility' or not track.findall('.//Key'):
                            node.remove(track)
            tracks = node.findall("Track[@paramType='Position']")
            if not tracks: continue
            if not node.get('EntityGUID'):
                raise ValueError('Position track has no entity coordinate space')
            if guid_key(node.get('EntityGUID')) not in parented:
                for track in tracks: translate_position_track(track, offset)
        for node in entity.iter():
            for attr in ('TargetGuid', 'EntityGUID'):
                if node.get(attr) and node.get(attr) != '0':
                    node.set(attr, guids[guid_key(node.get(attr))])
            if node.tag == 'Link' and node.get('TargetId') not in (None, '0'):
                node.set('TargetId', ids[node.get('TargetId')])
            if node.tag == 'CleanupArea': node.set('ID', ids[node.get('ID')])
            if node.tag == 'Entity' and node is not entity and node.get('Id') not in (None, '0'):
                node.set('Id', ids[node.get('Id')])
        id_to_guid = {ids[index.entities[k].get('EntityId')]: guids[k] for k in selected}
        for link in entity.findall('EntityLinks/Link'):
            if link.get('TargetId') in id_to_guid:
                link.set('TargetGuid', id_to_guid[link.get('TargetId')])
        if entity.find('Area') is not None:
            # Native SequenceArea cleanup volumes are stored inline with the
            # cinematic layer, not in triggerareas.fubar. Do not reinterpret
            # them as gameplay trigger volumes.
            (sequence_areas if entity.get('EntityClass')=='SequenceArea' else areas).append(entity.get('EntityGuid'))
        root.append(entity)
    return xml(root), dict(scene=plan['name'], emitted_entities=len(root), external_bindings=len(bindings),
                           offset=list(offset), layer=layer, areas=areas, sequence_areas=sequence_areas, source_game=source_game,
                           entity_guid_map=guids, entity_id_map=ids, runtime_verified=False)
