"""Report the structure of a compiled terrain/hlods.xml tree.

Read-only diagnostics for the distant-object conversion gap (#13, #29). Native
levels carry deep mixed trees with proxy references, while converted levels have
been observed with flat Cluster/Vegetation trees and no proxies. This tool turns
that manual comparison into a repeatable report and does not modify anything.

Unlike spatial_hlod.read_groups, the walker here does not reject existing
proxy links: native trees carry ProxyIndex values by design, and read_groups is
a conversion precondition, not an audit.
"""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from upgrade_map import read as read_entry


def structure(document):
    """Walk one hlods.xml document and return its structural statistics."""
    root = document if document.tag == 'HLod' else document.find('HLod')
    if root is None:
        raise ValueError('No HLod root in document')
    nodes = total_proxies = 0
    types = {}
    nearest = {}
    depths = {}
    hashes = 0
    radius = []
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        depths[depth] = depths.get(depth, 0) + 1
        kinds = node.get('Type', 'missing')
        types[kinds] = types.get(kinds, 0) + 1
        if node.get('ProxyIndex', '-1') != '-1':
            total_proxies += 1
        value = node.get('NearestObserverDistance')
        nearest[value] = nearest.get(value, 0) + 1
        if node.get('Hash') is not None:
            hashes += 1
        if node.get('Radius') is not None:
            try:
                radius.append(float(node.get('Radius')))
            except ValueError:
                pass
        stack.extend((child, depth + 1) for child in node)
    return {
        'nodes': nodes,
        'max_depth': max(depths) if depths else 0,
        'depth_distribution': depths,
        'node_types': types,
        'proxy_references': total_proxies,
        'nearest_observer_distances': nearest,
        'hash_attributes': hashes,
        'radius_min': min(radius) if radius else None,
        'radius_max': max(radius) if radius else None,
    }


def span_report(document, data):
    """Validate block extents without the conversion preconditions.

    Returns a dict with the number of valid and invalid nodes, so a native or
    partial archive can be inspected even when one block is malformed.
    """
    valid = invalid = 0
    for node in document.iter('HLod'):
        try:
            offset = int(node.get('DataOffset'))
            size = int(node.get('DataSize'))
        except (TypeError, ValueError):
            invalid += 1
            continue
        if offset < 0 or size < 0 or offset + size > len(data):
            invalid += 1
            continue
        valid += 1
    return {'valid_blocks': valid, 'invalid_blocks': invalid}


def audit(pak_path, xml_name='terrain/hlods.xml', dat_name='terrain/hlods.dat'):
    """Read a level archive and return the structural report."""
    with zipfile.ZipFile(pak_path) as pak:
        document = ET.fromstring(read_entry(pak, xml_name))
        report = structure(document)
        report['xml_entry'] = xml_name
        try:
            data = read_entry(pak, dat_name)
        except KeyError:
            report['spans'] = None
        else:
            report['spans'] = span_report(document, data)
    return report


def compare(first, second, second_path):
    """Diff two structural reports; positive deltas mean the second is larger."""
    comparable = ('nodes', 'max_depth', 'proxy_references')
    return {
        'pak': str(second_path),
        'deltas': {key: second[key] - first[key] for key in comparable},
        'first': {key: first[key] for key in comparable},
        'second': {key: second[key] for key in comparable},
        'node_types_first': first['node_types'],
        'node_types_second': second['node_types'],
        'nearest_observer_first': first['nearest_observer_distances'],
        'nearest_observer_second': second['nearest_observer_distances'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pak', type=Path, required=True, help='level archive containing the HLOD tables')
    parser.add_argument('--xml-name', default='terrain/hlods.xml', help='entry name of the HLOD document')
    parser.add_argument('--dat-name', default='terrain/hlods.dat', help='entry name of the HLOD payload')
    parser.add_argument('--compare', type=Path, help='optional second archive whose structure is diffed against the first')
    parser.add_argument('--output', type=Path, help='optional JSON report destination')
    args = parser.parse_args()
    report = audit(args.pak, args.xml_name, args.dat_name)
    report['pak'] = str(args.pak)
    if args.compare:
        report['compare'] = compare(report, audit(args.compare, args.xml_name, args.dat_name), args.compare)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(args.output)
    else:
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
