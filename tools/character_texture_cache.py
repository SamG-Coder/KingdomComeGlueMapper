"""Compressed, reusable texture families for population conversion."""
import hashlib
import json
from pathlib import Path
import re
import zipfile


PREFIX = 'objects/characters/gluepopulation/'


class TextureCache:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / 'index.json'
        self.index = json.loads(path.read_text()) if path.exists() else {}

    def save(self):
        temporary = self.root / 'index.json.tmp'
        temporary.write_text(json.dumps(self.index, sort_keys=True), encoding='utf-8')
        temporary.replace(self.root / 'index.json')

    def contains(self, name):
        return name in self.index and (self.root / self.index[name]).is_file()

    def family(self, name):
        if not self.contains(name): raise FileNotFoundError('Shared texture family: ' + name)
        with zipfile.ZipFile(self.root / self.index[name]) as z:
            return {n: z.read(n) for n in z.namelist()}

    def put(self, entries):
        names = sorted(entries)
        digest = hashlib.sha256()
        for name in names:
            digest.update(name.encode()); digest.update(entries[name])
        filename = digest.hexdigest() + '.zip'
        destination = self.root / filename
        if not destination.exists():
            temporary = destination.with_suffix('.tmp')
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
                for name in names: z.writestr(name, entries[name])
            temporary.replace(destination)
        for name in names:
            if name in self.index and self.index[name] != filename:
                raise ValueError('Shared texture changed: ' + name)
            self.index[name] = filename

    def source_family(self, original, members, reader):
        target = PREFIX + 'textures/' + hashlib.sha256(original.encode()).hexdigest()[:20] + '_' + Path(original).name
        if self.contains(target): return target, self.family(target)
        payloads = {target + suffix: reader(member) for member, suffix in members}
        self.put(payloads)
        return target, payloads

    def externalize(self, files):
        """Move baked DDS families to content paths and rewrite material refs.

        Returns replacement material bytes and the set of shared members. Meshes
        continue to reference their own materials, so their fixed fields don't
        need binary rewriting. Split-mip family suffixes stay together.
        """
        families = {}
        for name in files:
            match = re.fullmatch(r'(.+\.dds)(\.(?:a|[0-9]+a?))?', name, re.I)
            if match: families.setdefault(match[1], []).append((name, match[2] or ''))
        replacements, shared, excluded = {}, set(), set()
        for base, members in families.items():
            if base.startswith(PREFIX + 'textures/'):
                if not self.contains(base):
                    self.put({name: files[name]() for name, _ in members})
                shared.update(name for name, _ in members)
                excluded.update(name for name, _ in members)
                continue
            payloads = {suffix: files[name]() for name, suffix in members}
            digest = hashlib.sha256()
            for suffix in sorted(payloads):
                digest.update(suffix.encode()); digest.update(payloads[suffix])
            # CryEngine uses normal-map filename suffixes for decoding.
            semantic = next((s for s in ('_ddna', '_ddn', '_diff', '_spec', '_bgs')
                             if Path(base).stem.lower().endswith(s)), '')
            target = PREFIX + 'baked/' + digest.hexdigest()[:24] + semantic + '.dds'
            self.put({target + suffix: data for suffix, data in payloads.items()})
            replacements[base] = target
            shared.update(target + suffix for suffix in payloads)
            excluded.update(name for name, _ in members)
        materials = {}
        import xml.etree.ElementTree as ET
        for name in files:
            if not name.lower().endswith('.mtl'): continue
            root = ET.fromstring(files[name]())
            changed = False
            for texture in root.iter('Texture'):
                original = texture.get('File')
                if original in replacements:
                    texture.set('File', replacements[original]); changed = True
            if changed: materials[name] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        self.save()
        return materials, shared, excluded
