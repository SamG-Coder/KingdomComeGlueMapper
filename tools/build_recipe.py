"""Shared, versioned recipe for clean launcher and development builds.

Run against a private freshly combined world/travel package. The historical
builders remain the implementation; this module supplies their order and applies
their verified outputs without mutating an installed mod or a hard-linked base.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

from setup_campaign import RECEIPT, digest, verify_package

VERSION = 66
STAGE_LABELS = ('Open Talburg and Skalitz test gates', 'World containers, loot and keys', 'Neuhof horse shop and starter dialogue', 'Test supplies and getting started', 'Rattay smithing stations and coal repair', 'Smithing warning dialogue', 'Church altar prayer', 'Workshop recipes and resources', 'KCD1 New Game menu')
REVIEWED_BLEND_MODELS = (
    'objects/buildings/houses/modular/6x15/6x15_roof_p_03_flipped.cgf',
    'objects/buildings/houses/modular/6x15/6x15_basefloor_p_2_flipped.cgf',
)


def require_current(receipt):
    """A hash-valid old recipe is not a complete current setup build."""
    report = receipt.get('build_recipe', {})
    if report.get('version') != VERSION or report.get('status') != 'complete':
        raise ValueError('This package uses an incomplete or older gameplay recipe. Rebuild with the current setup.')
    if not all(report.get(key) for key in ('content_validation', 'streaming_validation', 'shader_validation')):
        raise ValueError('This package has no current campaign/streaming/shader validation. Rebuild with the current setup.')
    from campaign_start_policy import POLICY
    if report['content_validation'].get('start_policy') != POLICY:
        raise ValueError('This package has no existing-player campaign protection. Rebuild with the current setup.')
    if receipt.get('travel_bridge') and not receipt.get('single_playable_world'):
        raise ValueError('This package requires the single-world resume migration before installation.')
    if receipt.get('travel_bridge'):
        from character_person_appearance import APPEARANCE_POLICY_VERSION
        appearance = receipt.get('source_appearance_upgrade', {})
        if (appearance.get('version') != APPEARANCE_POLICY_VERSION
                or appearance.get('mode') != 'source_converter'
                or appearance.get('equipment_validation') is not True):
            raise ValueError('This package needs source clothing conversion and equipment validation. Resume with the current setup.')
        from setup_gameplay import STATIC_WORLD_PROFILES
        from static_navigation_profiles import VERSION as NAVIGATION_VERSION
        navigation = receipt.get('static_navigation_profiles', {})
        if (navigation.get('version') != NAVIGATION_VERSION
                or navigation.get('mode') != 'materialized_static_profiles'
                or [p.get('profile') for p in navigation.get('profiles', [])] != list(STATIC_WORLD_PROFILES)):
            raise ValueError('This package needs static-world navigation conversion before installation. Resume with the current setup.')
        from animal_population import VERSION as ANIMAL_VERSION
        from setup_animal_population import MODE as ANIMAL_MODE
        animals = receipt.get('animal_population', {})
        if (animals.get('version') != ANIMAL_VERSION or animals.get('mode') != ANIMAL_MODE
                or animals.get('profiles') != list(STATIC_WORLD_PROFILES)
                or animals.get('scheduler_validation') is not True):
            raise ValueError('This package needs native animal scheduler conversion before installation. Resume with the current setup.')
        from setup_profile_population import VERSION as POPULATION_VERSION, MODE as POPULATION_MODE
        population = receipt.get('profile_population', {})
        if (population.get('version') != POPULATION_VERSION or population.get('mode') != POPULATION_MODE
                or population.get('profiles') != list(STATIC_WORLD_PROFILES)
                or population.get('scheduler_validation') is not True):
            raise ValueError('This package needs source-profile population conversion before installation. Resume with the current setup.')
        from terrain_object_tree import VERSION as PLACEMENT_VERSION
        if receipt.get('surface_placement', {}).get('version') != PLACEMENT_VERSION:
            raise ValueError('This package needs KCD2 surface placement. Resume with the current setup.')
        from setup_crime_services import VERSION as CRIME_VERSION
        crime = receipt.get('crime_services', {})
        if crime.get('version') != CRIME_VERSION or crime.get('status') != 'bound_native_services':
            raise ValueError('This package needs local crime and punishment services. Resume with the current setup.')
    if report['shader_validation'].get('installation') != 'mod_private_root':
        raise ValueError('This package has no mod-contained shader launcher. Rebuild with the current setup.')
    from setup_gameplay import STAGE_IDS
    completed = [step.get('id') for step in report.get('steps', []) if step.get('id')]
    if completed != list(STAGE_IDS):
        raise ValueError('This package is missing ordered campaign recipe stages. Rebuild with the current setup.')
    return report


def reviewed_blend_mesh(name):
    return re.sub(r'_lod[1-6](?=\.cgf$)', '', (name or '').replace('\\', '/').lower()) in REVIEWED_BLEND_MODELS


def apply_overlay(package, overlay, *, consume=False):
    """Replace files, never write through links to an older build."""
    package, overlay = Path(package).resolve(), Path(overlay).resolve()
    files = sorted(p for p in overlay.rglob('*') if p.is_file())
    if not files:
        raise ValueError('Builder produced no package: ' + str(overlay))
    changed = {}
    for source in files:
        relative = source.relative_to(overlay)
        destination = (package / relative).resolve()
        if source.is_symlink() or not destination.is_relative_to(package):
            raise ValueError('Unsafe builder output: ' + str(relative))
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(destination.name + '.recipe-pending')
        expected = digest(source)
        if consume:
            # Builders and the assembled package share the configured workspace.
            # Move completed archives instead of retaining another full copy at
            # every recipe step. Reports and source conversion inputs remain.
            source.replace(pending)
        else:
            shutil.copy2(source, pending)
        if digest(pending) != expected:
            raise ValueError('Builder output copy failed: ' + str(relative))
        pending.replace(destination)
        changed[relative.as_posix()] = expected
    return changed


def build_containers(source, target, package, output):
    """Connect the existing loot importer and full-world container builder."""
    from container_audit import audit
    from container_items import build as items
    from build_world_containers import build as containers
    from build_horse_trader_test import rewrite
    import zipfile
    from upgrade_map import read

    output.mkdir(parents=True)
    inventory = audit(source, target)
    identifiers = {item for group in inventory['inventories'].values()
                   for item in group.get('item_dependencies', [])}
    items(source, target, package, output/'items', identifiers)
    staged = output/'containers'
    containers(source, target, package/'Data/Levels/kcd1_travel/level.pak', output/'items', staged)
    overlay = output/'package';overlay.mkdir()
    (staged/'Data').rename(overlay/'Data')
    # The container builder's localization PAK contains an additional table.
    # Merge that table; never replace the menu, travel, or smithing strings.
    with zipfile.ZipFile(staged/'Localization/English_xml.pak') as archive:
        strings = {n:read(archive,n) for n in archive.namelist()}
    rewrite(package/'Localization/English_xml.pak', overlay/'Localization/English_xml.pak', strings)


def run(source, target, package, work, notify=lambda *a: None, cancelled=lambda: False, *, converted_data=None,
        shaders=None):
    """Apply the gameplay recipe; `shaders` is a setup_shaders.BackgroundBuild already compiling."""
    from game_paths import GameLibrary
    from build_open_test_gates import build as gates
    from build_horse_trader_test import build as horses
    from build_debug_supplies import build as supplies
    from build_rattay_smithery import build as smithery
    from build_smithing_voice import build as voice
    from build_church_prayer import build as prayer
    from build_workshop_test import build as workshop
    from campaign_menu_stage import add_to_package

    source, target, package, work = map(lambda p: Path(p).resolve(), (source, target, package, work))
    receipt = verify_package(package, hashes=False)
    if receipt.get('build_recipe'):
        raise ValueError('Recipe already applied; start a new clean build or install this package.')
    if package.is_relative_to(source) or package.is_relative_to(target):
        raise ValueError('Build recipe requires a private package outside the game installations.')
    work.mkdir(parents=True, exist_ok=False)
    paths = work / 'game-paths.json'
    paths.write_text(json.dumps(dict(schema=1, kcd1=str(source), kcd2=str(target), build=str(work/'conversion'))))
    library = GameLibrary(paths)
    steps = (
        (STAGE_LABELS[0], lambda out: gates(package, out, source=source), '.'),
        (STAGE_LABELS[1], lambda out: build_containers(source, target, package, out), 'package'),
        (STAGE_LABELS[2], lambda out: horses(source, target, package, out), 'package'),
        (STAGE_LABELS[3], lambda out: supplies(source, target, package, out), 'package'),
        (STAGE_LABELS[4], lambda out: smithery(library, package, out), 'package'),
        (STAGE_LABELS[5], lambda out: voice(target, package, out), 'package'),
        (STAGE_LABELS[6], lambda out: prayer(library, package, out, game=target), 'package'),
        (STAGE_LABELS[7], lambda out: workshop(target, package, out), 'package'),
    )
    from setup_gameplay import steps as gameplay_steps, validate as validate_gameplay
    from setup_streaming import validate as validate_streaming
    from setup_shaders import build as build_shaders, tool_root, stage_for_mod
    tool_root(target)  # Fail before modifying the package if native tools are absent.
    campaign_steps = gameplay_steps(source, target, package, work)
    total = len(steps) + len(campaign_steps) + 2
    report = dict(version=VERSION, status='building', steps=[], runtime_verified=False)
    if cancelled():
        raise InterruptedError('Build cancelled before gameplay recipe')
    # Mark the private package before changing it: a failed/interrupted recipe
    # must not be applied again on top of its own partially modified output.
    receipt['build_recipe'] = report
    pending = package / (RECEIPT+'.pending')
    pending.write_text(json.dumps(receipt, indent=2))
    pending.replace(package/RECEIPT)
    for index, (label, build, folder) in enumerate(steps):
        if cancelled():
            raise InterruptedError('Build cancelled before ' + label)
        notify(label, index, total, '')
        output = work / f'{index+1:02d}'
        build(output)
        overlay = output / folder
        # Gate builder also writes its evidence next to Data, not into the mod.
        if folder == '.':
            staged = output / 'package'
            staged.mkdir()
            (output/'Data').rename(staged/'Data')
            overlay = staged
        changed = apply_overlay(package, overlay, consume=True)
        receipt['files'].update(changed)
        report['steps'].append(dict(name=label, files=changed))
        pending = package / (RECEIPT+'.pending')
        pending.write_text(json.dumps(receipt, indent=2))
        pending.replace(package/RECEIPT)
        if shaders is not None: shaders.check()
    for index, (identity, label, build) in enumerate(campaign_steps, len(steps)):
        if cancelled(): raise InterruptedError('Build cancelled before ' + label)
        notify(label, index, total, '')
        output = work / identity
        build(output)
        changed = apply_overlay(package, output/'package', consume=True)
        receipt['files'].update(changed)
        report['steps'].append(dict(id=identity, name=label, files=changed))
        pending = package / (RECEIPT+'.pending')
        pending.write_text(json.dumps(receipt, indent=2))
        pending.replace(package/RECEIPT)
        if shaders is not None: shaders.check()
    report['content_validation'] = validate_gameplay(package)
    from hlod_spatial_metadata import upgrade_package as upgrade_spatial_metadata
    spatial_files, spatial_report = upgrade_spatial_metadata(package)
    receipt['files'].update(spatial_files)
    receipt['vegetation_spatial_metadata'] = spatial_report
    from lod_ratio_metadata import upgrade_package as upgrade_lod_ratios
    ratio_files, ratio_report = upgrade_lod_ratios(package)
    receipt['files'].update(ratio_files)
    receipt['lod_ratio_metadata'] = ratio_report
    report['streaming_validation'] = validate_streaming(package, converted_data=converted_data)
    if cancelled(): raise InterruptedError('Build cancelled before shader compilation')
    notify('Compile native retail shader archives', total-2, total, '')
    if shaders is None:
        build_shaders(target, work/'shaders', notify, cancelled)
        compiled = work/'shaders/package'
    else:
        shaders.wait(notify, cancelled)
        compiled = shaders.output/'package'
    report['shader_validation'], changed = stage_for_mod(compiled, package, consume=True)
    receipt['files'].update(changed)
    report['steps'].append(dict(name='Compile and package mod-contained shaders', files=changed))
    pending.write_text(json.dumps(receipt, indent=2))
    pending.replace(package/RECEIPT)
    if cancelled():
        raise InterruptedError('Build cancelled before menu registration')
    notify(STAGE_LABELS[8], total-1, total, '')
    add_to_package(target, package)
    report['status'] = 'complete'
    report['steps'].append(dict(name='KCD1 New Game menu'))
    receipt = verify_package(package, hashes=False)
    receipt['build_recipe'] = report
    pending = package / (RECEIPT+'.pending')
    pending.write_text(json.dumps(receipt, indent=2))
    pending.replace(package/RECEIPT)
    (work/'report.json').write_text(json.dumps(report, indent=2))
    if receipt.get('travel_bridge'):
        from setup_package_upgrade import finalize
        finalize(package, source=source, target=target, notify=notify, cancelled=cancelled)
    notify('Gameplay recipe complete', total, total, '')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'target', 'package', 'work'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--converted-data', type=Path)
    run(**vars(parser.parse_args()))
