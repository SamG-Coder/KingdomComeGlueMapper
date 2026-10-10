"""Bounded ZIP32 mod archives and a unified view of their contents."""
from contextlib import ExitStack
from pathlib import Path
import zipfile

MAX_BYTES = 1024 ** 3
MAX_ENTRIES = 50000


class RetailPakWriter:
    def __init__(self, directory, stem, max_bytes=MAX_BYTES, max_entries=MAX_ENTRIES):
        self.directory, self.stem = Path(directory), stem
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.archive = None
        self.paths, self.names = [], set()
        self.central_size = 22

    def __enter__(self): return self

    def __exit__(self, *_):
        if self.archive: self.archive.close()

    def writestr(self, name, payload):
        name = name.filename if isinstance(name, zipfile.ZipInfo) else name
        name = name.replace('\\', '/')
        if name.lower() in self.names: raise ValueError('Duplicate PAK member: ' + name)
        if name.startswith('/') or ':' in name or '..' in name.split('/'):
            raise ValueError('Unsafe PAK member: ' + name)
        # Include worst-case deflate expansion, both headers, and central names.
        cost = len(payload) + len(payload) // 1000 + 200 + 2 * len(name.encode('utf-8'))
        if cost + 22 > self.max_bytes: raise ValueError('Single asset exceeds PAK budget: ' + name)
        if (self.archive is None or len(self.archive.filelist) >= self.max_entries
                or self.archive.fp.tell() + self.central_size + cost > self.max_bytes):
            if self.archive: self.archive.close()
            suffix = '' if not self.paths else '_population_' + str(len(self.paths) - 1).zfill(3)
            path = self.directory / (self.stem + suffix + '.pak')
            self.archive = zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED, allowZip64=False, compresslevel=1)
            self.paths.append(path)
            self.central_size = 22
        self.archive.writestr(name, payload)
        self.central_size += 46 + len(name.encode('utf-8'))
        self.names.add(name.lower())


class PakSet:
    """Read all root mod archives, rejecting ambiguous resource overrides."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.stack = ExitStack()
        self.entries = {}

    def __enter__(self):
        try:
            seen = set()
            for path in sorted(self.directory.glob('*.pak')):
                archive = self.stack.enter_context(zipfile.ZipFile(path))
                for name in archive.namelist():
                    if name.lower() in seen: raise ValueError('Duplicate resource across mod PAKs: ' + name)
                    seen.add(name.lower()); self.entries[name] = archive
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *_): self.stack.close()
    def namelist(self): return list(self.entries)
    def read(self, name): return self.entries[name].read(name)


def validate_paks(mods):
    result = []
    for path in sorted(Path(mods).rglob('*.pak')):
        size = path.stat().st_size
        if size > MAX_BYTES: raise ValueError('PAK exceeds retail build limit: ' + str(path))
        with zipfile.ZipFile(path) as z:
            # Exported terrain archives have a separate engine loading path and
            # can contain more than 65k small tile records (the working map has
            # 83,434). Apply the shard entry budget to root mod archives only.
            if path.parent.name.lower() == 'data' and len(z.filelist) > MAX_ENTRIES:
                raise ValueError('Too many root mod PAK entries: ' + str(path))
            if any(i.extract_version >= 45 for i in z.filelist):
                raise ValueError('ZIP64/unsupported ZIP version in retail PAK: ' + str(path))
            result.append(dict(path=str(path.relative_to(mods)), bytes=size, entries=len(z.filelist)))
    return result
