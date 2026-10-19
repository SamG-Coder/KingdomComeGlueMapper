"""Export an authored KCD2 scene with Warhorse's own HLOD generator.

This is a hierarchy-generation stage, not a complete proxy-geometry baker.
No KCD1 hierarchy or previously compiled hlods.dat is accepted as source.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import Counter


def inspect_tree(path):
    root = ET.parse(path).getroot()
    if root.tag != 'HLodTree':
        raise ValueError('Expected native authored HLodTree, not compiled HLod data')
    kinds = {}
    proxies = set()
    placements = 0
    for node in root.iter():
        kinds[node.tag] = kinds.get(node.tag, 0) + 1
        if node is root:
            continue
        # Native XML omits identity transforms (including position 0,0,0).
        pos = [float(x) for x in node.get('Pos', '0,0,0').split(',')]
        if len(pos) != 3 or not all(map(math.isfinite, pos)):
            raise ValueError('Invalid native placement position: ' + node.get('Name', ''))
        if 'Radius' in node.attrib:
            radius = float(node.get('Radius'))
            if not math.isfinite(radius) or radius < 0:
                raise ValueError('Invalid native HLOD radius')
        if node.tag.endswith('Lod'):
            geometry = node.get('Geometry', '')
            if geometry:
                proxies.add(geometry.replace('\\', '/').lower())
        else:
            placements += 1
    if len(list(root)) == 0:
        raise ValueError('Native generator emitted an empty hierarchy')
    return dict(settings=dict(root.attrib), node_types=kinds,
                explicit_placements=placements, proxy_meshes=sorted(proxies),
                runtime_verified=False, package_ready=False)


def verify_placements(level, tree):
    """Reject successful exports that silently dropped or duplicated brushes."""
    expected = {}
    for path in (Path(level) / 'Layers').rglob('*.lyr'):
        for obj in ET.parse(path).iter('Object'):
            if obj.get('Type') != 'Brush':
                raise ValueError('Unsupported authored object in placement-only scene')
            key = obj.get('Id', '').lower().strip('{}')
            if not key or key in expected:
                raise ValueError('Duplicate or missing authored placement identity')
            expected[key] = obj
    actual = list(ET.parse(tree).iter('Brush'))
    counts = Counter(obj.get('Id', '').lower().strip('{}') for obj in actual)
    if counts != Counter({key: 1 for key in expected}):
        missing = len(set(expected) - set(counts))
        raise ValueError(f'Native export changed brush membership ({missing} missing placements)')
    for obj in actual:
        source = expected[obj.get('Id').lower().strip('{}')]
        if obj.get('Geometry', '').replace('\\', '/').lower() != source.get('Prefab', '').replace('\\', '/').lower():
            raise ValueError('Native export changed a brush geometry binding')
        before = tuple(map(float, source.get('Pos').split(',')))
        after = tuple(map(float, obj.get('Pos', '0,0,0').split(',')))
        if len(after) != 3 or math.dist(before, after) > .01:
            raise ValueError('Native export moved a brush placement')
    return {'brushes_verified': len(actual), 'unique_placement_ids': len(counts)}


def export_tree(editor_root, level_name, profile, evidence):
    """Run the official exporter on an existing authored scene, in place.

    Evidence remains separate from the scene. The caller must supply its own
    build workspace; installed Mods and junction-backed scenes are rejected.
    The exporter owns hlodtree.xml and can replace it on each invocation.
    """
    if not level_name.replace('_', '').isalnum() or not profile.replace('_', '').isalnum():
        raise ValueError('Invalid level or export profile name')
    editor_root = Path(editor_root).resolve()
    level = editor_root / 'Data/Levels' / level_name
    if level.resolve() != level.absolute() or 'mods' in [x.lower() for x in level.resolve().parts]:
        raise ValueError('Use an authored build workspace, not a mounted installation')
    required = [level / (level_name + '.cry'), level / 'level.editor_xml',
                level / 'layerexportprofiles' / (profile + '.xml')]
    if not (level / 'Layers').is_dir():
        raise ValueError('Missing native editor layers; compiled level.pak is not an authored scene')
    for path in required:
        if not path.is_file():
            raise ValueError('Missing native generator input: ' + str(path))
    exe = editor_root / 'Bin/Win64ReleaseSteamLTO_DLL/Editor.exe'
    if not exe.is_file():
        raise ValueError('Official KCD2 Modding Tools editor is required')
    running = subprocess.run(['powershell', '-NoProfile', '-Command',
        'if (Get-Process Editor -ErrorAction SilentlyContinue) { exit 1 }'], capture_output=True)
    if running.returncode:
        raise RuntimeError('An editor is already running')
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    target = level / 'hlodtree.xml'
    started = time.time_ns()
    args = [str(exe), '-layerExportProfile', profile, '-exportHLodTree',
            str(required[0]), '-unattended', '-ignoreAssert', '-TraceNoServer', '-noSourceControl']
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    result = subprocess.run(args, cwd=editor_root, startupinfo=startup)
    log = (editor_root / 'editor.log').read_bytes()
    (evidence / 'native-hlod-export.log').write_bytes(log)
    if result.returncode:
        raise RuntimeError(f'Native HLOD export failed ({result.returncode}); see captured editor log')
    if not target.exists() or target.stat().st_mtime_ns < started:
        raise RuntimeError('Native export did not produce a fresh hlodtree.xml')
    if b'HLodTree generation error' in log or b'Failed to serialize HLod tree' in log:
        raise RuntimeError('Native HLOD generator reported a failure')
    report = inspect_tree(target)
    if (level / 'native-placement-inputs.json').exists():
        report.update(verify_placements(level, target))
    # An XML filename is not evidence that its geometry was generated.
    missing = [path for path in report['proxy_meshes'] if not (editor_root / 'Data' / path).is_file()]
    report['missing_proxy_meshes'] = missing
    report['proxy_geometry_complete'] = not missing
    report.update(generator=str(exe), source_level=str(level), command=args,
                  hierarchy_exported=True,
                  remaining_stages=['generate and validate proxy meshes/materials/textures',
                                    'native compiled level export', 'resource packaging',
                                    'retail approach and retreat test'])
    (evidence / 'native-hlod-export.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--editor-root', type=Path, required=True)
    p.add_argument('--level', required=True)
    p.add_argument('--profile', default='game')
    p.add_argument('--evidence', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(export_tree(a.editor_root, a.level, a.profile, a.evidence), indent=2))
