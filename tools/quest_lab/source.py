"""Read loose or archived converted XML without extracting or altering it."""
import hashlib
from pathlib import Path
import posixpath
import xml.etree.ElementTree as E
import zipfile

from . import Unsupported
from .dialogue import KINDS as DIALOGUES


def definition_key(node):
    """Ignore XML formatting and an empty include list, not executable content."""
    text = node.text if node.text and node.text.strip() else ''
    return (node.tag, tuple(sorted(node.attrib.items())), text, tuple(
        definition_key(child) for child in node
        if not (child.tag == 'Definitions' and not len(child) and not child.attrib
                and not (child.text or '').strip())))


class Source:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.files = {}
        self.definitions = {}
        self.hashes = {}
        self.enums = {}
        self.types = {}
        if self.path.is_dir():
            for p in sorted(self.path.rglob('*.xml')):
                self.add(p.relative_to(self.path).as_posix(), p.read_bytes())
        elif zipfile.is_zipfile(self.path):
            with zipfile.ZipFile(self.path) as z:
                for item in z.infolist():
                    if item.filename.lower().endswith('.xml'):
                        self.add(item.filename, z.read(item))
        else:
            self.add(self.path.name, self.path.read_bytes())
        if not self.files: raise ValueError('No XML in source')

    @classmethod
    def blobs(cls, blobs):
        obj = cls.__new__(cls)
        obj.path = Path('<generated>')
        obj.files, obj.definitions, obj.hashes, obj.enums, obj.types = {}, {}, {}, {}, {}
        for path, blob in sorted(blobs.items()): obj.add(path, blob)
        return obj

    def add(self, path, data):
        key = path.replace('\\', '/').lstrip('/').lower()
        if key in self.files: raise ValueError('Duplicate XML source: ' + path)
        try: root = E.fromstring(data)
        except E.ParseError:
            # Unrelated tables can be non-XML despite their extension; retain the
            # rejection in inventory instead of allowing a graph to resolve to it.
            root = None
        self.files[key] = root
        self.hashes[key] = hashlib.sha256(data).hexdigest()
        if root is None: return
        for group in root.findall('./Types'):
            def visit_types(parent, prefix=''):
                for node in parent.findall('Type'):
                    name = prefix + node.get('Name','')
                    self.types.setdefault(name, []).append((key, group.get('version'), node))
                    visit_types(node,name+':')
            visit_types(group)
        for group in root.findall('./Enums'):
            for enum in group.findall('Enum'):
                self.enums.setdefault(enum.get('Name'), []).append((key, group.get('version'), enum))
        for definition in root.findall('./Skald/*'):
            if definition.tag not in {'Project', 'Quest', 'Module', 'Level'} | DIALOGUES: continue
            name = definition.get('Name')
            if not name: raise ValueError('Unnamed Skald definition: ' + path)
            self.definitions.setdefault(name, []).append((key, definition))

    def enum_value(self, name, member):
        declarations = self.enums.get(name, [])
        if len(declarations) != 1:
            raise Unsupported('Missing/ambiguous behavior enum: '+name)
        _, version, node = declarations[0]
        if version != '1' or set(node.attrib) != {'Name'}:
            raise Unsupported('Unsupported behavior enum schema: '+name)
        values = {}
        for value in node:
            key = value.get('Name')
            if value.tag != 'Value' or set(value.attrib) != {'Name','Value'} or not key or key in values or len(value):
                raise Unsupported('Malformed behavior enum member: '+name)
            try: values[key] = int(value.get('Value'), 10)
            except ValueError as error: raise Unsupported('Noninteger behavior enum member: '+name+'.'+key) from error
        if member not in values: raise Unsupported('Missing behavior enum member: '+name+'.'+member)
        return values[member]

    def reference(self, reference, origin):
        reference = reference.replace('\\','/').lower()
        candidates = [reference, posixpath.join(posixpath.dirname(origin), reference), 'quests/'+reference]
        for candidate in candidates:
            key = posixpath.normpath(candidate)
            if key in self.files: return key
        raise Unsupported('Missing definition file: ' + reference + ' from ' + origin)

    def definition(self, name, preferred=None):
        matches = self.definitions.get(name, [])
        if preferred:
            scoped = [(p,d) for p,d in matches if p in preferred]
            if scoped: matches = scoped
        # Packages can carry identical copies in multiple include closures.
        unique = {}
        for path, node in matches: unique.setdefault(definition_key(node), (path,node))
        matches = list(unique.values())
        if len(matches) != 1: raise Unsupported('Missing/ambiguous Skald definition: ' + name)
        return matches[0]

    def inventory(self):
        from collections import Counter
        return dict(source=str(self.path), files=len(self.files), definitions=[
            dict(name=name, file=path, kind=node.tag,
                 nodes=dict(sorted(Counter(c.tag for c in node.findall('./Nodes/*')).items())))
            for name, candidates in sorted(self.definitions.items()) for path, node in candidates],
            invalid_xml=[p for p, root in self.files.items() if root is None])
