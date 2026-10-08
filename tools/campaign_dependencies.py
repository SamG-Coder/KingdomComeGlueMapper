"""Import missing quest dependencies before resolving native registrations.

Every missing identity goes through an importer. The registry contains output
identities only after conversion succeeds; reading a source row is not enough.
Children are imported recursively and all request sites survive deduplication.
"""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass
class ImportPlan:
    dependencies: list = field(default_factory=list)
    source: dict = field(default_factory=dict)


class DependencyImporter:
    def __init__(self, adapters, registered=None):
        self.adapters = adapters
        self.registered = {}
        for group, values in (registered or {}).items():
            pairs = values.items() if isinstance(values, dict) else ((v, v) for v in values)
            self.registered[group] = {}
            for source, target in pairs:
                if not isinstance(source, str) or not source.strip() or not target:
                    raise ValueError('Invalid existing dependency registration')
                key = source.lower()
                if key in self.registered[group] and self.registered[group][key] != target:
                    raise ValueError('Ambiguous registered dependency: ' + group + ':' + source)
                self.registered[group][key] = target
        self.jobs = {}
        self.files = {}

    def ensure(self, group, identity, site=None):
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('A nonempty source dependency identity is required')
        identity = identity.lower()
        registry = self.registered.setdefault(group, {})
        key = (group, identity)
        if key in self.jobs:
            record = self.jobs[key]
            if site is not None:
                record['requested_by'].append(site)
            if record['status'] == 'importing':
                record['cycle_detected'] = True
            return record.get('target')
        matches = [value for source, value in registry.items() if source.lower() == identity]
        if len(matches) > 1:
            raise ValueError('Ambiguous registered dependency: ' + group + ':' + identity)
        if matches:
            return matches[0]
        record = dict(group=group, identity=identity, requested_by=[site] if site is not None else [],
                      status='importing', dependencies=[])
        self.jobs[key] = record
        try:
            adapter = self.adapters.get(group)
            if adapter is None:
                raise ValueError('Dependency conversion adapter is not implemented: ' + group)
            plan = adapter.plan(identity)
            record['source'] = plan.source
            failed = []
            for child_group, child_id in plan.dependencies:
                target = self.ensure(child_group, child_id, dict(parent_group=group, parent=identity))
                record['dependencies'].append(dict(group=child_group, source=child_id, target=target))
                if target is None:
                    failed.append(child_group + ':' + child_id)
            if failed:
                raise ValueError('Dependency imports did not complete: ' + ', '.join(failed))
            target, files, evidence = adapter.convert(identity, plan, self.registered)
            if not target:
                raise ValueError('Importer did not produce a registration identity')
            for name, data in files.items():
                path = PurePosixPath(name.replace('\\', '/'))
                if path.is_absolute() or '..' in path.parts or ':' in name or not name:
                    raise ValueError('Dependency output escapes the conversion package: ' + name)
                if name in self.files and self.files[name] != data:
                    raise ValueError('Conflicting dependency output: ' + name)
            self.files.update(files)
            registry[identity] = target
            record.update(status='imported', target=target, evidence=evidence, files=sorted(files))
            return target
        except (ValueError, KeyError, FileNotFoundError) as error:
            record.update(status='conversion_failed', error=str(error))
            return None

    def report(self):
        return dict(schema=1, jobs=list(self.jobs.values()), registered=self.registered,
                    counts=dict(Counter(r['status'] for r in self.jobs.values())),
                    runtime_validated=False)


def import_binding_dependencies(plans, importer):
    """Request every source binding; missing registrations cause conversion."""
    for plan in plans.values():
        for binding in plan['bindings']:
            reg = binding['registration']
            site = dict(quest=plan['quest'], asset=binding['name'], source=binding['sites'])
            if reg['kind'] in ('entity', 'placed_soul'):
                importer.ensure('entities', reg['guid'], site)
            if reg['kind'] == 'placed_soul':
                importer.ensure('souls', reg['shared_soul_guid'], site)
            elif reg['kind'] != 'entity':
                importer.ensure(reg['kind'] + 's', reg['guid'], site)
    return importer.registered
