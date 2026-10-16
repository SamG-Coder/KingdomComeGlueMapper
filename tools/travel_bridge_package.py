"""Add coach travel to an owned world package, under its existing mod identity."""
import json
import os
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
PLAYER_HOOK = 'Scripts/Entities/actor/player.lua'

HORSE_HOOK_MARKERS = (b'GlueTravelHorseRecovery', b'GluePlayerAnimationUpdates')


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
        if PLAYER_HOOK not in names:
            raise ValueError('Bridge is missing the shipped player hook: ' + PLAYER_HOOK)
        player = read(pak, PLAYER_HOOK)
        for marker in HORSE_HOOK_MARKERS:
            if marker not in player:
                raise ValueError('Bridge player hook marker is missing: ' + marker.decode())
        if b'GlueTravelHorseRoadArrivals=' not in player:
            raise ValueError('Bridge player hook is missing the horse road arrivals table')
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


def link_or_copy(source, destination):
    # Share the multi-GB world files instead of writing a second copy. Every
    # file combine changes is replaced through unlinked(), never edited in place.
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def unlinked(path):
    """Detach path from any hard-linked base file before it is rewritten."""
    path.unlink(missing_ok=True)
    return path


def combine(base, bridge, target, output):
    base, bridge, output = map(Path, (base, bridge, output))
    receipt = verify_package(base)
    base_files = receipt['files']
    if output.exists():
        raise FileExistsError('Choose a fresh combined package directory')
    if output.resolve().is_relative_to(base.resolve()):
        raise ValueError('Combined package must be outside the installed mod')
    if ET.parse(bridge/'mod.manifest').findtext('info/modid') != MOD_ID:
        raise ValueError('Build the bridge with modid='+MOD_ID)
    shutil.copytree(base, output, copy_function=link_or_copy)
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
        shutil.copy2(path, unlinked(dest))
        bridge_files.append(name)
    # There must be exactly one level patch under the shared mod identity.
    bridge_pak = output/BRIDGE_PAK
    with zipfile.ZipFile(bridge_pak) as pak:
        entries = [(i, read(pak, i.filename)) for i in pak.infolist()]
    new_level = next((blob for info, blob in entries if info.filename == LEVEL_TABLE), None)
    if new_level is None:
        raise ValueError('Bridge package is missing ' + LEVEL_TABLE)
    levels = managed_patches({LEVEL_TABLE: level_registration(target),
                              'Libs/Tables/level__bridge.xml': new_level}, MOD_ID)[LEVEL_TABLE]
    with zipfile.ZipFile(unlinked(bridge_pak), 'w', zipfile.ZIP_STORED) as pak:
        for info, blob in entries:
            if info.filename != LEVEL_TABLE:
                pak.writestr(info, blob)
    root_pak = output/f'Data/{MOD_ID}.pak'
    with zipfile.ZipFile(root_pak) as pak:
        entries = [(i, read(pak, i.filename)) for i in pak.infolist()]
    with zipfile.ZipFile(unlinked(root_pak), 'w', zipfile.ZIP_STORED) as pak:
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
    unlinked(output/'mod.manifest').write_bytes(xml(manifest))
    roof_report = None
    if (output/'Data/Levels/kcd1_travel/level.pak').is_file():
        from probe_activation import repair_package as repair_probe_activation
        probe_report = repair_probe_activation(output, receipt['sources']['kcd1'])
        from roof_clip_repair import repair_package
        roof_report = repair_package(output, receipt['sources']['kcd1'])
        from brush_registration import repair_package as repair_brushes
        brush_report = repair_brushes(output)
        from water_layers import repair_package as repair_water
        water_report = repair_water(output, receipt['sources']['kcd1'])
    receipt.update(version=VERSION, menu=False, start_probe=False,
                   travel_bridge=dict(files=bridge_files, destination='kcd1_travel', runtime_verified=False))
    if roof_report is not None:
        receipt['travel_bridge']['probe_activation'] = probe_report
        receipt['travel_bridge']['brush_registration'] = {'changed_brushes': brush_report['changed_brushes']}
        receipt['travel_bridge']['water_layers'] = water_report
        receipt['travel_bridge']['roof_clipping'] = {
            key: roof_report[key] for key in ('roof_placements', 'changed_clip_count', 'linked_probe_count')}
    # Untouched world files keep the digests verify_package(base) just checked;
    # verify_package(output) below still re-reads every file once.
    rewritten = {*bridge_files, BRIDGE_PAK, f'Data/{MOD_ID}.pak', 'mod.manifest'}
    if roof_report is not None:
        rewritten.update(('Data/Levels/kcd1_travel/level.pak', 'roof-clip-repair.json'))
        rewritten.add('probe-activation-repair.json')
        rewritten.add('Data/Levels/kcd1_travel/terrain.pak')
    names = [p.relative_to(output).as_posix() for p in sorted(output.rglob('*')) if p.is_file() and p.name != RECEIPT]
    receipt['files'] = {n: base_files[n] if n in base_files and n not in rewritten else digest(output/n) for n in names}
    unlinked(output/RECEIPT).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    validate_bridge(output)
    verify_package(output)
    return output
