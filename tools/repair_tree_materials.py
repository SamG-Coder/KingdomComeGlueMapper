"""Apply the tree-shadow material conversion to already generated loose assets."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from upgrade_map import xml
from vegetation_materials import convert_shadow_proxies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(r'D:\SteamLibrary\steamapps\common\KCD2Mod\Data'))
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    changes = []
    for namespace in ('glueveg', 'gluebuild', 'glueitems'):
        for path in sorted((args.data / namespace).rglob('*.mtl')):
            original = path.read_bytes()
            doc = ET.fromstring(original)
            count = convert_shadow_proxies(doc)
            if count:
                relative = path.relative_to(args.data)
                changes.append({'path': relative.as_posix(), 'shadow_proxies': count})
                if args.apply:
                    backup = args.backup / relative
                    if backup.exists() and backup.read_bytes() != original:
                        raise ValueError(f'Conflicting backup: {backup}')
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(original)
                    path.write_bytes(xml(doc))
    args.backup.mkdir(parents=True, exist_ok=True)
    (args.backup / 'report.json').write_text(json.dumps({'applied': args.apply, 'changes': changes}, indent=2))
    print(f'{"Updated" if args.apply else "Would update"} {len(changes)} materials; report: {args.backup / "report.json"}')


if __name__ == '__main__':
    main()
