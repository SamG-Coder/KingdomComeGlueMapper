"""Retail source adapters used by automatic campaign dependency import."""
from collections import OrderedDict
import copy
import hashlib
from pathlib import Path, PurePosixPath
import re
import threading
import uuid
import weakref
import xml.etree.ElementTree as ET
import zipfile

from campaign_dependencies import ImportPlan
from campaign_entity_links import guid_value, native_guid
from campaign_quest_graph import identifier, xml
from campaign_trigger_areas import read_areas, write_areas
from campaign_inventory import resolve_inventory, TABLES as INVENTORY_TABLES
from quest_import import ast
from static_assets import asset_pack, character_definition
from upgrade_map import read


# Parsed rows shared by every SourceTables in the process, keyed by the content hash the
# retail reader already computed, so a changed archive can never serve stale rows. A build
# creates SourceTables per import and parsed tables about 1,400 times. Bounded by the size
# of the cached table XML, least recently used first.
_ROWS = OrderedDict()
_ROWS_LOCK = threading.Lock()
ROWS_CACHE_BYTES = 64 * 1024 ** 2


def _table_rows(table, item, row_factory=dict):
    key = (table, item['candidates'][-1]['sha256'], len(item['data']))
    with _ROWS_LOCK:
        cached = _ROWS.get(key)
        if cached is not None:
            _ROWS.move_to_end(key)
    if cached is None:
        cached = (tuple(dict(r.attrib) for r in ET.fromstring(item['data']).findall('./table/rows/row')), len(item['data']))
        with _ROWS_LOCK:
            _ROWS[key] = cached
            while sum(size for _, size in _ROWS.values()) > ROWS_CACHE_BYTES:
                _ROWS.popitem(last=False)
    return [row_factory(row) for row in cached[0]]


def _row_edit(operation):
    def edit(self, *args, **kwargs):
        try:
            return operation(self, *args, **kwargs)
        finally:
            self._invalidate()
    return edit


class _SourceRow(dict):
    """Keep the existing editable row contract without leaving stale indexes."""
    __slots__ = ('_invalidate',)

    def __init__(self, values, invalidate):
        super().__init__(values)
        self._invalidate = invalidate

    __setitem__ = _row_edit(dict.__setitem__)
    __delitem__ = _row_edit(dict.__delitem__)
    __ior__ = _row_edit(dict.__ior__)
    clear = _row_edit(dict.clear)
    pop = _row_edit(dict.pop)
    popitem = _row_edit(dict.popitem)
    setdefault = _row_edit(dict.setdefault)
    update = _row_edit(dict.update)

    def __copy__(self):
        return dict(self)

    def __deepcopy__(self, memo):
        return copy.deepcopy(dict(self), memo)

    def __reduce__(self):
        return dict, (dict(self),)


class SourceTables:
    def __init__(self, source, reader):
        self.source, self.reader, self.cache, self.indexes = source, reader, {}, {}
        self.exposed = set()

    def _invalidate(self, table):
        for key in list(self.indexes):
            if key[0] == table:
                del self.indexes[key]

    def _get(self, table):
        if table not in self.cache:
            item = self.reader(self.source, {'table': ('Tables.pak', 'Libs/Tables/' + table + '.xml')})['table']
            owner = weakref.ref(self)
            def invalidate():
                tables = owner()
                if tables is not None:
                    tables._invalidate(table)
            rows = _table_rows(table, item, lambda row: _SourceRow(row, invalidate))
            self.cache[table] = dict(rows=rows, provenance=item['candidates'])
        return self.cache[table]

    def get(self, table):
        # The caller can retain this list, insert ordinary dicts or replace it.
        # Such edits cannot be observed through row callbacks, so keep the old
        # scan semantics for exposed tables. Pure row lookups retain indexing.
        self.exposed.add(table)
        self._invalidate(table)
        return self._get(table)

    def row(self, table, field, value, optional=False):
        rows = self._get(table)['rows']
        if table in self.exposed:
            matches = [r for r in rows if r.get(field, '').lower() == value.lower()]
        else:
            index = self.indexes.get((table, field))
            if index is None:
                index = {}
                for r in rows:
                    index.setdefault(r.get(field, '').lower(), []).append(r)
                self.indexes[table, field] = index
            matches = index.get(value.lower(), [])
        if not matches and optional:
            return {}
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous retail record: ' + table + ':' + value)
        return matches[0]


