"""Build a Zora horse-sales test from an existing combined mod, preserving its world.

Writes a staging directory only. Installation is deliberately separate from
conversion/validation and must happen while the game is closed.
"""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from character_person import actor_resources
from character_person_ai import FACTION_PATH
from character_person_appearance import build_appearance
from region_travel_services import resolve_merchant
from region_travel_dialogue import merge_localization
from region_travel_tables import managed_patches
from horse_trader import roster, horse_resources
from horse_trader_dialogue import attach, ROLE
from horse_trader_world import register, STASH_PREFIX
from merchant_stash import source_stash, stash_assets
from upgrade_map import read, xml, digest

ENTRY = 'Quests/GlueTravel/entry/kcd1_travel.xml'
PROJECT = 'Quests/GlueTravel/GlueTravel_kcd1.xml'
LEVEL_FILES = ('objects_mission0.xml', 'whdata_0', 'waitinglinks.xml', 'triggerareas.fubar', 'tables/ai/scheduler.xml')



def rewrite(source, target, replacements):
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, 'x', zipfile.ZIP_STORED, allowZip64=False) as dest:
        existing = set()
        for info in original.infolist():
            if info.filename in existing: raise ValueError('Duplicate archive path: '+info.filename)
            existing.add(info.filename)
            dest.writestr(info, replacements.get(info.filename, read(original, info.filename)))
        for name, blob in replacements.items():
            if name not in existing: dest.writestr(name, blob)


