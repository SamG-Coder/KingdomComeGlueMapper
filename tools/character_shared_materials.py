"""Intern identical population materials without changing shader/slot semantics."""
import hashlib
import posixpath
import struct
import xml.etree.ElementTree as ET

from upgrade_map import xml

ROOT = 'objects/characters/'
POPULATION = ROOT + 'gluetravel/gmp_'
SHARED = ROOT + 'gluetravel/shared/'


def key(path): return posixpath.normpath(path.replace('\\', '/')).lower()


class SharedMaterials:
    def __init__(self):
        self.aliases, self.materials = {}, {}
        self.source_slots = 0
        self.component_references = 0
        self.mesh_references = 0

    def add(self, name, payload):
        if not name.lower().startswith(POPULATION) or not name.lower().endswith('.mtl'): return
        root = ET.fromstring(payload)
        # Do not remove material names: clothing features can depend on them.
        normalized = ET.canonicalize(ET.tostring(root, encoding='unicode')).encode('utf-8')
        destination = SHARED + hashlib.sha256(normalized).hexdigest() + '.mtl'
        self.aliases[key(name)] = destination
        self.materials.setdefault(destination, payload)
        self.source_slots += sum(bool(e.get('Shader')) for e in root.iter('Material'))

    def rewrite(self, name, payload):
        if key(name) in self.aliases: return None
        lower = name.lower()
        if lower.startswith('libs/tables/character/') and lower.endswith('.xml'):
            root = ET.fromstring(payload)
            changed = False
            for component in root.iter('Component'):
                old = component.get('FilePath', '').replace('\\', '/')
                if not old.lower().startswith('gluetravel/gmp_'): continue
                if any(e is not component and e.get('FilePath') is not None for e in component.iter()):
                    raise ValueError('Population component has a nested path override')
                component.set('FilePath', 'gluetravel/')
                for element in component.iter():
                    for attribute, extension in (('Model', None), ('Material', '.mtl')):
                        value = element.get(attribute)
                        if not value or extension and not value.lower().endswith(extension): continue
                        resolved = key(ROOT + old + value)
                        replacement = self.aliases.get(resolved, resolved)
                        prefix = ROOT + 'gluetravel/'
                        if not replacement.startswith(prefix):
                            raise ValueError('Unexpected population component asset path: ' + replacement)
                        element.set(attribute, replacement[len(prefix):])
                        if attribute == 'Material':
                            if resolved not in self.aliases:
                                raise ValueError('Missing material to intern: ' + resolved)
                            self.component_references += 1
                changed = True
            return xml(root) if changed else payload
        if lower.startswith(POPULATION) and lower.endswith(('.skin', '.chr', '.cgf', '.cga')):
            blob = bytearray(payload)
            if len(blob) < 16: raise ValueError('Truncated population mesh')
            count, table = struct.unpack_from('<II', blob, 8)
            if table + count * 16 > len(blob): raise ValueError('Invalid population chunk table')
            for i in range(count):
                kind, version, _, size, offset = struct.unpack_from('<HHIII', blob, table + i * 16)
                if kind != 0x1014: continue
                if version != 0x802 or size < 128 or offset + size > len(blob):
                    raise ValueError('Unsupported population material-name chunk')
                original = bytes(blob[offset:offset+128]).split(b'\0', 1)[0].decode('utf-8')
                resolved = key(original if original.lower().endswith('.mtl') else original + '.mtl')
                destination = self.aliases.get(resolved)
                if destination:
                    value = destination.removesuffix('.mtl').encode('utf-8')
                    if len(value) >= 128: raise ValueError('Shared material path exceeds mesh field')
                    blob[offset:offset+128] = value.ljust(128, b'\0')
                    self.mesh_references += 1
            return bytes(blob)
        return payload

    def report(self):
        return dict(source_material_files=len(self.aliases), shared_material_files=len(self.materials),
                    source_shader_slots=self.source_slots,
                    shared_shader_slots=sum(sum(bool(e.get('Shader')) for e in ET.fromstring(p).iter('Material'))
                                            for p in self.materials.values()),
                    component_references=self.component_references, mesh_references=self.mesh_references,
                    runtime_verified=False)