class LazyAdapter:
    def __init__(self, factory):
        self.factory, self.adapter = factory, None

    def materialize(self):
        if self.adapter is None:
            self.adapter = self.factory()
        return self.adapter

    def plan(self, identity):
        return self.materialize().plan(identity)

    def convert(self, *args):
        return self.adapter.convert(*args)


class InventoryAdapter:
    def __init__(self, tables, namespace, item_name=None):
        self.tables, self.namespace = tables, identifier(namespace).lower()
        self.item_name = item_name

    def plan(self, identity):
        source = resolve_inventory(identity, {n: self.tables.get('inventory/' + n)['rows'] for n in INVENTORY_TABLES})
        return ImportPlan([('items', item) for item in source['item_dependencies']], source)

    def convert(self, identity, plan, registered):
        source = plan.source
        definition = source['definition']
        if definition.get('read_only', '').lower() == 'true' or float(definition.get('restock_interval') or 0) != 0:
            raise ValueError('Inventory restock/read-only policy needs native conversion')
        from container_loot import compile_inventory
        item_names = {}
        for item in source['item_dependencies']:
            target_item = registered['items'][item.lower()]
            if self.item_name is None:
                raise ValueError('Inventory preset requires registered native item names')
            item_names[target_item] = self.item_name(target_item)
        root, original, policy = compile_inventory(dict(source, source_id=identity, status='authored'),
                                                   registered['items'], item_names)
        target = self.namespace + '_inventory_' + identity.replace('-', '_')
        for node in root.iter():
            if node.tag in ('InventoryPreset', 'InventoryPresetRef'):
                name = node.get('Name', '')
                if name == original or name.startswith(original+'_'):
                    node.set('Name', target+name[len(original):])
        path = 'Libs/Tables/item/InventoryPreset__' + target + '.xml'
        return target, {path: xml(root)}, dict(source_id=identity, items=len(source['direct_items']), selection_policy=policy)


class SoulAdapter:
    def __init__(self, tables, souls, person_registration=None):
        self.tables = tables
        self.person_registration = person_registration
        self.instances = {}
        for soul in souls.findall('./SoulList/Souls/Soul'):
            shared = soul.findtext('SharedSoulGuid')
            if shared:
                self.instances.setdefault(shared.lower(), []).append(soul)

    def plan(self, identity):
        row = self.tables.row('rpg/soul', 'soul_id', identity)
        instances = self.instances.get(identity, [])
        from character_instance import effective_soul
        effective = [effective_soul(row, s) for s in instances] or [dict(row)]
        inventories = {s.get('inventory_id') for s in effective}
        inventories -= {None, '', '00000000-0000-0000-0000-000000000000'}
        parts = set()
        for kind in ('body', 'head', 'hair', 'beard'):
            values = {s.get('character_' + kind + '_id') for s in effective}
            for value in values - {None, '', '00000000-0000-0000-0000-000000000000'}:
                parts.add(kind + ':' + value)
        dependencies = [] if self.person_registration is not None else [('character_parts', p) for p in sorted(parts)]
        dependencies += [('inventories', i) for i in sorted(inventories)]
        from campaign_initial_loadout import resolve as resolve_loadout
        loadouts = [resolve_loadout(self.tables, identity,
            {field: person[field] for field in ('initial_clothing_preset_id', 'initial_weapon_preset_id')
             if field in person}) for person in effective]
        dependencies += [('items', item) for item in sorted({i['item'] for p in loadouts for i in p['items']
            if self.person_registration is None or i['kind'] == 'weapon'})]
        if self.person_registration is not None:
            dependencies += [('person_names', name) for name in sorted({s.findtext('StaticData/NameStringId')
                for s in instances} - {None, ''})]
        # Appearance and AI are source identities too, not a substitute default
        # body or a cinematic-only brain assigned because registration failed.
        return ImportPlan(dependencies, dict(row=row, instances=[ast(s) for s in instances], loadouts=loadouts,
                          provenance=self.tables.get('rpg/soul')['provenance']))

    def convert(self, identity, plan, registered):
        if self.person_registration is not None:
            return self.person_registration.convert(identity, plan, registered)
        raise ValueError('Soul appearance/brain conversion must be connected to the character upgrader before registration')


