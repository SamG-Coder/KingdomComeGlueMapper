"""Resolve a KCD1 NPC outfit and package an isolated skinned-character probe.

This is an appearance compatibility test, not an AI/soul database migration.
Source tables and assets remain local; no generated game data belongs in Git.
"""
import argparse
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from static_assets import asset_pack
from upgrade_map import read, xml
from clothing_appearance import apply_armor_colorization


def load_tables(stack, data):
    """Apply whole-table overrides in the same order as the asset packer."""
    tables = {}
    paths = [data / 'Tables.pak', *sorted((data / 'patch').glob('*.pak'))]
    for path in paths:
        archive = stack.enter_context(zipfile.ZipFile(path))
        for entry in archive.namelist():
            name = entry.replace('\\', '/').lower()
            if name.startswith('libs/tables/') and name.endswith('.xml'):
                tables[name.removeprefix('libs/tables/').removesuffix('.xml')] = (archive, entry)
    def rows(name):
        archive, entry = tables[name]
        return [dict(r.attrib) for r in ET.fromstring(read(archive, entry)).iter('row')]
    return rows


def resolve_parts(soul, rows):
    parts = []
    for kind in ('body', 'head', 'hair', 'beard'):
        key = 'character_' + kind + '_id'
        if soul.get(key):
            row = next(r for r in rows('character_' + kind) if r[key] == soul[key])
            parts.append(dict(kind=kind, **row))
    preset = soul.get('initial_clothing_preset_id')
    armor_ids = {r['armor_id'] for r in rows('item/armor2clothing_preset')
                 if r['clothing_preset_id'] == preset}
    clothing = {r['clothing_id']: r for r in rows('item/clothing')}
    for armor in rows('item/armor'):
        if armor['item_id'] in armor_ids:
            for field in ('clothing_id', 'clothing2_id'):
                if armor.get(field):
                    parts.append(dict(kind='cloth', armor_id=armor['item_id'],
                                      armor_settings=armor,
                                      **clothing[armor[field]]))
    return parts


def asset_path(index, part, field, extension):
    value = part[field].replace('\\', '/').lower().removesuffix(extension)
    exact = value + extension
    if exact in index:
        return exact
    if field == 'model' and value.endswith('_dontuse'):
        # Obsolete source records can retain the authoring-only suffix while
        # retail ships the same base model and material without it. Resolve
        # only that exact asset identity, never a similar-looking hairstyle.
        base = value.removesuffix('_dontuse')
        if part.get('material', '').lower() == base:
            replacement = dict(part, model=base)
            return asset_path(index, replacement, field, extension)
    candidates = [p for p in index if p.startswith('objects/characters/humans/')
                  and p.endswith('/' + value + extension)]
    preferred = [p for p in candidates if '/' + part['kind'] + '/' in p]
    candidates = preferred or candidates
    if len(candidates) != 1:
        raise ValueError(f'{part["kind"]} {field} {value}: expected one asset, found {candidates}')
    return candidates[0]


