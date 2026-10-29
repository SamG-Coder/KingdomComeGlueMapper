"""Developer-only rebuild of receipt-owned source NPC appearances.

Builds a separate complete package for desktop_setup.install_transaction, or a
smaller developer-only overlay. It does not install, change gameplay, or expose
diagnostic controls in Setup UI.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib

from setup_campaign import RECEIPT, digest, verify_package
from setup_package_upgrade import rewrite

TRAVEL = 'Data/gluemappertravel.pak'
LOCALE = 'Localization/English_xml.pak'


def retire_shared_definitions(resources, namespaces, incoming):
    """Migrate receipt-owned definitions out of older shared appearance tables.

    Each shared file is rewritten once for the entire selection. Removing a
    sibling belonging to another character, or a reference to one, is forbidden.
    Only exact definitions supplied by the replacement appearance may move.
    A shared prefix is not ownership: equipment wrappers, carried inventories
    and shop stock use the same namespace but remain gameplay definitions.
    """
    namespaces = tuple(namespaces)
    definitions = {
        'charactercomponent': {'Component', 'Body', 'Head', 'Hair', 'Beard', 'Clothing'},
        'clothinghidinggroup': {'HidingGroup'}, 'clothingmaterial': {'Material'},
        'inventorypreset': {'InventoryPreset'}, 'clothing_preset': {'clothing_preset'},
        'item': {'Armor', 'Helmet', 'Hood'},
    }
    pattern = r'libs/tables/(?:character|item)/([a-z_]+)__([a-z0-9_]+)\.xml'
    moved = {}
    for name, blob in incoming.items():
        match = re.fullmatch(pattern, name.lower())
        if not match or match[1] not in definitions:
            continue
        owners = [ns for ns in namespaces if match[2] in (ns, ns+'_clothes')]
        if not owners:
            continue
        owner = max(owners, key=len)
        for node in ET.fromstring(blob).iter():
            if node.tag in definitions[match[1]]:
                identity = node.get('Name') or node.get('clothing_preset_name') or ''
                if identity:
                    moved[match[1], node.tag, identity] = owner
    replacements = {}; removed = []
    for name, blob in resources.items():
        match = re.fullmatch(pattern, name.lower())
        if not match or match[1] not in definitions:
            continue
        root = ET.fromstring(blob); changed = False
        for parent in list(root.iter()):
            for node in list(parent):
                if node.tag not in definitions[match[1]]:
                    continue
                identity = node.get('Name') or node.get('clothing_preset_name') or ''
                owner = moved.get((match[1], node.tag, identity))
                if owner and match[2] not in (owner, owner+'_clothes'):
                    parent.remove(node); changed = True
                    removed.append(dict(resource=name, namespace=owner, definition=identity, kind=node.tag))
        if changed:
            replacements[name] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    return replacements, removed


def shared_overlay(baseline, destination, namespaces, incoming):
    # PersonImport receipts and their table registrations are owned by TRAVEL.
    # Other world shards can contain independent loot tables with colliding
    # names; they are not appearance resources and must not be migrated here.
    with zipfile.ZipFile(baseline.root / TRAVEL) as archive:
        resources = {name: archive.read(name) for name in archive.namelist()
                     if name.lower().startswith('libs/tables/') and '__' in name and name.endswith('.xml')}
    replacements, removed = retire_shared_definitions(resources, namespaces, incoming)
    if replacements:
        pending = destination.with_suffix('.pending')
        with zipfile.ZipFile(pending, 'x', zipfile.ZIP_DEFLATED) as archive:
            for name, blob in replacements.items():
                archive.writestr(name, blob)
        pending.replace(destination)
    return removed


def restore_missing_inventory_presets(resources, recovery):
    """Recover missing definitions without replacing current outfits or stock.

    Explicit repair helper for a known recovery package. A preset relocated to
    another table already exists and is not restored as a duplicate.
    """
    def inventory_file(name):
        return bool(re.fullmatch(r'libs/tables/item/inventorypreset__[^/]+\.xml', name.lower()))
    known = {n.get('Name') for path, blob in resources.items() if inventory_file(path)
             for n in ET.fromstring(blob).iter('InventoryPreset')}
    by_name = {name.lower(): name for name in resources}
    patches, restored = {}, []
    for path, blob in recovery.items():
        if not inventory_file(path):
            continue
        original = ET.fromstring(blob)
        missing = [n for n in original.iter('InventoryPreset') if n.get('Name') not in known]
        if not missing:
            continue
        path = by_name.get(path.lower(), path)
        if path in resources:
            current = ET.fromstring(resources[path])
            parent = current.find('InventoryPresets')
            if parent is None:
                raise ValueError('Missing InventoryPresets container in '+path)
        else:
            current = copy.deepcopy(original)
            parent = current.find('InventoryPresets')
            if parent is None:
                raise ValueError('Missing recovery InventoryPresets container in '+path)
            for node in list(parent):
                if node.tag == 'InventoryPreset':
                    parent.remove(node)
        for node in missing:
            parent.append(copy.deepcopy(node))
            known.add(node.get('Name'))
            restored.append(dict(resource=path, definition=node.get('Name')))
        patches[path] = ET.tostring(current, encoding='utf-8', xml_declaration=True)
    return patches, restored


def imports(archive, selected=None):
    """Resolve source identities from importer receipts, never names guessed from meshes."""
    found = {}
    for name in archive.namelist():
        if not name.startswith('Libs/GlueMapper/PersonImport/') or not name.endswith('.json'):
            continue
        row = json.loads(archive.read(name))
        person = row.get('person')
        if selected is not None and person not in selected:
            continue
        if not isinstance(person, str) or not person:
            raise ValueError('Missing source person identity')
        namespace = row.get('namespace', '')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', namespace):
            raise ValueError('Unsupported appearance ownership: ' + person)
        if name != 'Libs/GlueMapper/PersonImport/' + namespace + '.json' or person in found:
            raise ValueError('Ambiguous source appearance: ' + person)
        if row.get('appearance') != 'character_person_appearance':
            raise ValueError('Unsupported appearance adapter: ' + person)
        found[person] = namespace
    if selected is not None and set(found) != set(selected):
        raise ValueError('No imported appearance receipt for: ' + ', '.join(sorted(set(selected) - set(found))))
    return found


def write_delta(appearance, destination, namespace, baseline, native=None):
    """Store only changed assets, after the same streaming conversion as full builds.

    The result is a developer overlay requiring the recorded base package. It
    has no installer receipt and must not be installed as a standalone mod.
    """
    from texture_streaming import checked_entries, generated_entries
    resources = {p.relative_to(appearance).as_posix(): p.read_bytes() for p in appearance.rglob('*')
                 if p.is_file() and p.relative_to(appearance).parts[0] == 'Libs'}
    validate_resources(resources, namespace)
    def entries():
        for name, path in appearance_objects(appearance, resources, native).items():
            yield name, path.read_bytes()
    pending = destination.with_suffix('.pending')
    count = total = 0
    from itertools import chain
    with zipfile.ZipFile(pending, 'x', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, blob in chain(checked_entries(generated_entries(entries())), resources.items()):
            if name.lower() in getattr(baseline, 'collisions', ()):
                raise ValueError('Conflicting source appearance entry: '+name)
            old = baseline.index.get(name.lower())
            if old and old[2:] == (zlib.crc32(blob), len(blob)):
                continue
            archive.writestr(name, blob)
            count += 1; total += len(blob)
    pending.replace(destination)
    return dict(changed_assets=count, uncompressed_bytes=total, archive_bytes=destination.stat().st_size)


def appearance_objects(appearance, resources, native=None):
    """Follow final component bindings; conversion intermediates are not runtime assets."""
    from clothing_lab.catalog import resolved_nodes
    from character_lods import source_lods
    from clothing_lab.assets import key
    index = {key(p.relative_to(appearance).as_posix()):p for p in (appearance/'objects').rglob('*') if p.is_file()}
    wanted = set(); materials = set()
    for name, blob in resources.items():
        if '/CharacterComponent__' not in name:
            continue
        for part in resolved_nodes(ET.fromstring(blob)):
            for element in part['elements']:
                model = element.get('ModelResolved')
                mat = element.get('MaterialResolved')
                if model:
                    wanted.add(model); wanted.update(source_lods(index, model).values())
                if mat:
                    materials.add(mat)
    wanted.update(materials)
    for name in materials:
        if name not in index:
            raise ValueError('Missing final appearance material: '+name)
        for texture in ET.parse(index[name]).getroot().iter('Texture'):
            value = texture.get('File')
            if not value:
                continue
            base = key(value)
            if base in ('nearest_cubemap', 'nearest_cubemap.dds'):
                continue  # Engine-provided reflection probe, not a disk image.
            if base.endswith('.tif'):
                base = base[:-4]+'.dds'
            if base not in index and native is not None and base in native.index:
                continue  # Native material donors retain their shipped detail maps.
            wanted.add(base)
            wanted.update(n for n in index if n.startswith(base+'.') and n[len(base)+1:].isdigit())
    missing = wanted-set(index)
    if missing:
        raise ValueError('Missing final appearance assets: '+', '.join(sorted(missing)))
    return {name:index[name] for name in sorted(wanted)}


def stage_overlay(source, target, installed, output, people=None, *, notify=print):
    """Rebuild receipt-owned people one at a time without duplicating the world."""
    from clothing_lab.assets import Assets
    from character_person import resolve_person
    from character_person_appearance import build_appearance
    from bake_pool import release
    from setup_campaign import digest
    source, target, installed, output = map(lambda p: Path(p).resolve(), (source, target, installed, output))
    if output.exists() or output.is_relative_to(installed):
        raise ValueError('Choose a fresh output outside the installed package')
    baseline = Assets(installed)
    native = Assets(target)
    with zipfile.ZipFile(installed/TRAVEL) as archive:
        selected = imports(archive, people)
    if not selected:
        raise ValueError('No imported source appearances')
    (output/'Data').mkdir(parents=True)
    report = dict(version=1, scope='developer_source_appearance_overlay', base_package=str(installed),
                  base_signature=baseline.signature(), runtime_verified=False, complete=False,
                  requested_people=sorted(selected), people=[],
                  converter_sources={p.name:digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    report_path = output/'rebuild-report.json'
    def save():
        pending = report_path.with_suffix('.pending')
        pending.write_text(json.dumps(report,indent=2),encoding='utf-8'); pending.replace(report_path)
    save()
    try:
        for index, (name, namespace) in enumerate(selected.items(), 1):
            notify(f'Rebuilding {index}/{len(selected)}: {name}')
            person = resolve_person(source, name, include_ai=False)
            if namespace.startswith('gm_person_') and 'gm_person_'+person['soul']['soul_id'].replace('-','') != namespace:
                raise ValueError('Source soul no longer matches import receipt: '+name)
            # TemporaryDirectory owns only this newly created staging folder.
            # Keeping the delta and report bounds disk use across a whole cast.
            with tempfile.TemporaryDirectory(prefix='appearance-', dir=output) as scratch:
                appearance = Path(scratch)/'converted'
                character = build_appearance(source, target, person, namespace, appearance)
                with zipfile.ZipFile(installed/TRAVEL) as archive:
                    verify_bindings(archive, character, namespace)
                delta = write_delta(appearance, output/'Data'/(namespace+'.pak'), namespace, baseline, native)
                (output/(namespace+'.json')).write_text(json.dumps(character,indent=2),encoding='utf-8')
            report['people'].append(dict(person=name, namespace=namespace, gender=character['gender'], **delta))
            save()
        # Include unchanged canonical tables as well as changed delta entries.
        # Retirement is based on exact replacement definitions, never prefixes.
        with zipfile.ZipFile(installed/TRAVEL) as archive:
            incoming = {n: archive.read(n) for n in archive.namelist()
                        if n.lower().startswith('libs/tables/') and n.endswith('.xml')}
        for namespace in selected.values():
            with zipfile.ZipFile(output/'Data'/(namespace+'.pak')) as archive:
                incoming.update({n: archive.read(n) for n in archive.namelist()
                                 if n.lower().startswith('libs/tables/') and n.endswith('.xml')})
        report['retired_shared_definitions'] = shared_overlay(
            baseline, output/'Data/appearance_shared_tables.pak', selected.values(), incoming)
        from clothing_inventory import audit_assets
        checks = audit_assets(Assets(installed, output), {ns: name for name, ns in selected.items()})
        report['equipment_checks'] = checks
        if any(row['status'] == 'error' for row in checks):
            raise ValueError('Rebuilt appearance overlay has broken NPC equipment references')
        report['complete'] = True
        save()
    except Exception as exc:
        report['error'] = str(exc); save()
        raise
    finally:
        release()
    notify('Developer overlay ready: '+str(output))
    return report


def owned_shards(package, files, namespace, *, allow_shared=False, allow_equipment=False):
    pattern = re.compile(re.escape('Data/' + namespace) + r'(?:_\d{2,})?\.pak$')
    paths = sorted(name for name in files if pattern.fullmatch(name))
    if not paths and not allow_shared:
        raise ValueError('Missing source appearance archives: ' + namespace)
    prefixes = ('objects/characters/gluetravel/' + namespace + '/',)
    if allow_equipment:
        prefixes += ('objects/gluecampaign/' + namespace + '_weapons/',)
    for name in paths:
        with zipfile.ZipFile(package / name) as archive:
            for entry in archive.namelist():
                normalized = entry.replace('\\', '/').lower()
                if (not normalized.startswith(prefixes) or '..' in normalized.split('/')
                        or ':' in normalized):
                    raise ValueError('Appearance archive contains unrelated assets: ' + name + ' (' + entry + ')')
    return paths


def preserve_carried_assets(package, namespace, shards, *, limit=900 * 1024**2):
    """Keep receipt-owned equipment when replacing mixed appearance shards.

    Actor imports pack clothing and converted carried items together. Appearance
    rebuilds do not regenerate the latter. Copy their exact virtual paths and
    bytes into separate bounded PAKs before any old shard is replaced/deleted.
    Call only on private staging; source/checkpoint hardlinks are never edited.
    """
    from contextlib import ExitStack
    package = Path(package)
    owned_shards(package, shards, namespace, allow_shared=True, allow_equipment=True)
    prefix = 'objects/gluecampaign/' + namespace + '_weapons/'
    entries = {}
    with ExitStack() as stack:
        for relative in shards:
            archive = stack.enter_context(zipfile.ZipFile(package / relative))
            for info in archive.infolist():
                name = info.filename.replace('\\', '/').lower()
                if info.is_dir() or not name.startswith(prefix):
                    continue
                if name in entries:
                    previous, old = entries[name]
                    if previous.read(old) != archive.read(info):
                        raise ValueError('Conflicting carried equipment asset: ' + name)
                else:
                    entries[name] = (archive, info)
        if not entries:
            return {}
        with tempfile.TemporaryDirectory(prefix='retained-equipment-', dir=package.parent) as temporary:
            staged = Path(temporary); outputs = []; used = 22; count = 0; out = None
            try:
                for name, (source, info) in sorted(entries.items()):
                    size = info.file_size + 2 * len(name.encode('utf-8')) + 76
                    if size + 22 > limit:
                        raise ValueError('Carried equipment asset exceeds ZIP32 shard limit: ' + name)
                    if out is not None and (used + size > limit or count >= 50000):
                        out.close(); out = None
                    if out is None:
                        suffix = '' if not outputs else '_%02d' % len(outputs)
                        path = staged / (namespace + '_retained_equipment' + suffix + '.pak')
                        outputs.append(path)
                        out = zipfile.ZipFile(path, 'x', zipfile.ZIP_STORED, allowZip64=False)
                        used, count = 22, 0
                    # Reading verifies the original CRC. Normalizing only the
                    # virtual path does not alter streamed mip or model bytes.
                    item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)); item.create_system = 0
                    out.writestr(item, source.read(info))
                    used += size; count += 1
            finally:
                if out is not None: out.close()
            changed = {}
            for path in outputs:
                relative = 'Data/' + path.name
                expected = digest(path)
                destination = package / relative
                if destination.exists():
                    if digest(destination) != expected:
                        raise ValueError('Retained equipment archive already has different contents: ' + relative)
                else:
                    path.replace(destination)
                changed[relative] = expected
            return changed


def retire_shared_assets(package, files, namespaces, owned):
    """Detach old appearance assets from shared packs before adding new shards.

    Namespaced virtual paths are ownership evidence from import receipts.
    Other people and world content stay byte-identical inside the shared pack.
    """
    namespaces = set(namespaces)
    prefix = 'objects/characters/gluetravel/'
    found = set(); pending = {}
    owned = set(owned)
    for relative in files:
        if relative in owned or not re.fullmatch(r'Data/[^/]+\.pak',relative):
            continue
        with zipfile.ZipFile(package/relative) as archive:
            remove = set()
            for name in archive.namelist():
                normalized = name.replace('\\','/').lower()
                if normalized.startswith(prefix):
                    namespace, separator, _ = normalized[len(prefix):].partition('/')
                    if separator and namespace in namespaces:
                        found.add(namespace); remove.add(name)
            if remove:
                pending[relative] = remove
    missing = {ns for ns in namespaces if not any(
        re.fullmatch(r'Data/'+re.escape(ns)+r'(?:_\d{2,})?\.pak',name) for name in owned)} - found
    if missing:
        raise ValueError('No owned appearance assets: '+', '.join(sorted(missing)))
    changed = {}
    for relative, remove in pending.items():
        rewrite(package,relative,{},remove=remove.__contains__)
        changed[relative] = digest(package/relative)
    return changed


def validate_resources(resources, namespace):
    for name, blob in resources.items():
        if not re.fullmatch(r'Libs/Tables/(?:Character|item)/[A-Za-z_]+__' + re.escape(namespace)
                            + r'(?:_clothes)?\.xml', name):
            raise ValueError('Appearance rebuild attempted an unrelated resource: ' + name)
        ET.fromstring(blob)


def verify_bindings(archive, character, namespace):
    """Refuse a rebuild requiring a gameplay/Storm migration instead of silently breaking it."""
    root = ET.fromstring(archive.read('Libs/Storm/appearance/' + namespace + '.xml'))
    actual = {node.tag[3:].lower(): node.get('name') for node in root.findall('.//operations/*')
              if node.tag.startswith('set')}
    if actual != character['appearance'] or character['inventory'] != namespace + '_clothes':
        raise ValueError('Appearance identities changed; explicit binding migration required: ' + namespace)


def stage(source, target, installed, output, people, *, notify=print):
    from build_profile_population_upgrade import clone_missing
    from build_recipe import require_current
    from build_quest_people_test import write_actor_paks
    from build_english_audio_test import merge_audio_strings
    from character_person import resolve_person
    from character_person_appearance import build_appearance
    from bake_pool import release
    from setup_pak_limits import validate as validate_pak_limits

    source, target, installed, output = map(lambda p: Path(p).resolve(), (source, target, installed, output))
    if not people or len(set(people)) != len(people):
        raise ValueError('Select one or more distinct imported source people')
    if output.exists() or output.is_relative_to(installed):
        raise ValueError('Choose a fresh output outside the installed package')
    notify('Verifying installed baseline')
    baseline = verify_package(installed)
    require_current(baseline)
    with zipfile.ZipFile(installed / TRAVEL) as archive:
        selected = imports(archive, people)
    shards = {name: owned_shards(installed, baseline['files'], ns, allow_shared=True, allow_equipment=True)
              for name, ns in selected.items()}
    output.mkdir(parents=True)
    # Keep a verified, cheap recovery package. Every changed file is replaced,
    # never written through these links into either checkpoint or installation.
    package, recovery = output / 'package', output / 'baseline'
    clone_missing(installed, recovery)
    clone_missing(recovery, package)
    receipt = copy.deepcopy(baseline)
    receipt['files'].update(retire_shared_assets(package, baseline['files'], selected.values(),
                                                [path for paths in shards.values() for path in paths]))
    resources = {}
    labels, converted = [], []
    try:
        for name in people:
            namespace = selected[name]
            notify('Rebuilding appearance: ' + name)
            person = resolve_person(source, name, include_ai=False)
            if namespace.startswith('gm_person_') and 'gm_person_' + person['soul']['soul_id'].replace('-', '') != namespace:
                raise ValueError('Source soul no longer matches import receipt: ' + name)
            appearance = output / 'people' / namespace
            character = build_appearance(source, target, person, namespace, appearance)
            with zipfile.ZipFile(recovery / TRAVEL) as archive:
                verify_bindings(archive, character, namespace)
            actor_stage = output / 'packed' / namespace
            actor_stage.mkdir(parents=True)
            paks, libs = write_actor_paks(appearance, actor_stage, namespace)
            validate_resources(libs, namespace)
            resources.update(libs)
            strings = appearance / 'Localization/English/text_ui_items.xml'
            if strings.exists():
                labels.append(strings.read_bytes())
            incoming = {'Data/' + p.name for p in paks}
            receipt['files'].update(preserve_carried_assets(package, namespace, shards[name]))
            for old in shards[name]:
                if old not in incoming:
                    (package / old).unlink()
                    del receipt['files'][old]
            for pak in paks:
                relative = 'Data/' + pak.name
                pak.replace(package / relative)
                receipt['files'][relative] = digest(package / relative)
            record = dict(person=name, namespace=namespace, gender=character['gender'],
                          fitting=character.get('fitting'), archives=sorted(incoming),
                          runtime_verified=False)
            converted.append(record)
            (appearance / 'appearance-result.json').write_text(json.dumps(character, indent=2), encoding='utf-8')
    finally:
        release()
    with zipfile.ZipFile(recovery / TRAVEL) as archive:
        shared, retired = retire_shared_definitions(
            {name: archive.read(name) for name in archive.namelist()
             if name.lower().startswith('libs/tables/') and name.endswith('.xml')},
            selected.values(), resources)
    rewrite(package, TRAVEL, {**shared, **resources})
    from clothing_inventory import require_equipment, relevant_resource
    with zipfile.ZipFile(package / TRAVEL) as archive:
        equipment_checks = require_equipment(
            {n: archive.read(n) for n in archive.namelist() if relevant_resource(n)},
            {ns: name for name, ns in selected.items()})
    receipt['files'][TRAVEL] = digest(package / TRAVEL)
    if labels:
        with zipfile.ZipFile(package / LOCALE) as archive:
            before = blob = archive.read('text_ui_items.xml')
        for label in labels:
            blob = merge_audio_strings(blob, label)
        if blob != before:
            rewrite(package, LOCALE, {'text_ui_items.xml': blob})
            receipt['files'][LOCALE] = digest(package / LOCALE)
    report = dict(version=1, scope='explicit_imported_source_appearances', people=converted,
                  retired_shared_definitions=retired,
                  equipment_checks=equipment_checks,
                  native_texture_streaming=True, runtime_verified=False,
                  recovery_package=str(recovery),
                  changed_files=[n for n, h in receipt['files'].items() if baseline['files'].get(n) != h],
                  removed_files=sorted(set(baseline['files']) - set(receipt['files'])))
    receipt['source_appearances'] = report
    temporary = package / (RECEIPT + '.pending')
    temporary.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    temporary.replace(package / RECEIPT)
    verify_package(package, hashes=False)
    validate_pak_limits(package)
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    notify('Candidate ready for bounded native playtest: ' + str(package))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'target', 'installed', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--person', action='append')
    selection.add_argument('--all-imported', action='store_true', help='Rebuild every source-person import receipt')
    parser.add_argument('--overlay-only', action='store_true', help='Write a small developer overlay, not an installable package')
    args = parser.parse_args()
    people = args.person
    if args.all_imported and not args.overlay_only:
        with zipfile.ZipFile(args.installed/TRAVEL) as archive:
            people = list(imports(archive))
    operation = stage_overlay if args.overlay_only else stage
    operation(args.source, args.target, args.installed, args.output, people, notify=lambda s: print(s, flush=True))


if __name__ == '__main__':
    main()