class EntityAdapter:
    def __init__(self, world, souls, area_data, export_version, doors=None, controllers=None, smart_areas=None,
                 navigation=None, smart_objects=None):
        self.doors = doors
        self.controllers = controllers or {}
        self.smart_areas = smart_areas or {}
        self.smart_objects = smart_objects or {}
        from campaign_work_items import WorkItems
        self.work_items = WorkItems(world['entities'].values())
        self.entities = {}
        for record in world['entities'].values():
            text = record['attributes'].get('EntityGuid', '')
            if not re.fullmatch(r'[0-9a-fA-F]{16}', text):
                continue
            self.entities.setdefault(native_guid(int(text, 16)), []).append(record)
        self.souls = {int(s.findtext('EntityGuid')): s for s in souls.findall('./SoulList/Souls/Soul')
                      if s.findtext('EntityGuid')}
        self.areas = {a.guid: a for a in read_areas(area_data)['areas']}
        self.export_version = export_version
        self.navigation = navigation
        self.paths = None

    def plan(self, identity):
        matches = self.entities.get(identity, [])
        if len(matches) != 1:
            raise ValueError('Placed entity requires unambiguous source/profile resolution: ' + identity)
        source = matches[0]
        dependencies = []
        if source['attributes'].get('EntityClass') in ('NPC', 'NPC_Female'):
            soul = self.souls.get(guid_value(identity))
            if soul is None:
                raise ValueError('Placed actor is absent from retail SoulList: ' + identity)
            shared = soul.findtext('SharedSoulGuid')
            if not shared:
                raise ValueError('Placed actor has no shared retail soul identity: ' + identity)
            dependencies.append(('souls', shared))
        source = copy.deepcopy(source)
        if source['attributes'].get('EntityClass') == 'PickableItem':
            alias = self.work_items.alias(source)
            if alias:
                source['tool_slot_alias'] = alias
                return ImportPlan([('entities', alias)], source)
        if source['attributes'].get('EntityClass') == 'ItemSlot':
            source['work_item'] = self.work_items.prepare(source)
            dependencies.append(('items',source['work_item']['item']))
        if source['attributes'].get('EntityClass') in ('SmartObjectHolder','ItemSlot'):
            bindings = self.smart_objects.get(identity, [])
            props = [n for n in source['entity']['children'] if n['op'] == 'Properties']
            if len(props) != 1:
                raise ValueError('Work object requires one properties record')
            p = props[0]['attributes']
            if p.get('guidSmartObjectType') not in (None, '', '00000000-0000-0000-0000-000000000000'):
                if len(bindings) != 1:
                    raise ValueError('Work object requires an unambiguous campaign template binding')
                source['work_object_identity'] = bindings[0]
                dependencies.append(('smart_entities', bindings[0]))
            if p.get('soclasses_SmartObjectHelpers'):
                dependencies.append(('work_helpers', p['soclasses_SmartObjectHelpers']))
        if source['attributes'].get('EntityClass') == 'PredefinedNavigationPath':
            if self.navigation is None:
                raise ValueError('Predefined route requires its source navigation graph')
            from character_work_paths import read_paths, bind_path
            if self.paths is None:
                read_paths(read(self.navigation.target_level, 'ubernav.tmm'))
                self.paths = read_paths(read(self.navigation.level, 'ubernav.tmm'))
            original = ET.Element('Entity', source['attributes'])
            _, source['route'] = bind_path(ET.Element('Objects'), original, self.paths)
            dependencies.append(('navigation_base', self.navigation.identity))
        if source['attributes'].get('EntityClass') == 'SmartAreaShape':
            identities = self.smart_areas.get(identity, [])
            if len(identities) != 1:
                raise ValueError('Placed work area requires an unambiguous campaign template binding')
            source['area_identity'] = identities[0]
            dependencies.append(('smart_areas',identities[0]))
        if source['attributes'].get('EntityClass') == 'AnimDoor' and self.doors is not None:
            door = self.doors.plan(source)
            dependencies.extend(door.dependencies)
            source['door_conversion'] = door.source
        if source['attributes'].get('EntityClass') == 'QuestObject':
            controllers = self.controllers.get(source['attributes'].get('Name'), [])
            if len(controllers) != 1:
                raise ValueError('Quest controller requires unambiguous database registration')
            source['controller_identity'] = controllers[0]
            dependencies.append(('smart_entities', controllers[0]))
        return ImportPlan(dependencies, source)

    def convert(self, identity, plan, registered):
        if plan.source.get('tool_slot_alias'):
            return registered['entities'][plan.source['tool_slot_alias']], {}, dict(
                tool_slot_alias=plan.source['tool_slot_alias'], source_item=identity)
        source = plan.source['attributes']
        cls = source['EntityClass']
        if cls not in ('TriggerArea', 'TagPoint', 'NPC', 'NPC_Female', 'AnimDoor', 'QuestObject', 'SmartAreaShape',
                       'PredefinedNavigationPath', 'SmartObjectHolder', 'ItemSlot'):
            raise ValueError('Native entity-class conversion is not implemented: ' + cls)
        attrs = {k: source[k] for k in ('Name', 'Pos', 'Rotate', 'Scale', 'EntityClass', 'CastShadowMinSpec') if k in source}
        attrs['EntityGuid'] = identity
        # EntityId is allocated only when merging with the target level.
        attrs['EditorLayer'] = 'KDC1/' + source.get('Layer', 'Imported')
        entity = ET.Element('Entity', attrs)
        files = {}
        evidence = dict(source=plan.source['source'], source_class=cls, identity_preserved=True,
                        profile=source.get('Layer'), transform_preserved=True)
        if cls != 'ItemSlot' and source.get('ParentGuid') not in (None, '', '0'):
            raise ValueError('Parented entity attachment conversion is required: ' + identity)
        if cls == 'ItemSlot':
            self.work_items.apply(entity,plan.source,plan.source['work_item'],registered)
            evidence['work_item'] = plan.source['work_item']
            evidence['replaces_visual_entities'] = ['glue_item_'+source['EntityId']]
            if plan.source['work_item']['pickup_id']:
                evidence['replaces_visual_entities'].append('glue_item_'+plan.source['work_item']['pickup_id'])
        elif cls == 'SmartObjectHolder':
            from campaign_ai_registration import require_fields
            props = next(n for n in plan.source['entity']['children'] if n['op'] == 'Properties')
            p = props['attributes']
            require_fields(p, ('object_editorModel', 'bNo_POI', 'bPOI_discoverable_by_location',
                'bSaved_by_game', 'soclasses_SmartObjectHelpers', 'guidSmartObjectType',
                'sWH_AI_EntityCategory'), 'work-object properties')
            attrs = {k: v for k, v in p.items() if k not in
                ('object_editorModel', 'soclasses_SmartObjectHelpers', 'guidSmartObjectType')}
            if 'work_object_identity' in plan.source:
                attrs['guidSmartObjectType'] = registered['smart_entities'][plan.source['work_object_identity'].lower()]
            if p.get('soclasses_SmartObjectHelpers'):
                attrs['soclass_SmartObjectHelpers'] = registered['work_helpers'][p['soclasses_SmartObjectHelpers'].lower()]
            native_props = ET.SubElement(entity, 'Properties', attrs)
            for child in props['children']:
                if child['op'] != 'Script' or child['children']:
                    raise ValueError('Work-object script requires semantic conversion')
                from campaign_script_properties import misc_properties
                ET.SubElement(native_props, 'Script', Misc=misc_properties(child['attributes']))
            evidence['work_object'] = plan.source.get('work_object_identity')
        elif cls == 'PredefinedNavigationPath':
            ET.SubElement(entity, 'Properties', bSaved_by_game='0')
            evidence['route'] = plan.source['route']
        elif cls == 'SmartAreaShape':
            from campaign_smart_areas import place_area, source_geometry
            area_identity = plan.source['area_identity']
            place_area(entity, plan.source['entity'], registered['smart_areas'][area_identity.lower()])
            number = guid_value(identity)
            geometry = self.areas.get(number)
            if geometry is None:
                geometry = source_geometry(plan.source['entity'])
            files['world/triggerareas/' + identity + '.fubar'] = write_areas([geometry], self.export_version)
            evidence.update(vertices=len(geometry.points), area_template=area_identity)
        elif cls == 'TriggerArea':
            number = guid_value(identity)
            if number not in self.areas:
                raise ValueError('Source trigger polygon is missing: ' + identity)
            ET.SubElement(entity, 'Properties', bSaved_by_game='0', bTrackAlways='1')
            files['world/triggerareas/' + identity + '.fubar'] = write_areas([self.areas[number]], self.export_version)
            evidence['vertices'] = len(self.areas[number].points)
        elif cls == 'AnimDoor':
            if self.doors is None or 'door_conversion' not in plan.source:
                raise ValueError('Interactive door conversion is not connected')
            evidence['door'] = self.doors.apply(entity, ImportPlan(source=plan.source['door_conversion']), registered)
            evidence['source_id'] = source.get('EntityId')
            evidence['replaces_visual_entity'] = 'glue_item_' + str(source.get('EntityId'))
        elif cls == 'QuestObject':
            entity.set('EntityClass', 'SmartObjectHolder')
            properties = next((child for child in plan.source['entity']['children'] if child['op'] == 'Properties'), None)
            if properties is None:
                raise ValueError('Source quest controller has no properties')
            if set(properties['attributes']) - {'bSaved_by_game', 'sWH_AI_EntityCategory'}:
                raise ValueError('Source quest controller has additional unconverted properties')
            if any(c['op'] != 'Script' or c['attributes'] != {'Misc': ''} or c['children'] for c in properties['children']):
                raise ValueError('Source quest controller has additional script properties needing conversion')
            props = dict(properties['attributes'])
            props['guidSmartObjectType'] = registered['smart_entities'][plan.source['controller_identity'].lower()]
            ET.SubElement(entity, 'Properties', props)
            evidence['controller'] = plan.source['controller_identity']
        elif cls in ('NPC', 'NPC_Female'):
            from character_person_ai import apply_person_properties
            def source_element(node):
                result = ET.Element(node['op'], node['attributes'])
                result.extend(source_element(c) for c in node['children'])
                return result
            apply_person_properties(entity, source_element(plan.source['entity']))
            original = self.souls[guid_value(identity)]
            soul = ET.Element('Soul', version='8')
            shared = original.findtext('SharedSoulGuid').lower()
            for name, value in [('SharedSoulGuid', registered['souls'][shared]), ('Guid', original.findtext('Guid')),
                                ('EntityGuid', identity), ('Name', source['Name'])]:
                ET.SubElement(soul, name).text = value
            files['world/souls/' + identity + '.xml'] = xml(soul)
        files['world/entities/' + identity + '.xml'] = xml(entity)
        return identity, files, evidence


