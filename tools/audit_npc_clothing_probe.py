"""Read the live KCD2 clothing manager and compare it with a probe manifest.

Registration is distinct from visual correctness. This check does not certify
mesh partitions, garment morphs, materials, or animation compatibility.
"""
import argparse
import http.client
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, default=Path('D:/SteamLibrary/steamapps/common/KCD2Mod'))
    parser.add_argument('--namespace', default='npc_layers_v1')
    parser.add_argument('--person', action='store_true', help='Audit the registered source NPC instead of the visual probe')
    parser.add_argument('--output', type=Path, default=Path('reports/npc-clothing-live-audit.json'))
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.namespace):
        parser.error('Invalid namespace')
    asset_root = args.tools/'Data/objects/characters/gluenpc'/args.namespace
    manifest = json.loads((asset_root/'clothing-report.json').read_text(encoding='utf-8'))
    name = manifest['soul_name'] if args.person else 'GlueNPC_'+args.namespace
    if not re.fullmatch(r'[A-Za-z0-9_]+', name):
        parser.error('Invalid entity name')
    connection = http.client.HTTPConnection('127.0.0.1', 1403, timeout=10)
    try:
        connection.request('GET', '/api/ent/ClothingSystem/AttachmentManagersByName/'
                           + name + '?depth=4')
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f'Clothing manager request failed: HTTP {response.status}')
        raw = response.read()
    finally:
        connection.close()
    root = ET.fromstring(raw)
    if root.tag != 'ComponentAttachmentManager':
        raise RuntimeError('The probe has no native clothing manager')
    slots = root.find('SlotCache')
    components = {n.get('Name') for n in slots.iter('Value')} if slots is not None else set()
    checks = [dict(source=c['source'], component=c['component'],
                   registered_in_live_slots=c['component'] in components)
              for c in manifest['clothing']]
    attachments = [dict(name=n.get('Key'), state=n.find('Value').attrib,
                        elements=[e.attrib for e in n.findall('.//Element')])
                   for n in root.findall('AttachmentsByName/Pair')]
    result = dict(namespace=args.namespace, owner=root.get('OwnerName'),
                  garments=checks, attachments=attachments,
                  registration_passed=bool(checks) and all(c['registered_in_live_slots'] for c in checks),
                  visual_conversion_verified=False,
                  remaining=['Authored region boundaries and hiding masks',
                             'Independent equipment changes and per-item condition',
                             'Native blood, grime and wear material conversion',
                             'Animation coverage beyond the idle test'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    args.output.with_suffix('.xml').write_bytes(raw)
    print(json.dumps(result, indent=2))
    if not result['registration_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