def bind_material(blob, material_path, emitted, target):
    """Keep authored subset IDs while expanding a single material to their slots."""
    blob = bytearray(blob)
    count, table = struct.unpack_from('<II', blob, 8)
    slots = 1
    chunks = [struct.unpack_from('<HHIII', blob, table + i*16) for i in range(count)]
    for kind, version, _, size, offset in chunks:
        if kind == 0x1017:
            if version != 0x800:
                raise ValueError('Unsupported skin subset format')
            subsets = struct.unpack_from('<I', blob, offset+4)[0]
            if 16 + subsets*36 > size:
                raise ValueError('Truncated skin subsets')
            for i in range(subsets):
                slots = max(slots, struct.unpack_from('<I', blob, offset+32+i*36)[0]+1)
    doc = ET.parse(emitted[material_path+'.mtl']).getroot()
    if slots > 1 and doc.find('SubMaterials') is None:
        if slots > 256:
            raise ValueError('Unreasonable material slot count')
        multi = ET.Element('Material', Name='NPC probe material slots', MtlFlags='256')
        children = ET.SubElement(multi, 'SubMaterials')
        for _ in range(slots):
            children.append(copy.deepcopy(doc))
        material_path = target
        emitted[target+'.mtl'] = xml(multi)
    # Runtime otherwise resolves generic wh_geometry relative to the new folder.
    for kind, version, _, size, offset in chunks:
        if kind == 0x1014 and blob[offset:offset+3] == b'wh_':
            if version != 0x802 or size < 128 or len(material_path.encode()) >= 128:
                raise ValueError('Unsupported material name')
            blob[offset:offset+128] = material_path.encode().ljust(128, b'\0')
    return bytes(blob), material_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path('D:/SteamLibrary/steamapps/common'))
    parser.add_argument('--npc', default='ska_fatherOfHenry')
    parser.add_argument('--namespace', default='npc_native_v1')
    parser.add_argument('--rig', choices=('native', 'legacy'), default='native',
                        help='KCD2 rig/animations, or a KCD1 bind-pose diagnostic')
    parser.add_argument('--character-root', action='store_true',
                        help='Package beneath objects/characters for native component definitions')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.namespace):
        parser.error('Invalid namespace')
    prefix = ('objects/characters/' if args.character_root else '') + 'gluenpc/' + args.namespace + '/'
    target_root = args.library / 'KCD2Mod/Data'
    destination = target_root / prefix
    if destination.exists():
        parser.error('Output exists; use a fresh namespace')
    Path('outputs').mkdir(exist_ok=True)
    with ExitStack() as stack:
        data = args.library / 'KingdomComeDeliverance/Data'
        rows = load_tables(stack, data)
        matches = [r for r in rows('rpg/soul') if r.get('soul_name') == args.npc]
        if len(matches) != 1:
            raise ValueError('Expected exactly one named source soul')
        soul = matches[0]
        parts = resolve_parts(soul, rows)
        cache = stack.enter_context(tempfile.TemporaryDirectory(prefix='npc-', dir='outputs'))
        index, emitted, material, mesh_bytes = asset_pack(stack, args.library, prefix, cache)
        for part in parts:
            part['mesh_path'] = asset_path(index, part, 'model', '.skin')
            part['material_path'] = asset_path(index, part, 'material', '.mtl')
        gender = next(p['gender_id'] for p in parts if p['kind'] == 'body')
        sex = {'1': 'male', '2': 'female'}[gender]
        doc = ET.Element('CharacterDefinition')
        if args.rig == 'native':
            native_path = f'objects/characters/humans/{sex}/skeleton/{sex}.cdf'
            native = None
            for path in sorted((args.library/'KCD2Mod/Data').glob('*Characters*.pak')):
                with zipfile.ZipFile(path) as archive:
                    for entry in archive.namelist():
                        if entry.lower() == native_path:
                            native = ET.fromstring(read(archive, entry))
            if native is None:
                raise FileNotFoundError(native_path)
            doc.append(copy.deepcopy(native.find('Model')))
        else:
            skeleton = 'objects/characters/humans/skeleton/' + sex + '.chr'
            skeleton_blob, skeleton_mat = bind_material(mesh_bytes(skeleton),
                material('objects/characters/skeleton_nodraw'), emitted, prefix+'skeleton_slots')
            emitted[prefix + 'skeleton.chr'] = skeleton_blob
            emitted[prefix + 'skeleton.chrparams'] = xml(ET.Element('Params'))
            ET.SubElement(doc, 'Model', File=prefix+'skeleton.chr', Material=skeleton_mat)
        attachments = ET.SubElement(doc, 'AttachmentList')
        if args.rig == 'native':
            for attachment in native.findall('AttachmentList/Attachment'):
                if not attachment.get('Binding'):
                    attachments.append(copy.deepcopy(attachment))
            if native.find('Modifiers') is not None:
                doc.append(copy.deepcopy(native.find('Modifiers')))
        for number, part in enumerate(parts):
            target = prefix + 'part' + str(number) + '.skin'
            mat = material(part['material_path'])
            if part['kind'] == 'cloth':
                tinted = prefix+'part'+str(number)+'_tinted'
                emitted[tinted+'.mtl'] = xml(apply_armor_colorization(
                    ET.parse(emitted[mat+'.mtl']).getroot(), part['armor_settings']))
                mat = tinted
            blob, mat = bind_material(mesh_bytes(part['mesh_path']),
                mat, emitted, prefix+'part'+str(number)+'_slots')
            emitted[target] = blob
            ET.SubElement(attachments, 'Attachment', Type='CA_SKIN',
                          AName=part['kind'] + str(number), Binding=target,
                          Material=mat, Flags='0')
        emitted[prefix + 'character.cdf'] = xml(doc)
        placements = []
        with zipfile.ZipFile(data / 'Levels/rataje/level.pak') as archive:
            for name in archive.namelist():
                if name.lower().endswith('.xml') and (name.lower().startswith('layers/') or name.lower() == 'objects_mission0.xml'):
                    for entity in ET.fromstring(read(archive, name)).iter('Entity'):
                        if entity.get('Name') == args.npc and entity.get('EntityClass') in ('NPC', 'NPC_Female'):
                            placements.append(dict(source=name, **entity.attrib))
        # Publish only after resolving and packaging every requested component.
        destination.mkdir(parents=True)
        for name, source in emitted.items():
            target = (target_root / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('Asset escaped namespace')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        report = dict(npc=args.npc, soul_id=soul['soul_id'], model=prefix+'character.cdf',
                      parts=parts, placements=placements, files=len(emitted), rig=args.rig,
                      idle_animation='relaxed_idle_both' if args.rig == 'native' else None,
                      limitations=['Character visual probe only; no NPC actor or AI',
                                   'Clothing morphs, body hiding and attachment presets are not applied',
                                   'Animation and skeleton compatibility require runtime verification'])
        (destination / 'npc-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
