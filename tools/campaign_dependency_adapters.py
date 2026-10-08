"""Retail source adapters used by automatic campaign dependency import."""
import copy
import hashlib
from pathlib import Path, PurePosixPath
import re
import uuid
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


class SourceTables:
    def __init__(self, source, reader):
        self.source, self.reader, self.cache = source, reader, {}

    def get(self, table):
        if table not in self.cache:
            item = self.reader(self.source, {'table': ('Tables.pak', 'Libs/Tables/' + table + '.xml')})['table']
            self.cache[table] = dict(rows=[dict(r.attrib) for r in ET.fromstring(item['data']).findall('./table/rows/row')],
                                     provenance=item['candidates'])
        return self.cache[table]

    def row(self, table, field, value, optional=False):
        matches = [r for r in self.get(table)['rows'] if r.get(field, '').lower() == value.lower()]
        if not matches and optional:
            return {}
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous retail record: ' + table + ':' + value)
        return matches[0]


class LazyAdapter:
    def __init__(self, factory):
        self.factory, self.adapter = factory, None

    def plan(self, identity):
        if self.adapter is None:
            self.adapter = self.factory()
        return self.adapter.plan(identity)

    def convert(self, *args):
        return self.adapter.convert(*args)


class InventoryAdapter:
    def __init__(self, tables, namespace):
        self.tables, self.namespace = tables, identifier(namespace).lower()

    def plan(self, identity):
        source = resolve_inventory(identity, {n: self.tables.get('inventory/' + n)['rows'] for n in INVENTORY_TABLES})
        return ImportPlan([('items', item) for item in source['item_dependencies']], source)

    def convert(self, identity, plan, registered):
        source = plan.source
        definition = source['definition']
        if definition.get('max_items') or definition.get('read_only') == 'True' or definition.get('restock_interval', '0') != '0':
            raise ValueError('Inventory capacity/restock policy needs native conversion')
        if source['preset_choices']:
            raise ValueError('Weighted source inventory preset policy needs native conversion')
        root = ET.Element('database', name='barbora')
        target = self.namespace + '_inventory_' + identity.replace('-', '_')
        preset = ET.SubElement(ET.SubElement(root, 'InventoryPresets', version='2'), 'InventoryPreset', Name=target, Mode='All')
        for row in source['direct_items']:
            if any(row.get(k) not in ('', '0', None) for k in ('amount_random_add', 'priority', 'health_offset')):
                raise ValueError('Random source item selection/condition needs native conversion')
            attrs = dict(ItemClassId=registered['items'][row['item_id'].lower()], Amount=row['amount'])
            if row.get('health'):
                attrs['Health'] = row['health']
            ET.SubElement(preset, 'PresetItem', attrs)
        path = 'Libs/Tables/item/InventoryPreset__' + target + '.xml'
        return target, {path: xml(root)}, dict(source_id=identity, items=len(source['direct_items']), random_selection=False)


class SoulAdapter:
    def __init__(self, tables, souls):
        self.tables = tables
        self.instances = {}
        for soul in souls.findall('./SoulList/Souls/Soul'):
            shared = soul.findtext('SharedSoulGuid')
            if shared:
                self.instances.setdefault(shared.lower(), []).append(soul)

    def plan(self, identity):
        row = self.tables.row('rpg/soul', 'soul_id', identity)
        instances = self.instances.get(identity, [])
        inventories = {s.findtext('./StaticData/InventoryId') for s in instances}
        inventories.add(row.get('inventory_id'))
        inventories -= {None, '', '00000000-0000-0000-0000-000000000000'}
        parts = set()
        for kind in ('body', 'head', 'hair', 'beard'):
            values = {row.get('character_' + kind + '_id')}
            values.update(s.findtext('./StaticData/CharacterBodyDescription/' + kind.title() + 'Id') for s in instances)
            for value in values - {None, '', '00000000-0000-0000-0000-000000000000'}:
                parts.add(kind + ':' + value)
        dependencies = [('character_parts', p) for p in sorted(parts)]
        dependencies += [('inventories', i) for i in sorted(inventories)]
        # Appearance and AI are source identities too, not a substitute default
        # body or a cinematic-only brain assigned because registration failed.
        return ImportPlan(dependencies, dict(row=row, instances=[ast(s) for s in instances],
                          provenance=self.tables.get('rpg/soul')['provenance']))

    def convert(self, identity, plan, registered):
        raise ValueError('Soul appearance/brain conversion must be connected to the character upgrader before registration')


class EntityAdapter:
    def __init__(self, world, souls, area_data, export_version, doors=None, controllers=None):
        self.doors = doors
        self.controllers = controllers or {}
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
        source = plan.source['attributes']
        cls = source['EntityClass']
        if cls not in ('TriggerArea', 'TagPoint', 'NPC', 'NPC_Female', 'AnimDoor', 'QuestObject'):
            raise ValueError('Native entity-class conversion is not implemented: ' + cls)
        attrs = {k: source[k] for k in ('Name', 'Pos', 'Rotate', 'Scale', 'EntityClass', 'CastShadowMinSpec') if k in source}
        attrs['EntityGuid'] = identity
        # EntityId is allocated only when merging with the target level.
        attrs['EditorLayer'] = 'KDC1/' + source.get('Layer', 'Imported')
        entity = ET.Element('Entity', attrs)
        files = {}
        evidence = dict(source=plan.source['source'], source_class=cls, identity_preserved=True,
                        profile=source.get('Layer'), transform_preserved=True)
        if source.get('ParentGuid') not in (None, '', '0'):
            raise ValueError('Parented entity attachment conversion is required: ' + identity)
        if cls == 'TriggerArea':
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
    def __init__(self, source, namespace, locale):
        self.namespace, self.locale = identifier(namespace).lower(), locale
        self.rows = {}
        with zipfile.ZipFile(Path(source) / 'Localization' / (locale + '_xml.pak')) as archive:
            data = read(archive, 'text_ui_items.xml')
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
        'npc_tool': {'npc_tool_type_id': 'Type', 'npc_tool_subtype_id': 'SubType'},
    }

    def __init__(self, tables, namespace):
        self.tables, self.namespace = tables, identifier(namespace).lower()

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
            presentation_pending=['Source inventory icon atlas conversion'] if rows['player_item'].get('icon_id') else [])
