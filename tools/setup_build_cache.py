"""Workspace-wide immutable build artifacts; job packages always receive copies."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4*1024**2), b''): h.update(block)
    return h.hexdigest()


TOOLS = Path(__file__).resolve().parent
_TOOLS_DIGEST = None


def tools_digest():
    """Digest of every file under tools/: any converter change makes a new key.

    The converters import one another throughout, so a narrower list could
    miss a dependency. Cached outputs survive only identical code.
    """
    global _TOOLS_DIGEST
    if _TOOLS_DIGEST is None:
        files = {}
        for directory, folders, names in os.walk(TOOLS):
            folders[:] = sorted(f for f in folders if f != '__pycache__')
            for name in names:
                path = Path(directory)/name
                files[path.relative_to(TOOLS).as_posix()] = file_hash(path)
        _TOOLS_DIGEST = fingerprint(files)
    return _TOOLS_DIGEST


def converter_identity():
    """Code, interpreter, libraries and output-affecting settings of this process."""
    import importlib.metadata
    import sys
    ignored = ('GLUEMAPPER_BUILD_CACHE', 'GLUEMAPPER_REBUILD_CACHE', 'GLUEMAPPER_IMPORTED_CACHE',
               'GLUEMAPPER_FULL_VERIFY', 'GLUEMAPPER_PROFILE')
    settings = {k: v for k, v in os.environ.items() if k.startswith('GLUEMAPPER_')
                and k not in ignored and not k.endswith('_WORKERS')}
    libraries = sorted((d.metadata['Name'] or '', d.version) for d in importlib.metadata.distributions())
    return dict(tools=tools_digest(), python=sys.version, libraries=libraries, settings=settings)


_INSTALLS = {}


def install_fingerprint(game):
    """Every game archive's path, size and time: patches, DLC and repairs change it.

    Remembered per process: a game is never patched while a build runs.
    """
    game = Path(game)
    if game.resolve() in _INSTALLS: return _INSTALLS[game.resolve()]
    entries = []
    for folder in ('Data', 'Localization'):
        for directory, _, files in os.walk(game/folder):
            for name in files:
                if name.lower().endswith('.pak'):
                    stat = os.stat(os.path.join(directory, name))
                    entries.append((os.path.relpath(os.path.join(directory, name), game).replace('\\', '/').lower(),
                                    stat.st_size, stat.st_mtime_ns))
    _INSTALLS[game.resolve()] = fingerprint(sorted(entries))
    return _INSTALLS[game.resolve()]


@contextmanager
def environment(config):
    values = {'GLUEMAPPER_BUILD_CACHE': str(Path(config['workspace']).resolve()/'cache'),
              'GLUEMAPPER_REBUILD_CACHE': '1' if config.get('rebuild_cache') else '0',
              'GLUEMAPPER_IMPORTED_CACHE': config.get('imported_cache','')}
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try: yield
    finally:
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v


def root():
    value = os.environ.get('GLUEMAPPER_BUILD_CACHE')
    return Path(value) if value else None


def forced():
    return os.environ.get('GLUEMAPPER_REBUILD_CACHE') == '1'


class Cache:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def lookup(self, key):
        """Never accept partial publication, changed bytes or redirected paths."""
        try:
            name = json.loads((self.folder/(key+'.json')).read_text())['entry']
            if Path(name).name != name or not name.startswith(key+'-'): return None
            entry = self.folder/name
            if entry.resolve() != entry.absolute(): return None
            report = json.loads((entry/'manifest.json').read_text())
            if report['key'] != key or not report['files']: return None
            for rel, expected in report['files'].items():
                p = entry/'payload'/rel
                if (Path(rel).is_absolute() or '..' in Path(rel).parts or ':' in rel
                        or '\\' in rel or p.resolve() != p.absolute()
                        or file_hash(p) != expected): return None
            actual = {p.relative_to(entry/'payload').as_posix()
                      for p in (entry/'payload').rglob('*') if p.is_file()}
            if actual != set(report['files']): return None
            return entry/'payload', report['metadata']
        except (OSError, ValueError, KeyError, TypeError, AttributeError): return None

    def publish(self, key, files, metadata, cancelled=lambda: False):
        # A unique attempt plus atomic pointer keeps the old entry valid until
        # replacement completes. Failed/cancelled attempts are never selected.
        entry = self.folder/(key+'-'+uuid.uuid4().hex)
        payload = entry/'payload'; payload.mkdir(parents=True)
        hashes = {}
        for rel, source in files.items():
            if cancelled(): raise InterruptedError('Cancelled before caching completed outputs')
            destination = payload/rel
            if (Path(rel).is_absolute() or '..' in Path(rel).parts or ':' in rel
                    or '\\' in rel): raise ValueError('Unsafe cache output')
            destination.parent.mkdir(parents=True, exist_ok=True)
            expected = file_hash(source)
            shutil.copy2(source, destination)
            if file_hash(destination) != expected: raise ValueError('Cache copy changed: '+rel)
            hashes[rel] = expected
        (entry/'manifest.json').write_text(json.dumps(dict(key=key, files=hashes, metadata=metadata)))
        if cancelled(): raise InterruptedError('Cancelled before publishing cache')
        pending = self.folder/(entry.name+'.tmp')
        pending.write_text(json.dumps(dict(entry=entry.name)))
        pending.replace(self.folder/(key+'.json'))
        return payload

    def prune(self, keep):
        """Keep the `keep` most recently published keys; remove other and abandoned entries.

        Entries younger than an hour that no pointer names may belong to a
        publish still in progress elsewhere and are left alone.
        """
        import time
        pointers = sorted((p for p in self.folder.glob('*.json')), key=lambda p: p.stat().st_mtime, reverse=True)
        kept = set()
        for index, pointer in enumerate(pointers):
            try: name = json.loads(pointer.read_text())['entry']
            except (OSError, ValueError, KeyError, TypeError): name = None
            if index < keep and name and Path(name).name == name:
                kept.add(name); continue
            pointer.unlink(missing_ok=True)
        for entry in self.folder.iterdir():
            if (entry.is_dir() and entry.name not in kept and entry.resolve().parent == self.folder.resolve()
                    and time.time() - entry.stat().st_mtime > 3600):
                shutil.rmtree(entry, ignore_errors=True)


def copy_payload(payload, destination):
    """No hard links: later consumers may rewrite or consume their own copy."""
    shutil.copytree(payload, destination, dirs_exist_ok=True)
