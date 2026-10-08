"""Convert authored doors to native interactive entities and animated assets.

This is a class adapter, not a story-specific replacement. The native door
contract is discovered from shipped entities with the same rig and helper.
Source meshes, material, transform, key identity and lock state are retained.
Policies which depend on unconverted ownership areas are not silently dropped.
"""
from collections import defaultdict
import copy
import hashlib
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET
import zipfile

from audit_character_bodies import Assets, bone_table
from campaign_dependencies import ImportPlan
from campaign_quest_graph import xml
from character_skin_upgrade import upgrade_skin, limit_skin_influences
from clothing_regions import read_chunks
from upgrade_map import read


def normalized(value):
    return value.replace('\\', '/').lower()


def from_ast(node):
    element = ET.Element(node['op'], node['attributes'])
    element.text = node.get('text')
    element.extend(from_ast(child) for child in node.get('children', []))
    return element


def rig_mapping(source, target):
    """Match named joints and an unambiguous renamed root; never guess a pivot."""
    old, new = bone_table(read_chunks(source)), bone_table(read_chunks(target))
    if not old or not new:
        raise ValueError('Animated prop is missing a compiled rig')
    native = {bone['name'].lower(): bone for bone in new}
    if len(native) != len(new):
        raise ValueError('Ambiguous native prop joint names')
    roots = [bone for bone in new if bone['parent'] is None]
    mapping = {}
    for bone in old:
        match = native.get(bone['name'].lower())
        if match is None and bone['parent'] is None and len(roots) == 1:
            match = roots[0]
        if match is None:
            raise ValueError('Unconverted animated prop joint: ' + bone['name'])
        mapping[bone['name']] = match['name']
    for bone in old:
        match = native[mapping[bone['name']].lower()]
        parent = mapping.get(bone['parent'])
        if parent != match['parent']:
            raise ValueError('Animated prop joint hierarchy differs: ' + bone['name'])
    return mapping


class NativeDoorCatalog:
    def __init__(self, game, stack):
        self.assets = Assets(Path(game), stack)
        self.contracts = defaultdict(dict)
        self.models = {}
        for path in sorted((Path(game) / 'Data/Levels').glob('*/level.pak')):
            with zipfile.ZipFile(path) as archive:
                try:
                    data = read(archive, 'objects_mission0.xml')
                except KeyError:
                    continue
            for entity in ET.fromstring(data).iter('Entity'):
                if entity.get('EntityClass') != 'AnimDoor':
                    continue
                props = entity.find('Properties')
                if props is None or not props.get('object_Model'):
                    continue
                model = normalized(props.get('object_Model'))
                if model not in self.models:
                    try:
                        blob, _ = self.assets.get(model)
                        self.models[model] = normalized(ET.fromstring(blob).find('Model').get('File'))
                    except (KeyError, AttributeError):
                        self.models[model] = None
                rig = self.models[model]
                if not rig:
                    continue
                helper = props.get('soclass_SmartObjectHelpers', '')
                contract = (props.get('esDoorAnimSet', ''), props.get('guidSmartObjectType', ''))
                if all(contract):
                    self.contracts[(rig, helper)][contract] = dict(
                        archive=str(path), entity=entity.get('Name'), model=model)

    def resolve(self, rig, helper):
        matches = self.contracts.get((normalized(rig), helper), {})
        if len(matches) != 1:
            raise ValueError('No unambiguous native door contract for ' + rig + ':' + helper)
        (animation, smart_object), evidence = next(iter(matches.items()))
        return dict(animation_set=animation, smart_object=smart_object, helper=helper, evidence=evidence)


class AnimatedDoorAssetAdapter:
    def __init__(self, assets, native, namespace):
        self.assets, self.native = assets, native
        self.prefix = 'objects/gluecampaign/' + namespace.lower() + '/doors/'

    def source_definition(self, identity):
        if self.assets.adapter is None:
            self.assets.adapter = self.assets.factory()
        pack = self.assets.adapter
        archive, entry = pack.index[identity]
        root = ET.fromstring(read(archive, entry.filename))
        model = root.find('Model')
        if root.tag != 'CharacterDefinition' or model is None:
            raise ValueError('Expected source door character definition')
        return root, normalized(model.get('File', '')), dict(archive=str(archive.filename), entry=entry.filename)

    def plan(self, identity):
        root, rig, provenance = self.source_definition(identity)
        if not rig.endswith('.chr'):
            raise ValueError('Door character definition has no skeleton')
        native_rig, target_provenance = self.native.assets.get(rig)
        # The native skeleton owns its animation database. Keep this path so
        # loading the CDF loads the corresponding retail CHRPARAMS as well.
        params, _ = self.native.assets.get(str(PurePosixPath(rig).with_suffix('.chrparams')))
        if ET.fromstring(params).find('AnimationList') is None:
            raise ValueError('Native door skeleton has no animation list')
        pack = self.assets.adapter
        archive, entry = pack.index[rig]
        mapping = rig_mapping(read(archive, entry.filename), native_rig)
        dependencies = []
        for element in root.iter():
            if element.get('Material'):
                material = normalized(element.get('Material')).removesuffix('.mtl') + '.mtl'
                dependencies.append(('assets', material))
        for attachment in root.findall('AttachmentList/Attachment'):
            if attachment.get('Type') != 'CA_SKIN' or not attachment.get('Binding', '').lower().endswith('.skin'):
                raise ValueError('Door attachment requires a separate transform adapter')
        return ImportPlan(dependencies, dict(model=identity, rig=rig, bone_mapping=mapping,
            source=provenance, native_rig=target_provenance))

    def convert(self, identity, plan, registered):
        root, rig, _ = self.source_definition(identity)
        native_rig, _ = self.native.assets.get(rig)
        pack = self.assets.adapter
        # Native AnimDoor:IsRightDoor reads the model filename, so a digest
        # alone loses an operational property. Retain the source basename.
        prefix = self.prefix + hashlib.sha256(identity.encode()).hexdigest()[:16] + '/'
        target = prefix + PurePosixPath(identity).name
        root.find('Model').set('File', rig)
        files, meshes = {}, []
        for element in root.iter():
            if element.get('Material'):
                source = normalized(element.get('Material')).removesuffix('.mtl') + '.mtl'
                element.set('Material', registered['assets'][source].removesuffix('.mtl'))
        for index, attachment in enumerate(root.findall('AttachmentList/Attachment')):
            source = normalized(attachment.get('Binding'))
            upgraded, evidence = upgrade_skin(pack.mesh(source), native_rig,
                bone_mapping=plan.source['bone_mapping'], geometry_mode='preserve', clear_hiding=False)
            upgraded, influences = limit_skin_influences(upgraded)
            destination = prefix + str(index) + '_' + PurePosixPath(source).name
            attachment.set('Binding', destination)
            files[destination] = upgraded
            meshes.append(dict(source=source, output=destination, geometry=evidence, influences=influences))
        files[target] = xml(root)
        # mesh() can discover embedded materials beyond CDF overrides.
        files.update(pack.emitted)
        return target, files, dict(source=plan.source, meshes=meshes,
            native_animation_list=True, geometry_mode='preserve', runtime_validated=False)