class StaticAssetAdapter:
    def __init__(self, stack, library, namespace, cache):
        self.prefix = 'objects/gluecampaign/' + identifier(namespace).lower() + '/'
        self.index, self.emitted, self.material, self.mesh = asset_pack(stack, library, self.prefix, cache)

    def plan(self, identity):
        if identity not in self.index:
            raise FileNotFoundError('Retail model/material is missing: ' + identity)
        archive, info = self.index[identity]
        return ImportPlan(source=dict(path=identity, archive=str(archive.filename), entry=info.filename))

    def convert(self, identity, plan, registered):
        previous = set(self.emitted)
        extension = PurePosixPath(identity).suffix
        if extension == '.mtl':
            target = self.material(identity) + '.mtl'
        elif extension in ('.cgf', '.cga'):
            target = self.prefix + hashlib.sha256(identity.encode()).hexdigest()[:24] + extension
            self.emitted[target] = self.mesh(identity)
        elif extension == '.cdf':
            target = self.prefix + hashlib.sha256(identity.encode()).hexdigest()[:24] + extension
            character_definition(self.index, self.emitted, self.material, self.mesh, identity, target)
        else:
            raise ValueError('This dependency requires a skinned-character adapter: ' + identity)
        files = {n: self.emitted[n] for n in self.emitted if n not in previous}
        return target, files, dict(source=plan.source, converted_model=target, material_textures_remapped=True)


