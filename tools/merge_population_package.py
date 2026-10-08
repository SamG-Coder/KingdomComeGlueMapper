"""Combine staged character packages, rejecting conflicting definitions/assets.

This assembles local output only; it does not install or mark a campaign ready.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET


IDENTITY_FIELDS = ('Name', 'name', 'Id', 'id', 'soul_id', 'clothing_preset_id', 'path')


def signature(node):
    return (node.tag, sorted(node.attrib.items()), (node.text or '').strip(),
            [signature(c) for c in node])


def identity(node):
    for field in IDENTITY_FIELDS:
        if field in node.attrib: return node.tag, field, node.get(field)
    return node.tag, None, None


def merge_xml(left, right, location='root'):
    if signature(left) == signature(right): return copy.deepcopy(left)
    if left.tag != right.tag or left.attrib != right.attrib or (left.text or '').strip() != (right.text or '').strip():
        raise ValueError('Conflicting XML definition at ' + location)
    result = copy.deepcopy(left)
    def indexed(children):
        lookup = {}
        for child in children:
            key = identity(child)
            if key in lookup: raise ValueError('Ambiguous XML identity at ' + location + ': ' + str(key))
            lookup[key] = child
        return lookup
    existing = indexed(result)
    incoming = indexed(right)
    for key, child in incoming.items():
        if key not in existing:
            result.append(copy.deepcopy(child)); continue
        old = existing[key]
        # Records must agree in full. Only shared container/task structures may
        # accumulate children; a garment with the same name must not be spliced.
        if key[1] is not None and child.tag != 'task':
            if signature(old) != signature(child):
                raise ValueError('Conflicting XML record at ' + location + ': ' + str(key))
        else:
            merged = merge_xml(old, child, location + '/' + str(key))
            position = list(result).index(old)
            result.remove(old); result.insert(position, merged)
    return result


def assemble(sources, output):
    if output.exists(): raise FileExistsError('Choose a new output directory')
    files = {}; overlays = set(); people = []
    for source in sources:
        reports = list(source.glob('objects/characters/gluenpc/*/clothing-report.json'))
        if not reports: raise ValueError('No staged registration reports in ' + str(source))
        for path in reports:
            report = json.loads(path.read_text(encoding='utf-8'))
            for relative in report['overlay_files']:
                overlay = (source / relative).resolve()
                if not overlay.is_relative_to(source.resolve()) or not overlay.is_file():
                    raise ValueError('Missing or invalid staged overlay: ' + relative)
            people.append(dict(namespace=report['namespace'], soul_id=report['soul_id'],
                               source_person_registered=report['source_person_registered']))
            overlays.update(p.replace('\\', '/').lower() for p in report['overlay_files'])
        for path in source.rglob('*'):
            if path.is_file():
                relative = path.relative_to(source).as_posix()
                files.setdefault(relative.lower(), []).append((relative, path))
    namespaces = [p['namespace'] for p in people]
    if len(namespaces) != len(set(namespaces)): raise ValueError('Duplicate character namespace')
    soul_ids = [p['soul_id'] for p in people]
    if len(soul_ids) != len(set(soul_ids)): raise ValueError('Duplicate character soul identity')
    prepared = {}; copies = {}
    # Validate all collisions before publishing any files.
    for key, candidates in files.items():
        if key in overlays:
            merged = ET.parse(candidates[0][1]).getroot()
            for _, path in candidates[1:]: merged = merge_xml(merged, ET.parse(path).getroot(), key)
            prepared[candidates[0][0]] = ET.tostring(merged, encoding='utf-8', xml_declaration=True)
        else:
            hashes = set()
            for _, path in candidates:
                with path.open('rb') as stream:
                    hashes.add(hashlib.file_digest(stream, 'sha256').hexdigest())
            if len(hashes) != 1: raise ValueError('Conflicting asset: ' + key)
            copies[candidates[0][0]] = candidates[0][1]
    output.mkdir(parents=True)
    for relative, payload in prepared.items():
        target = output/relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(payload)
    for relative, source in copies.items():
        target = output/relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    report = dict(characters=people, files=len(files), overlays=len(prepared), installed=False,
                  campaign_ready=False)
    (output/'population-package.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(assemble(a.source, a.output)))
