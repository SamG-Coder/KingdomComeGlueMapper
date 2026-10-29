"""Measure fresh Setup character conversion and packing, without a saved job.

Uses the same converter and ZIP32 writer as the initial people import. Each
actor starts with an empty directory; only normal within-build caches survive
between actors. No installed mod or source assets are modified.
"""
import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET


def run(args):
    # Older checkout baselines predate the scoped source-input cache.
    from contextlib import nullcontext
    try:
        from character_build_cache import appearance_batch
    except ModuleNotFoundError:
        appearance_batch = nullcontext
    with appearance_batch():
        _run(args)


def _run(args):
    from character_person import resolve_person
    from character_person_appearance import build_appearance
    from build_quest_people_test import write_actor_paks
    from bake_pool import release
    from static_assets import release_library_index
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(scope='fresh appearance conversion and Setup packing; excludes weapons, world and lighting',
                  people=[], seconds=0)
    profile = cProfile.Profile() if args.profile else None
    start = time.perf_counter()
    try:
        for name in args.person:
            started = time.perf_counter()
            person = resolve_person(args.source, name, include_ai=False)
            resolved = time.perf_counter()
            namespace = 'gm_person_' + person['soul']['soul_id'].replace('-', '')
            with tempfile.TemporaryDirectory(prefix='actor-', dir=args.output) as directory:
                root = Path(directory)
                appearance, data = root/'appearance', root/'Data'
                data.mkdir()
                if profile: profile.enable()
                result = build_appearance(args.source, args.target, person, namespace, appearance)
                converted = time.perf_counter()
                paks, libs = write_actor_paks(appearance, data, namespace)
                packed = time.perf_counter()
                if profile: profile.disable()
                manifest = {k: hashlib.sha256(v).hexdigest() for k, v in libs.items()}
                materials = {}
                size = 0
                for pak in paks:
                    size += pak.stat().st_size
                    with zipfile.ZipFile(pak) as archive:
                        for entry in archive.infolist():
                            blob = archive.read(entry)
                            manifest[entry.filename] = hashlib.sha256(blob).hexdigest()
                            if entry.filename.endswith('.mtl'):
                                materials[entry.filename] = blob
                surfaces = {}
                for path, blob in materials.items():
                    material = ET.fromstring(blob)
                    for texture in material.iter('Texture'):
                        filename = texture.get('File', '').replace('\\', '/').lower()
                        family = {p[len(filename):]: digest for p, digest in manifest.items()
                                  if p == filename or p.startswith(filename+'.')}
                        if family:
                            texture.set('File', json.dumps(family, sort_keys=True))
                    surfaces[path] = hashlib.sha256(ET.tostring(material)).hexdigest()
                (args.output/(name+'-surfaces.json')).write_text(json.dumps(surfaces, indent=2))
                (args.output/(name+'-manifest.json')).write_text(json.dumps(manifest, indent=2))
                item = dict(person=name, resolve_seconds=resolved-started,
                            convert_seconds=converted-resolved, pack_seconds=packed-converted,
                            build_seconds=packed-started, packed_bytes=size,
                            timings=result.get('timings_seconds'), bakes=result.get('texture_bakes'))
                report['people'].append(item)
                print(json.dumps(item), flush=True)
            report['seconds'] = time.perf_counter()-start
            (args.output/'report.json').write_text(json.dumps(report, indent=2))
    finally:
        if profile:
            profile.disable()
            profile.dump_stats(str(args.output/'profile.pstats'))
            with (args.output/'profile.txt').open('w') as stream:
                pstats.Stats(profile, stream=stream).sort_stats('cumulative').print_stats(90)
        release(); release_library_index()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'target', 'output'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--person', action='append', required=True)
    parser.add_argument('--profile', action='store_true')
    run(parser.parse_args())
