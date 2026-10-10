"""Collect travel registrations into one native mod patch per database table."""
import copy
from pathlib import PurePosixPath
import xml.etree.ElementTree as ET

from upgrade_map import xml


KEYS = {
    'souls': 'soul_id', 'roles': 'role_name', 'Shops': 'shop_id',
    'InventoryPresets': 'Name', 'CharacterComponents': 'Name',
    'ClothingMaterials': 'Name',
    'clothing_presets': 'clothing_preset_id', 'ItemClasses': 'Id',
    'levels': 'LevelId', 'LevelSwitches': 'Name',
    'locations': 'location_id', 'poi_types': 'poi_type_id',
    'ui_map_labels': 'ui_map_label_id', 'ui_local_mapss': 'ui_local_map_id',
    'FactionTree': 'Name',
    'brains': 'brain_id', 'subbrains': 'subbrain_id',
    'subbrain_behaviour_trees': 'subbrain_id',
    'brain2subbrains': ('brain_id', 'subbrain_id'),
    'brain2mailboxs': ('brain_id', 'mailbox_id'),
}

# RequiredAttribute fields from KCD2Mod's GeneratedDatabase reader. The XML
# XSD bundled in Tables.pak incorrectly marks PresetItem.Name optional.
REQUIRED = {
    'Armor': ('Id', 'Name', 'Weight', 'DefenseStab', 'DefenseSlash',
              'DefenseSmash', 'StrReq', 'Noise', 'Visibility', 'Conspicuousness',
              'Charisma', 'SocialClassId', 'WealthLevel', 'IconId'),
    'PresetItem': ('Name',),
}


def validate_required_fields(root, filename):
    for row in root.iter():
        missing = [name for name in REQUIRED.get(row.tag, ()) if not row.get(name)]
        if missing:
            raise ValueError(f'{filename}: {row.tag} missing required attributes: ' + ', '.join(missing))


def managed_patches(files, modid):
    """Preserve non-table resources; combine additive registrations by table.

    A table patch's suffix must equal the manifest modid. Resource namespaces
    belong in record IDs/names, not in the patch filename. Reject collisions
    rather than silently replacing the coachman with the new merchant.
    """
    result, groups = {}, {}
    for name, blob in files.items():
        path = PurePosixPath(name)
        if not name.lower().startswith('libs/tables/') or '__' not in path.stem or path.suffix.lower() != '.xml':
            result[name] = blob
            continue
        target = str(path.with_name(path.stem.split('__', 1)[0] + '__' + modid + '.xml'))
        root = ET.fromstring(blob)
        validate_required_fields(root, name)
        if root.tag != 'database' or len(root) != 1:
            raise ValueError('Expected one database table in ' + name)
        table = root[0]
        if table.tag not in KEYS:
            raise ValueError('No primary-key validation for ' + name)
        if target not in groups:
            merged = ET.Element(root.tag, root.attrib)
            groups[target] = (merged, ET.SubElement(merged, table.tag, table.attrib), set())
        merged, rows, seen = groups[target]
        if rows.tag != table.tag or rows.attrib != table.attrib:
            raise ValueError('Incompatible table versions in ' + name)
        for row in table:
            fields = KEYS[table.tag]
            key = tuple(row.get(k) for k in fields) if isinstance(fields, tuple) else row.get(fields)
            if key is None or (isinstance(key, tuple) and None in key) or key in seen:
                raise ValueError('Missing or duplicate primary key in ' + name + ': ' + str(key))
            if table.tag == 'FactionTree':
                # Faction names are global even when nested under different
                # parents. Validate the whole subtree, not only its root.
                keys = [e.get('Name') for e in row.iter('Faction')]
                if None in keys or len(keys) != len(set(keys)) or seen.intersection(keys):
                    raise ValueError('Missing or duplicate nested faction name in ' + name)
                seen.update(keys)
            else:
                seen.add(key)
            rows.append(copy.deepcopy(row))
    result.update({name: xml(root) for name, (root, _, _) in groups.items()})
    return result