def build(source, target, mod, output):
    if output.exists(): raise FileExistsError('Choose a fresh staging directory')
    output.mkdir(parents=True)
    manifest = ET.parse(mod/'mod.manifest'); modid = manifest.findtext('info/modid')
    if not modid: raise ValueError('Mod ID is missing')
    travel = mod/'Data/gluemappertravel.pak'
    level = mod/'Data/Levels/kcd1_travel/level.pak'
    localization = mod/'Localization/English_xml.pak'
    originals = {str(p.relative_to(mod)): digest(p) for p in (travel, level, localization)}
    with zipfile.ZipFile(travel) as archive:
        resources = {n: read(archive, n) for n in archive.namelist()
                     if n.startswith(('Libs/Tables/', 'Libs/Storm/', 'Quests/')) and n.endswith('.xml')}
    if 'Libs/Tables/rpg/soul__gm_neuhof_horses.xml' in resources or any(b'gm_neuhof_horse_sales' in b for b in resources.values()):
        raise ValueError('Horse sales already present; rebuild from the recorded baseline')
    # The generic faction adapter merges ancestry; retain the already converted
    # innkeeper/Theresa factions before collecting managed patches again.
    faction = f'Libs/Tables/rpg/FactionTree__{modid}.xml'
    if faction in resources: resources[FACTION_PATH] = resources.pop(faction)
    with zipfile.ZipFile(level) as archive:
        files = {n: read(archive, n) for n in LEVEL_FILES}
    print('Resolving original Neuhof stable roster and Zora', flush=True)
    horses = roster(source)
    person = resolve_merchant(source, 'neu_zdena')
    print('Converting Zora appearance and clothing', flush=True)
    character_dir = output/'zora-character'
    character = build_appearance(source, target, person, 'gm_neuhof_zora', character_dir)
    resources = actor_resources(target, person, character, 'gm_neuhof_zora', role_name=ROLE,
        native_soul_table='Libs/Tables/rpg/soul__tsem.xml', native_soul_name='tsem_man_15',
        native_role_name='PODKONI_TROSECKO', resources=resources)
    zora_soul = ET.fromstring(resources['Libs/Tables/rpg/soul__gm_neuhof_zora.xml']).find('souls/soul')
    resources = horse_resources(resources, target, horses, zora_soul.get('factionName'))
    print('Registering stable, purchase dialogue and companion transfer', flush=True)
    files, resources, schedules = register(files, resources, person, horses, source, target, ENTRY)
    with zipfile.ZipFile(target/'Data/Scripts.pak') as scripts:
        resources, strings = attach(resources, ENTRY, horses, person['soul']['soul_id'], scripts, person['name'])
    character_assets = stash_assets(source, source_stash(source, person['shop_entity']), STASH_PREFIX)
    for path in character_dir.rglob('*'):
        if path.is_file() and not path.relative_to(character_dir).as_posix().startswith('Localization/'):
            name = path.relative_to(character_dir).as_posix()
            if name in resources: raise ValueError('Character resource collision: '+name)
            if name.startswith('Libs/Tables/'):
                resources[name] = path.read_bytes()
            else:
                character_assets[name] = path.read_bytes()
    resources = managed_patches(resources, modid)
    for name, blob in files.items():
        if name.startswith('tables/'):
            resources[f'Mods/{modid}/Data/Levels/kcd1_travel/{name}'] = blob
    with zipfile.ZipFile(localization) as archive:
        texts = {n: read(archive, n) for n in archive.namelist()}
    texts['text_ui_dialog.xml'] = merge_localization(texts['text_ui_dialog.xml'], strings)
    with zipfile.ZipFile(source/'Localization/English_xml.pak') as archive:
        source_names = ET.fromstring(read(archive, 'text_ui_soul.xml'))
    keys = {h['name_key'] for h in horses} | {person['instance'].findtext('StaticData/NameStringId')}
    selected = ET.Element('Table')
    for row in source_names.findall('Row'):
        if row.findtext('Cell') in keys: selected.append(row)
    texts['text_ui_soul.xml'] = merge_localization(texts['text_ui_soul.xml'], xml(selected))
    item_names = character_dir/'Localization/English/text_ui_items.xml'
    if item_names.exists(): texts['text_ui_items.xml'] = merge_localization(texts['text_ui_items.xml'], item_names.read_bytes())
    package = output/'package'
    # Existing managed entries are replaced, not shadowed by a second mod.
    print('Writing staged package while preserving existing scenery and quests', flush=True)
    rewrite(travel, package/'Data/gluemappertravel.pak', resources)
    rewrite(level, package/'Data/Levels/kcd1_travel/level.pak', files)
    rewrite(localization, package/'Localization/English_xml.pak', texts)
    # Keep asset shards below the setup builder's 1 GiB boundary. This is an
    # additional PAK inside the SAME mod, not a second mod or global override.
    asset_pak = package/'Data/gluemapper_neuhof_assets.pak'
    if sum(map(len, character_assets.values())) > 1024**3-1024**2:
        raise ValueError('Stable appearance exceeds one asset shard; split before installing')
    with zipfile.ZipFile(asset_pak, 'x', zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, blob in character_assets.items(): archive.writestr(name, blob)
    for name, blob in resources.items():
        if name.startswith('Libs/Tables/'):
            dest = output/'validation'/name
            dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(blob)
    report = dict(schema=1, source_mod=str(mod), baseline=originals, seller=person['name'],
        seller_position=person['actor'].get('Pos'), horse_count=len(horses),
        horses=[dict(name=h['name'], display_name=h['display_name'], soul=h['soul']['soul_id'],
                     position=h['actor'].get('Pos'), stats={k:h['soul'][k] for k in ('agi','str','vit','courage')}) for h in horses],
        character=character, schedules=schedules, runtime_verified=False,
        limitations=['Native KCD2 sale-horse appearance used for all horses; source horse coat conversion pending',
                     'Cash/native trade-in only; haggling and cinematic washing not offered',
                     'Native interruptible idle at original positions; original stable daily routines pending',
                     'Buying, X recall, save/reload and travel persistence require in-game validation'],
        files={str(p.relative_to(package)):digest(p) for p in package.rglob('*') if p.is_file()})
    (output/'horse-trader-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','target','mod','output'): parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    report = build(args.source, args.target, args.mod, args.output)
    print('Staged', report['horse_count'], 'horses. Runtime validation pending.')
