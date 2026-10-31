"""Standalone developer CLI; never imported by consumer Setup."""
import argparse
import json
from pathlib import Path

from .report import write
from .runner import run_file
from .source import Source


def main():
    parser = argparse.ArgumentParser(description='Emulate converted quest/NPC logic without launching KCD2.')
    sub = parser.add_subparsers(dest='command', required=True)
    campaign_parser = sub.add_parser('campaign', help='Audit every source quest with per-quest checkpoints')
    campaign_parser.add_argument('--kcd1', type=Path, required=True)
    campaign_parser.add_argument('--first', nargs='+', default=[], help='Source quest names to audit first; all remaining records follow')
    campaign_parser.add_argument('--output', type=Path, required=True)
    campaign_parser.add_argument('--inventory-bindings', type=Path,
        help='JSON with explicit item_map, registered target item IDs and player_asset')
    campaign_parser.add_argument('--kcd2', type=Path, help='Read actual target item registrations')
    campaign_parser.add_argument('--mod', type=Path, help='Include item definitions from this installed mod')
    campaign_parser.add_argument('--player-asset', help='Player alias for target inventory tracking')
    run = sub.add_parser('run', help='Run JSON scenarios and write JSON/HTML/JUnit reports')
    run.add_argument('scenarios', type=Path, nargs='+')
    run.add_argument('--output', type=Path, required=True)
    inventory = sub.add_parser('inventory', help='List actual Skald definitions and operation coverage')
    inventory.add_argument('source', type=Path)
    inventory.add_argument('--output', type=Path, required=True)
    audit = sub.add_parser('audit', help='Probe every installed quest and compare with the source quest inventory')
    audit.add_argument('source', type=Path)
    audit.add_argument('--kcd1', type=Path, help='Optional retail KCD1 directory for the complete source quest checklist')
    audit.add_argument('--source-graphs', action='store_true', help='Also dry-run conversion and local probes for all source quest graphs')
    audit.add_argument('--output', type=Path, required=True)
    demo = sub.add_parser('demo', help='Generate runnable fixtures using the production converters')
    demo.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'campaign':
        from .campaign import campaign
        if args.inventory_bindings and any((args.kcd2, args.mod, args.player_asset)):
            parser.error('--inventory-bindings cannot be combined with --kcd2, --mod or --player-asset')
        if args.mod and not args.kcd2:
            parser.error('--mod requires --kcd2')
        if bool(args.kcd2) != bool(args.player_asset):
            parser.error('--kcd2 and --player-asset must be provided together')
        bindings = None
        if args.kcd2:
            from .item_bindings import installed
            bindings = installed(args.kcd2, args.mod, args.player_asset)
        elif args.inventory_bindings:
            bindings = json.loads(args.inventory_bindings.read_text(encoding='utf-8'))
        result = campaign(args.kcd1, args.output, args.first,
                          lambda done, total, name, status: print(f'{done}/{total} {name}: {status}', flush=True),
                          inventory=bindings)
        print(json.dumps(dict(statuses=result['statuses'], probes=result['probes']), indent=2))
        return int(any(q['status'] != 'LOCAL_STATE_PROBES_EXECUTED' for q in result['quests']))
    if args.command == 'audit':
        if args.source_graphs and not args.kcd1: parser.error('--source-graphs requires --kcd1')
        from .audit import run as run_audit
        report = run_audit(args.source, args.output, args.kcd1, args.source_graphs)
        print(json.dumps(dict(quests=len(report['quests']), source_records=len(report['source_inventory']),
                              probes=sum(len(q['probes']) for q in report['quests']),
                              structural_findings=len(report['structure']['findings']),
                              report=str((args.output/'audit.html').resolve())), indent=2))
        # An audit is diagnostic, never an implicit all-quests PASS.
        return 1 if (report['structure']['findings'] or report['invalid_xml']
                     or any(q['status'] != 'LOCAL_PROBES_EXECUTED' for q in report['quests'])
                     or any(s['status'] != 'EXECUTED' or s.get('unsupported') for s in report['new_game'])
                     or any(r['status'] != 'LOCAL_STATE_PROBES_EXECUTED' for r in report.get('source_graphs', {}).get('quests', []))
                     or any(r['status'] == 'NOT_PRESENT_IN_PACKAGE' for r in report['source_inventory'])) else 0
    if args.command == 'run':
        reports = [run_file(p) for p in args.scenarios]
        write(reports, args.output)
        print(json.dumps(dict(results=[dict(name=r['name'],status=r['status'],error=r.get('error')) for r in reports],
                              report=str((args.output/'report.html').resolve())), indent=2))
        return 0 if all(r['status']=='PASS' for r in reports) else 1
    if args.command == 'inventory':
        from .graph import Graph
        source = Source(args.source); report = source.inventory()
        for definition in report['definitions']:
            try:
                g = Graph(source, definition['name'], entry_file=definition['file'])
                definition.update(unsupported=g.unsupported, input_ports=g.input_types,
                                  event_ports=sorted(g.event_ports), outputs=sorted(g.output_ports))
            except ValueError as exc: definition['error'] = str(exc)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(dict(definitions=len(report['definitions']), report=str(args.output.resolve()))))
        return 0
    from .demo import generate
    paths = generate(args.output)
    print(json.dumps(dict(scenarios=[str(p.resolve()) for p in paths]), indent=2))
    return 0


if __name__ == '__main__': raise SystemExit(main())