class ItemTextAdapter:
    def __init__(self, source, namespace, locale, table='text_ui_items.xml'):
        self.namespace, self.locale = identifier(namespace).lower(), locale
        self.rows = {}
        with zipfile.ZipFile(Path(source) / 'Localization' / (locale + '_xml.pak')) as archive:
            data = read(archive, table)
        self.hash = hashlib.sha256(data).hexdigest()
        for row in ET.fromstring(data).findall('Row'):
            cells = row.findall('Cell')
            if len(cells) >= 3:
                name = (cells[0].text or '').lower()
                if name in self.rows and xml(self.rows[name]) != xml(row):
                    raise ValueError('Conflicting item localization key: ' + str(name))
                self.rows[name] = copy.deepcopy(row)

    def plan(self, identity):
        if identity not in self.rows:
            raise ValueError('Missing retail item localization: ' + identity)
        return ImportPlan(source=dict(key=identity, locale=self.locale, sha256=self.hash))

    def convert(self, identity, plan, registered):
        target = self.namespace + '_' + identity
        root = ET.Element('Table')
        row = copy.deepcopy(self.rows[identity])
        row.find('Cell').text = target
        root.append(row)
        return target, {'localization/' + self.locale + '/text_' + target + '.xml': xml(root)}, plan.source


