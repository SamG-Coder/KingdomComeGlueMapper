"""Add coach travel to an owned world package, under its existing mod identity."""
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

from campaign_package import level_registration
from region_travel_dialogue import DIALOGUE, HOST, PORT, STRING
from region_travel_tables import managed_patches
from setup_campaign import MOD_ID, RECEIPT, VERSION, digest, verify_package
from upgrade_map import read, xml

MENU_FILES = {
    'scripts/mods/kingdomcomegluemapper.lua',
    'libs/ui/menu.gfx', 'libs/ui/uielements/menu.xml',
}
LEVEL_TABLE = f'Libs/Tables/level__{MOD_ID}.xml'
BRIDGE_PAK = 'Data/gluemappertravel.pak'


def validate_bridge(package):
    """Check the actual packaged menu, switch and mount names, not build inputs."""
    package = Path(package)
    if ET.parse(package/'mod.manifest').findtext('info/modid') != MOD_ID:
        raise ValueError('Bridge must use the installed world mod identity')
    with zipfile.ZipFile(package/BRIDGE_PAK) as pak:
        names = pak.namelist()
        for name in names:
            if name.lower().startswith('mods/gluemappertravel/'):
                raise ValueError('Bridge contains a table mount for the obsolete separate mod')
            if name.lower().startswith('libs/tables/') and '__' in name:
                if not name.endswith('__'+MOD_ID+'.xml'):
                    raise ValueError('Bridge table suffix does not match manifest: '+name)
        dialogue = ET.fromstring(read(pak, DIALOGUE))
        choices = dialogue.findall('./Skald/FaderDialog/Dialogue/Decision/Sequences/Sequence')
        if not any(c.find(f"UiPrompt[@StringName='{STRING}']") is not None for c in choices):
            raise ValueError('Rattay option missing from the native coachman menu')
        if not any(c.find("UiPrompt[@StringName='ui_prechod_z_seq1_Gr38']") is not None for c in choices):
            raise ValueError('Native Kuttenberg option was lost')
        host = ET.fromstring(read(pak, HOST))
        if host.find(f".//Edge[@From='prechod_z_trosecka_na_kutnohorsko.{PORT}']") is None:
            raise ValueError('Coachman menu is not connected to the travel graph')
        routes = ET.fromstring(read(pak, f'Libs/Tables/LevelSwitch__{MOD_ID}.xml'))
        if routes.find(".//LevelSwitchData[@Name='gluemappertravel_to_kcd1'][@TargetLevelId='1001']") is None:
            raise ValueError('Rattay level switch is not registered')
    with zipfile.ZipFile(package/f'Data/{MOD_ID}.pak') as pak:
        if MENU_FILES.intersection(n.lower() for n in pak.namelist()):
            raise ValueError('Old New Game menu is still installed')
        levels = ET.fromstring(read(pak, LEVEL_TABLE))
        if levels.find(".//*[@LevelId='1001']") is None:
            raise ValueError('Travel destination is not registered')
    with zipfile.ZipFile(package/'Localization/English_xml.pak') as pak:
        keys = {row.findtext('Cell') for row in ET.fromstring(read(pak, 'text_ui_dialog.xml'))}
        if not {STRING, 'ui_prechod_z_seq1_Gr38'} <= keys:
            raise ValueError('Coachman localization is incomplete')


def combine(base, bridge, target, output):
    base, bridge, output = map(Path, (base, bridge, output))
    receipt = verify_package(base)
    if output.exists():
        raise FileExistsError('Choose a fresh combined package directory')
    if output.resolve().is_relative_to(base.resolve()):
        raise ValueError('Combined package must be outside the installed mod')
    if ET.parse(bridge/'mod.manifest').findtext('info/modid') != MOD_ID:
        raise ValueError('Build the bridge with modid='+MOD_ID)
    shutil.copytree(base, output)
    # Remove only previously recorded bridge files, inside the private build.
    for name in receipt.get('travel_bridge', {}).get('files', []):
        path = output/name
        if not path.resolve().is_relative_to(output.resolve()):
            raise ValueError('Invalid prior bridge path')
        if path.is_file():
            path.unlink()
    bridge_files = []
    for path in sorted(bridge.rglob('*')):
        if not path.is_file() or path.name == 'mod.manifest':
            continue
        name = path.relative_to(bridge).as_posix()
        dest = output/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        bridge_files.append(name)
    # There must be exactly one level patch under the shared mod identity.
    bridge_pak = output/BRIDGE_PAK
    with zipfile.ZipFile(bridge_pak) as pak:
        entries = [(i, read(pak, i.filename)) for i in pak.infolist()]
    new_level = next(blob for info, blob in entries if info.filename == LEVEL_TABLE)
    levels = managed_patches({LEVEL_TABLE: level_registration(target),
                              'Libs/Tables/level__bridge.xml': new_level}, MOD_ID)[LEVEL_TABLE]
    with zipfile.ZipFile(bridge_pak, 'w', zipfile.ZIP_STORED) as pak:
        for info, blob in entries:
            if info.filename != LEVEL_TABLE:
                pak.writestr(info, blob)
    root_pak = output/f'Data/{MOD_ID}.pak'
    with zipfile.ZipFile(root_pak) as pak:
        entries = [(i, read(pak, i.filename)) for i in pak.infolist()]
    with zipfile.ZipFile(root_pak, 'w', zipfile.ZIP_STORED) as pak:
        for info, blob in entries:
            if info.filename.lower() not in MENU_FILES | {LEVEL_TABLE.lower()}:
                pak.writestr(info, blob)
        pak.writestr(LEVEL_TABLE, levels)
    manifest = ET.parse(output/'mod.manifest').getroot()
    manifest.find('info/name').text = 'KingdomComeGlueMapper'
    info = manifest.find('info')
    for key, value in {'version': VERSION, 'description': 'KCD1 world, coach travel and Rattay services in one mod.'}.items():
        node = info.find(key)
        if node is None: node = ET.SubElement(info, key)
        node.text = value
    (output/'mod.manifest').write_bytes(xml(manifest))
    receipt.update(version=VERSION, menu=False, start_probe=False,
                   travel_bridge=dict(files=bridge_files, destination='kcd1_travel', runtime_verified=False))
    receipt['files'] = {p.relative_to(output).as_posix(): digest(p)
                        for p in sorted(output.rglob('*')) if p.is_file() and p.name != RECEIPT}
    (output/RECEIPT).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    validate_bridge(output)
    verify_package(output)
    return output
