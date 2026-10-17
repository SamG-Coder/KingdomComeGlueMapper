"""Preserve validated source navigation under converted world/profile names.

KCD1 and installed KCD2 use matching Recast settings/tile version in this
contract. Geometry is retained byte-for-byte after index and extent checks;
only profile-qualified archive paths change. Runtime pathfinding still needs
an in-game acceptance test. Group-combination profiles require their complete
group conversion rather than a misleading stand-alone profile mesh.
"""
import struct

from campaign_dependencies import ImportPlan
from campaign_quest_graph import identifier
from region_travel_navigation import validate_mesh
from upgrade_map import read, digest_bytes as digest



class NavigationSource:
    def __init__(self, source_recast, target_recast, source_level, target_level, profiles,
                 namespace, source_identity):
        self.recast, self.level, self.target_level = source_recast, source_level, target_level
        self.namespace = identifier(namespace).lower()
        self.identity = source_identity
        self.index = {name.lower(): name for name in source_recast.namelist()}
        if len(self.index) != len(source_recast.namelist()):
            raise ValueError('Ambiguous source navigation archive paths')
        native = read(target_recast, 'recast/rdnavmesh_1.nav')
        if len(native) < 76:
            raise ValueError('Truncated native navigation reference')
        self.settings, self.version = native[:56], struct.unpack_from('<I', native, 64)[0]
        self.profiles = {}
        by_id = {}
        for profile in profiles.iter('GameProfile'):
            name = profile.findtext('Name')
            identifier(name)
            key, number = name.lower(), profile.findtext('Id')
            if key in self.profiles or number in by_id:
                raise ValueError('Duplicate source navigation profile identity')
            self.profiles[key], by_id[number] = profile, key
        self.groups = {}
        for group in profiles.findall('.//GameProfileGroups/Groups/Group'):
            for profile in group.findall('Profiles/Profile'):
                if profile.text not in by_id:
                    raise ValueError('Navigation group references unknown profile')
                self.groups.setdefault(by_id[profile.text], []).append(group.findtext('Name'))

    def pair(self, stem):
        paths = [stem + extension for extension in ('.hdr', '.nav')]
        if any(path.lower() not in self.index for path in paths):
            raise ValueError('Missing source navigation pair: ' + stem)
        header, mesh = [read(self.recast, self.index[path.lower()]) for path in paths]
        tiles = validate_mesh(header, mesh, self.settings, self.version)
        return header, mesh, dict(header_sha256=digest(header), mesh_sha256=digest(mesh),
            tiles=tiles, tile_version=self.version, source_stem=stem)


class BaseNavigationAdapter:
    def __init__(self, source):
        self.source = source

    def plan(self, identity):
        if identity != self.source.identity.lower():
            raise ValueError('Unexpected navigation world identity')
        _, _, evidence = self.source.pair('recast/rdnavmesh_1')
        graph = read(self.source.level, 'ubernav.tmm')
        native_graph = read(self.source.target_level, 'ubernav.tmm')
        if len(graph) < 4 or len(native_graph) < 4 or graph[:4] != native_graph[:4]:
            raise ValueError('Source navigation graph version differs from target')
        return ImportPlan(source=dict(evidence, graph_sha256=digest(graph),
            graph_version=struct.unpack_from('<I', graph)[0], source_world=identity))

    def convert(self, identity, plan, registered):
        header, mesh, evidence = self.source.pair('recast/rdnavmesh_1')
        if evidence['mesh_sha256'] != plan.source['mesh_sha256'] or evidence['header_sha256'] != plan.source['header_sha256']:
            raise ValueError('Navigation source changed during conversion')
        files = {'world/navigation/recast/rdnavmesh_1.hdr': header,
                 'world/navigation/recast/rdnavmesh_1.nav': mesh}
        for path in ('ubernav.tmm', 'areasmission0.bai'):
            files['world/navigation/' + path] = read(self.source.level, path)
        return self.source.namespace + '_navigation_base', files, dict(plan.source,
            coordinates_preserved=True, runtime_validated=False)


class ProfileNavigationAdapter:
    def __init__(self, source):
        self.source = source

    def plan(self, identity):
        profile = self.source.profiles[identity]
        if profile.findtext('HasNavigation') != '1':
            raise ValueError('Profile does not declare navigation')
        if identity in self.source.groups:
            raise ValueError('Navigation group combinations require conversion: ' + ', '.join(self.source.groups[identity]))
        stem = 'recast/rdnavmesh_' + profile.findtext('Name').lower() + '_1'
        _, _, evidence = self.source.pair(stem)
        return ImportPlan([('navigation_base', self.source.identity)], dict(evidence,
            source_name=profile.findtext('Name')))

    def convert(self, identity, plan, registered):
        header, mesh, evidence = self.source.pair(plan.source['source_stem'])
        if evidence['mesh_sha256'] != plan.source['mesh_sha256'] or evidence['header_sha256'] != plan.source['header_sha256']:
            raise ValueError('Profile navigation changed during conversion')
        target = self.source.namespace + '_' + plan.source['source_name']
        stem = 'world/navigation/recast/rdnavmesh_' + target.lower() + '_1'
        return target, {stem + '.hdr': header, stem + '.nav': mesh}, dict(evidence,
            native_profile=target, source_name=plan.source['source_name'],
            coordinates_preserved=True, profile_path_remapped=True, runtime_validated=False)