class ItemAdapter:
    """Join source SQL inheritance rows into native item-class registrations."""
    CATEGORIES = {'misc': 'MiscItem', 'key': 'Key', 'money': 'Money', 'npc_tool': 'NPCTool'}
    FIELDS = {
        'pickable_item': {'weight': 'Weight', 'price': 'Price', 'owner_fading_coef': 'FadeCoef',
                          'visibility_coef': 'VisibilityCoef'},
        # KDC1 numeric atlas indices are not KDC2 named icon registrations.
        # Keep the source index in provenance until its atlas is converted.
        'player_item': {},
        'questible_item': {'is_quest': 'IsQuestItem'},
        'divisible_item': {'is_divisible': 'IsDivisible'},
        'misc': {'misc_type_id': 'Type', 'misc_subtype_id': 'SubType'},
        'key': {'key_type_id': 'Type', 'key_subtype_id': 'SubType'},
        'npc_tool': {'npc_tool_type_id': 'Type', 'npc_tool_subtype_id': 'SubType',
                     'manipulation_type': 'ManipulationType'},
    }

    def __init__(self, tables, namespace, native_tool_types=None):
        self.tables, self.namespace = tables, identifier(namespace).lower()
        self.native_tool_types = native_tool_types
        self.native_item_rows = {}

    @staticmethod
    def asset(value, suffix):
        name = value.replace('\\', '/').lower()
        if not name.startswith('objects/'):
            name = 'objects/' + name
        if suffix == '.cgf' and PurePosixPath(name).suffix in ('.cgf', '.cga', '.cdf', '.skin'):
            return name
        if not name.endswith(suffix):
            name += suffix
        return name

    def plan(self, identity):
        uuid.UUID(identity)
        row = self.tables.row('item/item', 'item_id', identity)
        category = self.tables.row('item/item_category', 'item_category_id', row['item_category_id'])['item_category_name']
        joined = {'item': row}
        for table in self.FIELDS:
            joined[table] = self.tables.row('item/' + table, 'item_id', identity, optional=True)
        source = dict(category=category, rows=joined,
                      provenance={t: self.tables.get('item/' + t)['provenance'] for t in joined})
        # Resolve assets even for item classes whose gameplay schema is still
        # being ported, so subsequent adapters reuse the converted dependency.
        dependencies = []
        for table, field, suffix in [('pickable_item', 'model', '.cgf'), ('pickable_item', 'material', '.mtl'),
                                      ('divisible_item', 'container_model', '.cgf'), ('divisible_item', 'container_material', '.mtl')]:
            if joined[table].get(field):
                dependencies.append(('assets', self.asset(joined[table][field], suffix)))
        for name in ('ui_name', 'ui_info'):
            if joined['player_item'].get(name):
                dependencies.append(('strings', joined['player_item'][name]))
        return ImportPlan(dependencies, source)

    def convert(self, identity, plan, registered):
        category, rows = plan.source['category'], plan.source['rows']
        if category not in self.CATEGORIES:
            raise ValueError('Native item-class adapter is still required: ' + category)
        if rows['pickable_item'].get('entity_script'):
            raise ValueError('Item entity script requires conversion: ' + rows['pickable_item']['entity_script'])
        target = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/campaign/' + self.namespace + '/item/' + identity))
        attrs = dict(Id=target, Name=self.namespace + '_item_' + identity.replace('-', '_'))
        for table, fields in self.FIELDS.items():
            for old, new in fields.items():
                value = rows[table].get(old)
                if value not in (None, ''):
                    attrs[new] = value.lower() if value in ('True', 'False') else value
        type_evidence = []
        if category == 'npc_tool':
            from campaign_item_types import resolve
            for table, field in (('npc_tool_type', 'Type'), ('npc_tool_subtype', 'SubType')):
                if field not in attrs:
                    continue
                if self.native_tool_types is None or self.tables is None:
                    raise ValueError('Native tool type tables are required for conversion')
                retained = []
                for source_item in self.tables.get('item/npc_tool')['rows']:
                    native_item = self.native_item_rows.get(source_item.get('item_id', '').lower())
                    if (source_item.get(table + '_id') == attrs[field] and native_item is not None
                            and native_item.tag == 'NPCTool' and native_item.get(field)):
                        retained.append(dict(item_id=source_item['item_id'], target_id=native_item.get(field)))
                attrs[field], evidence = resolve(self.tables.get('item/' + table)['rows'],
                    self.native_tool_types.get(table, []), table, attrs[field], retained)
                type_evidence.append(evidence)
            if 'ManipulationType' in attrs:
                from campaign_item_types import manipulation
                if self.native_tool_types is None or self.tables is None:
                    raise ValueError('Native manipulation type table is required for conversion')
                attrs['ManipulationType'], evidence = manipulation(
                    self.tables.get('item/item_manipulation_type')['rows'],
                    self.native_tool_types.get('item_manipulation_type', []), attrs['ManipulationType'])
                type_evidence.append(dict(table='item_manipulation_type', **evidence))
        for old, new in [('ui_name', 'UIName'), ('ui_info', 'UIInfo')]:
            value = rows['player_item'].get(old)
            if value:
                attrs[new] = registered['strings'][value.lower()]
        for table, old, new, suffix in [('pickable_item', 'model', 'Model', '.cgf'),
                                       ('pickable_item', 'material', 'Material', '.mtl'),
                                       ('divisible_item', 'container_model', 'ContainerModel', '.cgf'),
                                       ('divisible_item', 'container_material', 'ContainerMaterial', '.mtl')]:
            value = rows[table].get(old)
            if value:
                attrs[new] = registered['assets'][self.asset(value, suffix)].removeprefix('objects/')
        root = ET.Element('database', name='barbora')
        ET.SubElement(ET.SubElement(root, 'ItemClasses', version='8'), self.CATEGORIES[category], attrs)
        filename = 'Libs/Tables/item/item__' + self.namespace + '_' + identity.replace('-', '') + '.xml'
        return target, {filename: xml(root)}, dict(source=plan.source, category=self.CATEGORIES[category],
            source_id=identity, target_id=target, model_and_text_imported=True,
            item_type_mappings=type_evidence,
            presentation_pending=['Source inventory icon atlas conversion'] if rows['player_item'].get('icon_id') else [])