class DoorEntityConversion:
    def __init__(self, tables, animated, native):
        self.tables, self.animated, self.native = tables, animated, native

    def plan(self, record):
        source = from_ast(record['entity'])
        props = source.find('Properties')
        if props is None:
            raise ValueError('Source door has no authored properties')
        model = normalized(props.get('object_Model', ''))
        if not model:
            raise ValueError('Source door has no model')
        row = self.tables.row('ai/so_smart_object', 'so_smart_object_id', props.get('guidSmartObjectType', ''))
        if row.get('so_smart_object_name') != 'so_door':
            raise ValueError('Custom door behavior requires its own source conversion')
        _, rig, _ = self.animated.source_definition(model)
        contract = self.native.resolve(rig, props.get('soclasses_SmartObjectHelpers', ''))
        dependencies = [('animated_props', model)]
        key = props.find('Lock').get('guidItemClassId', '') if props.find('Lock') is not None else ''
        if key == '00000000-0000-0000-0000-000000000000':
            key = ''
        if key:
            dependencies.append(('items', key))
        return ImportPlan(dependencies, dict(model=model, key=key, contract=contract,
                                            source_type=row, properties=ET.tostring(props, encoding='unicode')))

    def apply(self, entity, plan, registered):
        source = ET.fromstring(plan.source['properties'])
        # Native ownership and crime links replace the legacy interior enum.
        # Do not claim those doors are registered until their policy is ported.
        if source.get('esInteriorType', 'undefined') != 'undefined':
            raise ValueError('Door interior ownership/closing policy requires area-link conversion: ' + source.get('esInteriorType'))
        props = copy.deepcopy(source)
        props.attrib.pop('esInteriorType', None)
        props.set('object_Model', registered['animated_props'][plan.source['model']])
        contract = plan.source['contract']
        props.set('guidSmartObjectType', contract['smart_object'])
        props.set('soclass_SmartObjectHelpers', contract['helper'])
        props.attrib.pop('soclasses_SmartObjectHelpers', None)
        props.set('esDoorAnimSet', contract['animation_set'])
        props.set('esNavCompoment', 'Door')
        props.set('bSaved_by_game', '1')
        lock = props.find('Lock')
        if lock is None:
            lock = ET.SubElement(props, 'Lock')
        if lock.get('bNeverLock', '0') not in ('0', 'false'):
            raise ValueError('Door never-lock policy requires native state conversion')
        lock.attrib.pop('bNeverLock', None)
        lock.attrib.pop('bLockpickIsLegal', None)
        if source.find('Lock') is not None and source.find('Lock').get('bLockpickIsLegal', '0') not in ('0', 'false'):
            raise ValueError('Door legal lockpicking requires crime-area conversion')
        # KDC1 GUID keys are authored items. A new generated dynamic key would
        # bypass quest ownership, so retain only the converted source key.
        lock.set('bCanUnlockWithDynamicKey', '0')
        if plan.source['key']:
            lock.set('guidItemClassId', registered['items'][plan.source['key'].lower()])
        lock.set('bNeverLockByPassingNPC', '0')
        for old, new in (('fKeepOpenFrom', 'fKeepUnlockedFrom'), ('fKeepOpenUntil', 'fKeepUnlockedTo')):
            if old in props.attrib:
                lock.set(new, props.attrib.pop(old))
        entity.append(props)
        return dict(model=props.get('object_Model'), contract=contract, source_key=plan.source['key'],
            source_locked=lock.get('bLocked'), physics=props.find('Physics').attrib if props.find('Physics') is not None else {},
            source_identity_preserved=True, runtime_validated=False)
