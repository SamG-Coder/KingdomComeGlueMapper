"""Build/install the retail loader probe and audit KCD1 campaign inputs.

This packages a world startup milestone, not a playable campaign. Play KDC1
stays disabled by default; --start-probe enables experimental native New Game.
"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from campaign_sources import audit_opening, opening_spawn
from upgrade_map import read
from retail_menu import menu_assets
from campaign_package import LEVEL, LEVEL_ID, level_registration, package_world, package_opening, write_asset_shards, add_level_tables, mounted_level_tables

MOD_ID = 'kingdomcomegluemapper'
VERSION = '0.1.0'
RECEIPT = 'gluemapper-install.json'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def game_version(game):
    text = (Path(game) / 'system.cfg').read_text(encoding='utf-8-sig')
    match = re.search(r'^\s*wh_sys_version\s*=\s*["\']?([^\s"\']+)', text, re.M | re.I)
    if not match:
        raise ValueError('Cannot identify retail KCD2 version in system.cfg')
    return match[1]


def validate_games(source, target):
    required = [(source, 'Data/GameData.pak'), (source, 'Data/Scripts.pak'),
                (source, 'Data/Levels/rataje/level.pak'), (target, 'Data/Scripts.pak'),
                (target, 'Data/IPL_GameData.pak'), (target, 'Data/Levels/trosecko/level.pak')]
    for root, relative in required:
        if not (Path(root) / relative).is_file():
            raise ValueError(f'Missing installed game file: {Path(root) / relative}')
    if Path(source).resolve() == Path(target).resolve():
        raise ValueError('KCD1 and KCD2 installations must be separate')
    return game_version(target)


def build_probe(source, target, output, diagnostics=False, menu=False, start_probe=False):
    version = validate_games(source, target)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Output already exists; choose a new build directory')
    audit = audit_opening(source)
    assets, menu_report = {}, None
    if menu:
        with zipfile.ZipFile(Path(target) / 'Data/IPL_GameData.pak') as archive:
            assets, menu_report = menu_assets(archive, read)
    # Preserve source graph anomalies in the audit; they block quest conversion,
    # not the independent retail script-loading probe.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gluemapper-build-', dir=output.parent) as temporary:
        stage = Path(temporary) / MOD_ID
        (stage / 'Data').mkdir(parents=True)
        root = ET.Element('kcd_mod')
        info = ET.SubElement(root, 'info')
        for name, value in {'name': 'KingdomComeGlueMapper', 'modid': MOD_ID,
                            'description': 'Retail loading probe; KCD1 campaign not yet playable.',
                            'author': 'SamG-Coder', 'version': VERSION,
                            'created_on': date.today().isoformat()}.items():
            ET.SubElement(info, name).text = value
        ET.SubElement(ET.SubElement(root, 'supports'), 'version').text = version
        ET.ElementTree(root).write(stage / 'mod.manifest', encoding='utf-8', xml_declaration=True)
        # No overrides of main.lua, New Game, saves, or KCD2's quest graph.
        lua = ('KingdomComeGlueMapper = KingdomComeGlueMapper or {}\n'
               'KingdomComeGlueMapper.version = "' + VERSION + '"\n'
               'KingdomComeGlueMapper.campaignReady = false\n'
               'KingdomComeGlueMapper.startProbe = ' + ('true' if start_probe else 'false') + '\n'
               'System.LogAlways("[GlueMapper] retail bootstrap loaded; campaignReady=false; version=' + VERSION + '")\n')
        if diagnostics:
            lua += (Path(__file__).resolve().parents[1] / 'runtime/retail_diagnostics.lua').read_text(encoding='utf-8')
        if menu:
            lua += (Path(__file__).resolve().parents[1] / 'runtime/campaign_menu.lua').read_text(encoding='utf-8')
        pak = stage / 'Data' / (MOD_ID + '.pak')
        with zipfile.ZipFile(pak, 'w', compression=zipfile.ZIP_STORED) as archive:
            item = zipfile.ZipInfo('Scripts/Mods/' + MOD_ID + '.lua', (2026, 1, 1, 0, 0, 0))
            item.create_system = 0
            archive.writestr(item, lua)
            for name, contents in sorted(assets.items()):
                item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                item.create_system = 0
                archive.writestr(item, contents)
        # Audit is local setup evidence, not loaded quest data or runtime state.
        (stage / 'campaign-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
        receipt = {'schema': 1, 'owner': MOD_ID, 'version': VERSION, 'kind': 'retail-loader-probe',
                   'target_version': version, 'ready_to_play': False,
                   'diagnostics': diagnostics,
                   'start_probe': start_probe,
                   'menu': menu_report,
                   'sources': {'kcd1': str(Path(source).resolve()), 'kcd2': str(Path(target).resolve())},
                   'files': {p.relative_to(stage).as_posix(): digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}}
        (stage / RECEIPT).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        stage.rename(output)
    return receipt


def build_campaign(source, target, converted_data, source_level, output, diagnostics=False):
    """Assemble the converted world and retail opening inputs atomically.

    Native New Game routing and translated quest execution are still gated.
    """
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Output already exists; choose a new build directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gluemapper-campaign-', dir=output.parent) as temporary:
        stage = Path(temporary) / MOD_ID
        receipt = build_probe(source, target, stage, diagnostics=diagnostics, menu=True)
        world = package_world(converted_data, source_level, stage)
        level_pak = stage / f'Data/Levels/{LEVEL}/level.pak'
        tables_pak = level_pak.with_suffix('.tables.pak')
        spawn = opening_spawn(source)
        world['empty_level_tables'] = add_level_tables(level_pak, tables_pak, target, spawn)
        tables_pak.replace(level_pak)
        opening = package_opening(source, stage)
        with zipfile.ZipFile(stage / 'Data' / (MOD_ID + '.pak'), 'a', zipfile.ZIP_STORED) as pak:
            pak.writestr('Libs/Tables/level__' + MOD_ID + '.xml', level_registration(target))
            for name, data in mounted_level_tables(level_pak, MOD_ID).items():
                pak.writestr(name, data)
        campaign = {'schema': 1, 'id': 'kcd1', 'level': LEVEL,
                    'opening_quest': 'q_skalitz', 'world': world, 'opening_sources': opening,
                    'new_game': {'level': LEVEL, 'level_package': f'Levels/{LEVEL}/level.pak',
                                 'level_id': LEVEL_ID, 'initial_level_cvar': 'wh_sys_BaseLevelId',
                                 'initial_level_hook_validated': False,
                                 'native_dispatch_connected': True, 'spawn': spawn},
                    'ready_to_play': False,
                    'blockers': ['Campaign initial state and actor initialization',
                                 'Retail quest translation and dependency closure',
                                 'Native save/load validation',
                                 'Retail world and native asset validation']}
        (stage / 'campaign.json').write_text(json.dumps(campaign, indent=2), encoding='utf-8')
        manifest = ET.parse(stage / 'mod.manifest')
        manifest.find('./info/description').text = 'Converted KCD1 world and opening sources; campaign execution pending.'
        manifest.write(stage / 'mod.manifest', encoding='utf-8', xml_declaration=True)
        receipt.update(kind='retail-campaign-package', campaign_level=LEVEL)
        receipt['files'] = {p.relative_to(stage).as_posix(): digest(p)
                            for p in sorted(stage.rglob('*')) if p.is_file() and p.name != RECEIPT}
        (stage / RECEIPT).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        verify_package(stage)
        stage.rename(output)
    return receipt


def verify_package(package):
    package = Path(package).resolve()
    receipt = json.loads((package / RECEIPT).read_text(encoding='utf-8'))
    if receipt.get('owner') != MOD_ID or receipt.get('schema') != 1:
        raise ValueError('Not an owned GlueMapper package')
    required = {'mod.manifest', 'campaign-audit.json', 'Data/' + MOD_ID + '.pak'}
    if receipt.get('kind') == 'retail-campaign-package':
        required |= {'campaign.json',
                     'Data/kingdomcomegluemapper_sources.pak',
                     f'Data/Levels/{LEVEL}/level.pak', f'Data/Levels/{LEVEL}/terrain.pak',
                     f'Data/Levels/{LEVEL}/levelinfo.xml'}
        campaign = json.loads((package / 'campaign.json').read_text(encoding='utf-8'))
        required.update('Data/' + name for name in campaign['world'].get('asset_archives', ['kingdomcomegluemapper_world.pak']))
    if not required.issubset(receipt.get('files', {})):
        raise ValueError('Incomplete setup package receipt')
    for relative, expected in receipt['files'].items():
        path = package / relative
        if not path.resolve().is_relative_to(package) or path.is_symlink():
            raise ValueError(f'Invalid package path: {relative}')
        if digest(path) != expected:
            raise ValueError(f'Package changed: {relative}')
    actual = {p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
    if actual != set(receipt['files']) | {RECEIPT}:
        raise ValueError('Unexpected files in package')
    if ET.parse(package / 'mod.manifest').findtext('./info/modid') != MOD_ID:
        raise ValueError('Manifest modid does not match package owner')
    return receipt


def refresh_runtime(package, output, diagnostics=False, start_probe=False):
    """Rebuild menu/bootstrap from source, preserving a verified content bundle."""
    package, output = Path(package).resolve(), Path(output).resolve()
    previous = verify_package(package)
    if start_probe and previous['kind'] != 'retail-campaign-package':
        raise ValueError('Startup probe requires a packaged campaign world')
    if output.exists():
        raise ValueError('Output already exists; choose a new build directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gluemapper-refresh-', dir=output.parent) as temporary:
        stage = Path(temporary) / MOD_ID
        receipt = build_probe(previous['sources']['kcd1'], previous['sources']['kcd2'], stage,
                              diagnostics=diagnostics, menu=bool(previous.get('menu')), start_probe=start_probe)
        if receipt['target_version'] != previous['target_version']:
            raise ValueError('Game version changed; rebuild content before refreshing runtime')
        if previous['kind'] == 'retail-campaign-package':
            for relative in previous['files']:
                if relative in receipt['files']:
                    continue
                if relative == 'Data/kingdomcomegluemapper_world.pak':
                    with zipfile.ZipFile(package / relative) as old:
                        shards, assets = write_asset_shards(stage / 'Data', ((n, old.read(n)) for n in sorted(old.namelist())))
                    campaign = json.loads((package / 'campaign.json').read_text(encoding='utf-8'))
                    if assets != campaign['world']['imported_assets']:
                        raise ValueError('Repacked assets differ from the verified world manifest')
                    campaign['world']['asset_archives'] = shards
                    (stage / 'campaign.json').write_text(json.dumps(campaign, indent=2), encoding='utf-8')
                    continue
                if relative == 'campaign.json' and (stage / relative).exists():
                    continue
                dest = stage / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(package / relative, dest)
            level_pak = stage / f'Data/Levels/{LEVEL}/level.pak'
            tables_pak = level_pak.with_suffix('.tables.pak')
            spawn = opening_spawn(previous['sources']['kcd1'])
            added = add_level_tables(level_pak, tables_pak, previous['sources']['kcd2'], spawn)
            tables_pak.replace(level_pak)
            campaign = json.loads((stage / 'campaign.json').read_text(encoding='utf-8'))
            campaign['world']['empty_level_tables'] = sorted(set(campaign['world'].get('empty_level_tables', [])) | set(added))
            campaign['new_game'].update(native_dispatch_connected=True, spawn=spawn)
            (stage / 'campaign.json').write_text(json.dumps(campaign, indent=2), encoding='utf-8')
            with zipfile.ZipFile(stage / 'Data' / (MOD_ID + '.pak'), 'a', zipfile.ZIP_STORED) as pak:
                pak.writestr('Libs/Tables/level__' + MOD_ID + '.xml', level_registration(previous['sources']['kcd2']))
                for name, data in mounted_level_tables(level_pak, MOD_ID).items():
                    pak.writestr(name, data)
            receipt.update(kind=previous['kind'], campaign_level=previous['campaign_level'])
            shutil.copyfile(package / 'mod.manifest', stage / 'mod.manifest')
        receipt['files'] = {p.relative_to(stage).as_posix(): digest(p)
                            for p in sorted(stage.rglob('*')) if p.is_file() and p.name != RECEIPT}
        (stage / RECEIPT).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        verify_package(stage)
        stage.rename(output)
    return receipt


def update_runtime(package, backup, diagnostics=False, start_probe=False):
    """Update only the small runtime archive, preserving verified world content.

    Close the game first. The old archive and receipt are retained outside the
    package, and restored if a replacement fails.
    """
    package, backup = Path(package).resolve(), Path(backup).resolve()
    previous = verify_package(package)
    if backup.exists() or backup.is_relative_to(package):
        raise ValueError('Runtime backup must be a new path outside the package')
    if start_probe and previous['kind'] != 'retail-campaign-package':
        raise ValueError('Startup probe requires a packaged campaign world')
    relative = 'Data/' + MOD_ID + '.pak'
    with tempfile.TemporaryDirectory(prefix='.gluemapper-runtime-', dir=package.parent) as temporary:
        stage = Path(temporary) / MOD_ID
        built = build_probe(previous['sources']['kcd1'], previous['sources']['kcd2'], stage,
                            diagnostics=diagnostics, menu=bool(previous.get('menu')), start_probe=start_probe)
        if built['target_version'] != previous['target_version'] or built['files']['campaign-audit.json'] != previous['files']['campaign-audit.json']:
            raise ValueError('Installed game inputs changed; rebuild the campaign')
        if previous['kind'] == 'retail-campaign-package':
            with zipfile.ZipFile(stage / relative, 'a', zipfile.ZIP_STORED) as z:
                z.writestr('Libs/Tables/level__' + MOD_ID + '.xml', level_registration(previous['sources']['kcd2']))
        receipt = json.loads(json.dumps(previous))
        receipt.update(diagnostics=diagnostics, start_probe=start_probe, menu=built['menu'])
        changed = [relative]
        if previous['kind'] == 'retail-campaign-package':
            level_relative = f'Data/Levels/{LEVEL}/level.pak'
            (stage / level_relative).parent.mkdir(parents=True)
            spawn = opening_spawn(previous['sources']['kcd1'])
            added = add_level_tables(package / level_relative, stage / level_relative, previous['sources']['kcd2'], spawn)
            with zipfile.ZipFile(stage / relative, 'a', zipfile.ZIP_STORED) as z:
                for name, data in mounted_level_tables(stage / level_relative, MOD_ID).items():
                    z.writestr(name, data)
            campaign = json.loads((package / 'campaign.json').read_text(encoding='utf-8'))
            campaign['world']['empty_level_tables'] = sorted(set(campaign['world'].get('empty_level_tables', [])) | set(added))
            campaign['new_game'].update(native_dispatch_connected=True, spawn=spawn)
            (stage / 'campaign.json').write_text(json.dumps(campaign, indent=2), encoding='utf-8')
            changed += [level_relative, 'campaign.json']
        for name in changed:
            receipt['files'][name] = digest(stage / name)
        (stage / RECEIPT).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        changed.append(RECEIPT)
        for name in changed:
            (backup / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package / name, backup / name)
        try:
            for name in changed:
                (stage / name).replace(package / name)
        except OSError:
            for name in changed:
                shutil.copyfile(backup / name, package / name)
            raise
    return receipt


def uninstall(target, backup):
    """Move a verified owned package out of Mods, preserving it for rollback."""
    target, backup = Path(target).resolve(), Path(backup).resolve()
    mods = target / 'Mods'
    source = mods / MOD_ID
    if source.resolve() != source or mods.resolve() != mods:
        raise ValueError('Refusing redirected mod directory')
    verify_package(source)
    if backup.exists() or backup.is_relative_to(mods):
        raise ValueError('Backup must be a new path outside Mods')
    backup.parent.mkdir(parents=True, exist_ok=True)
    # Rename is atomic on the same volume. Cross-volume moves are intentionally
    # refused rather than risking a partially copied rollback package.
    source.rename(backup)
    return backup


def install(package, target):
    package, target = Path(package).resolve(), Path(target).resolve()
    receipt = verify_package(package)
    if receipt['target_version'] != game_version(target):
        raise ValueError('Game version differs from this build; rebuild setup')
    mods = target / 'Mods'
    if mods.is_symlink() or (mods.exists() and mods.resolve() != mods):
        raise ValueError('Mods directory is redirected; refusing implicit external install')
    order = mods / 'mod_order.txt'
    if order.exists():
        entries = [s.strip() for s in order.read_text(encoding='utf-8-sig').splitlines()]
        if MOD_ID not in entries:
            raise ValueError(f'{order} excludes {MOD_ID}; add its modid to enable it')
    destination = mods / MOD_ID
    if destination.exists():
        raise ValueError('Mod already installed; keep it intact and remove the owned probe before reinstalling')
    mods.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gluemapper-stage-', dir=target) as temporary:
        stage = Path(temporary) / MOD_ID
        shutil.copytree(package, stage)
        verify_package(stage)
        stage.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build-probe')
    build.add_argument('--kcd1', required=True, type=Path)
    build.add_argument('--kcd2', required=True, type=Path)
    build.add_argument('--output', required=True, type=Path)
    build.add_argument('--diagnostics', action='store_true', help='Log runtime API availability and menu events')
    build.add_argument('--menu', action='store_true', help='Install the disabled Play KDC1 entry while campaign conversion is in progress')
    campaign = commands.add_parser('build-campaign', help='Package a converted world and retail opening quest sources')
    campaign.add_argument('--kcd1', required=True, type=Path)
    campaign.add_argument('--kcd2', required=True, type=Path)
    campaign.add_argument('--converted-data', required=True, type=Path, help='Data directory produced by conversion stages')
    campaign.add_argument('--source-level', required=True)
    campaign.add_argument('--output', required=True, type=Path)
    campaign.add_argument('--diagnostics', action='store_true')
    refresh = commands.add_parser('refresh-runtime', help='Rebuild runtime code while preserving verified packaged content')
    refresh.add_argument('--package', required=True, type=Path)
    refresh.add_argument('--output', required=True, type=Path)
    refresh.add_argument('--diagnostics', action='store_true')
    refresh.add_argument('--start-probe', action='store_true', help='Enable the experimental native New Game route; quests remain unconverted')
    update = commands.add_parser('update-runtime', help='Update runtime in a verified package with a small rollback backup; close the game first')
    update.add_argument('--package', required=True, type=Path)
    update.add_argument('--backup', required=True, type=Path)
    update.add_argument('--diagnostics', action='store_true')
    update.add_argument('--start-probe', action='store_true')
    deploy = commands.add_parser('install')
    deploy.add_argument('--package', required=True, type=Path)
    deploy.add_argument('--kcd2', required=True, type=Path)
    check = commands.add_parser('verify')
    check.add_argument('--package', required=True, type=Path)
    remove = commands.add_parser('uninstall')
    remove.add_argument('--kcd2', required=True, type=Path)
    remove.add_argument('--backup', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'build-probe':
            result = build_probe(args.kcd1, args.kcd2, args.output, args.diagnostics, args.menu)
        elif args.command == 'build-campaign':
            result = build_campaign(args.kcd1, args.kcd2, args.converted_data, args.source_level, args.output, args.diagnostics)
        elif args.command == 'refresh-runtime':
            result = refresh_runtime(args.package, args.output, args.diagnostics, args.start_probe)
        elif args.command == 'update-runtime':
            result = update_runtime(args.package, args.backup, args.diagnostics, args.start_probe)
        elif args.command == 'install':
            result = {'installed': str(install(args.package, args.kcd2)), 'ready_to_play': False}
        elif args.command == 'uninstall':
            result = {'preserved_at': str(uninstall(args.kcd2, args.backup))}
        else:
            result = verify_package(args.package)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f'Setup failed: {error}\n')


if __name__ == '__main__':
    main()
