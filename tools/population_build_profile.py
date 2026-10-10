"""Select the original service NPCs for a controlled population build test.

TravelBase defines the retained actors and their dependency registrations. This
does not change the population converter or its cache. Current scenery, scripts,
travel dialogue, localization and level assets are retained for the comparison.
"""
import copy
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

from region_travel_policy import update_resource
from retail_pak import PakSet, RetailPakWriter
from upgrade_map import read, xml


PROFILE = 'services-only'
LEVEL = 'Data/Levels/kcd1_travel/level.pak'
SCHEDULER = 'mods/gluemappertravel/data/levels/kcd1_travel/tables/ai/scheduler.xml'
REGISTRATIONS = {
    'objects_mission0.xml', 'whdata_0', 'whdata_1', 'tables/ai/scheduler.xml',
    'triggerareas.fubar', 'waitinglinks.xml', 'extractedlayerentityids.xml',
}
REPORTS = {'population-package.json', 'population-validation.json',
           'registered-profiles.json', 'shared-materials.json'}


def key(name):
    return name.replace('\\', '/').lower()


def is_layer(name):
    return key(name).startswith('layers/') and key(name).endswith('.xml')


def is_aggregate(name):
    name = key(name)
    return name.startswith('libs/tables/') or name in ('libs/storm/storm.xml', SCHEDULER)


def npc_bindings(archive):
    documents = ['objects_mission0.xml'] + [n for n in archive.namelist() if is_layer(n)]
    return sorted((e.get('Name'), e.get('EntityGuid'), e.get('EntityClass'))
                  for n in documents for e in ET.fromstring(read(archive, n)).iter('Entity')
                  if e.get('EntityClass') in ('NPC', 'NPC_Female'))


def assemble(base, current, output, *, target):
    base, current, output = map(Path, (base, current, output))
    if output.exists():
        raise FileExistsError('Use a fresh package directory for the population test profile')
    with zipfile.ZipFile(base / LEVEL) as b, zipfile.ZipFile(current / LEVEL) as c:
        retained = npc_bindings(b)
        if not retained:
            raise ValueError('TravelBase has no service NPCs to preserve')
        before = npc_bindings(c)
        if not set(retained).issubset(before):
            raise ValueError('Current service NPC identities differ from TravelBase')
    output.mkdir(parents=True)
    # Copy current level assets and non-population files, not the large population
    # archives. No cache files or current files are modified or removed here.
    for path in current.rglob('*'):
        if not path.is_file(): continue
        relative = path.relative_to(current)
        if (relative.as_posix() == LEVEL or relative.as_posix() in REPORTS
                or relative.parent.as_posix().lower() == 'data' and path.suffix.lower() == '.pak'):
            continue
        dest = output / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    (output / 'Data').mkdir(exist_ok=True)
    with PakSet(base / 'Data') as b, PakSet(current / 'Data') as c, \
            RetailPakWriter(output / 'Data', 'gluemappertravel') as dst:
        current_names = {key(n): n for n in c.namelist()}
        for name in b.namelist():
            if key(name) not in current_names:
                raise ValueError('Missing original service resource: ' + name)
            # Only the dependency aggregates return to the base membership. In
            # particular Player.lua and native AI trees keep their current code.
            payload = b.read(name) if is_aggregate(name) else c.read(current_names[key(name)])
            dst.writestr(name, update_resource(name, payload))
    destination = output / LEVEL
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(base / LEVEL) as b, zipfile.ZipFile(current / LEVEL) as c, \
            zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED, compresslevel=1) as dst:
        baseline = {key(n): n for n in b.namelist()}
        emitted = set()
        for info in c.infolist():
            name = key(info.filename)
            if name in REGISTRATIONS or is_layer(name):
                if name not in baseline: continue
                payload = read(b, baseline[name])
            elif name == 'leveldata.xml':
                # Keep newer level/scenery settings; remove only population layers.
                doc, original = ET.fromstring(read(c, info.filename)), ET.fromstring(read(b, baseline[name]))
                for layers in list(doc.findall('Layers')): doc.remove(layers)
                if original.find('Layers') is not None:
                    doc.append(copy.deepcopy(original.find('Layers')))
                payload = xml(doc)
            else:
                payload = read(c, info.filename)
            dst.writestr(info, payload)
            emitted.add(name)
        for name in baseline.keys() - emitted:
            if name in REGISTRATIONS or is_layer(name):
                dst.writestr(baseline[name], read(b, baseline[name]))
    result = dict(population_profile=PROFILE, base_package=str(base.resolve()), target=str(target),
                  converted_residents=0, converted_conditionals=0, placements=[], selected_placements=[],
                  enabled_npcs=[n[0] for n in retained], disabled_placements=len(before) - len(retained),
                  population_cache_preserved=True, current_scenery_preserved=True,
                  installed=False, runtime_verified=False)
    (output / 'population-package.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def validate(package, base):
    """Check both visible entities AND global registrations/assets were excluded."""
    package, base = Path(package), Path(base)
    with PakSet(base / 'Data') as b, PakSet(package / 'Data') as p:
        actual = {key(n): n for n in p.namelist()}
        if set(actual) != {key(n) for n in b.namelist()}:
            raise ValueError('Service profile contains extra or missing population resources')
        for name in b.namelist():
            if is_aggregate(name) and p.read(actual[key(name)]) != update_resource(name, b.read(name)):
                raise ValueError('Service profile has changed dependency membership: ' + name)
    with zipfile.ZipFile(base / LEVEL) as b, zipfile.ZipFile(package / LEVEL) as p:
        retained = npc_bindings(b)
        if npc_bindings(p) != retained:
            raise ValueError('Service profile registers extra or missing NPC entities')
        original, actual = {key(n): n for n in b.namelist()}, {key(n): n for n in p.namelist()}
        owned = REGISTRATIONS | {n for n in original.keys() | actual.keys() if is_layer(n)}
        for name in owned:
            if (name in original) != (name in actual):
                raise ValueError('Service profile has extra or missing world registration: ' + name)
            if name in original and read(b, original[name]) != read(p, actual[name]):
                raise ValueError('Service profile has changed world registration: ' + name)
        def layers(z):
            return [ET.tostring(e) for e in ET.fromstring(read(z, 'leveldata.xml')).findall('Layers/Layer')]
        if layers(b) != layers(p):
            raise ValueError('Service profile still enables population layers')
    return dict(population_profile=PROFILE, enabled_npc_count=len(retained),
                enabled_npcs=[n[0] for n in retained], population_registrations_disabled=True)
