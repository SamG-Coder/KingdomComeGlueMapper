"""Build/install the retail loader probe and audit KCD1 campaign inputs.

This is the first setup stage, not a playable campaign installer. The optional
Play KDC1 menu entry stays disabled until conversion and persistence are ready.
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

from campaign_sources import audit_opening
from upgrade_map import read
from retail_menu import menu_assets

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


def build_probe(source, target, output, diagnostics=False, menu=False):
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
                   'menu': menu_report,
                   'sources': {'kcd1': str(Path(source).resolve()), 'kcd2': str(Path(target).resolve())},
                   'files': {p.relative_to(stage).as_posix(): digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}}
        (stage / RECEIPT).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        stage.rename(output)
    return receipt


def verify_package(package):
    package = Path(package).resolve()
    receipt = json.loads((package / RECEIPT).read_text(encoding='utf-8'))
    if receipt.get('owner') != MOD_ID or receipt.get('schema') != 1:
        raise ValueError('Not an owned GlueMapper package')
    required = {'mod.manifest', 'campaign-audit.json', 'Data/' + MOD_ID + '.pak'}
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
