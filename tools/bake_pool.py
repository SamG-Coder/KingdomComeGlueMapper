"""Float macro texture bakes in worker processes shared by one build.

A fixed-outfit conversion bakes about twenty 2048px textures, which took half
of each NPC's import when run one after another on the main thread. The pool
starts on first use and is reused by every later conversion; callers that
finish a batch call release() so its workers do not idle through the rest of
the build (atexit covers the rest).
"""
import atexit
from concurrent.futures import ProcessPoolExecutor, wait as wait_for
import copy
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from setup_progress import work_progress

from bounded_pool import worker_count
from character_native_materials import bake_float_dds

_POOL = None
_CACHE = None


class TextureBakeCache:
    """Private bounded cache of completed pixel-identical bakes in one process.

    Outputs are copied, never hardlinked: later converters may edit their files.
    Keys describe only the pixels and material inputs consumed by the baker.
    No cache survives release(), so a new converter version cannot reuse it.
    """
    def __init__(self, limit=512 * 1024**2):
        self.folder = tempfile.TemporaryDirectory(prefix='gluemapper-texture-bakes-')
        self.entries, self.size, self.limit = OrderedDict(), 0, limit

    @staticmethod
    def key(image, material, specular):
        params = material.find('PublicParams')
        flags = material.get('StringGenMask', '').split('%')
        mode = 'masked' if 'COLORIZING_WITH_MASK' in flags else 'tinted' if 'COLORIZING' in flags else 'plain'
        names = ['ColorizingHue', 'ColorizingSaturation', 'ColorizingBrightness'] if mode != 'plain' else []
        if mode == 'masked':
            names += [f'Mask{channel}{zone}' for zone in range(1, 4) for channel in ('Hue', 'Saturation', 'Brightness')]
        # Shader normals, roughness and other parameters are not read by the
        # macro baker. They must remain on the material, but cannot make the
        # same pixel bake a distinct texture. Specular supplies zones only in
        # the masked-colour permutation.
        inputs = dict(diffuse=material.get('Diffuse','1,1,1'), mode=mode,
                      params={name: params.get(name) for name in names if params is not None and name in params.attrib})
        digest = hashlib.sha256(json.dumps(inputs,sort_keys=True).encode())
        # The baker converts diffuse to RGBA before processing it. Specular is
        # resized in its original mode; keep that mode and palette in the key.
        for pixels in (image.convert('RGBA'), specular if mode == 'masked' else None):
            if pixels is None:
                digest.update(b'none'); continue
            digest.update(repr((pixels.mode,pixels.size)).encode())
            if pixels.palette is not None: digest.update(pixels.palette.tobytes())
            digest.update(pixels.tobytes())
        return digest.hexdigest()

    def get(self, key, destination):
        entry = self.entries.get(key)
        if entry is None: return False
        path, _ = entry
        shutil.copyfile(path, destination)
        self.entries.move_to_end(key)
        return True

    def put(self, key, source):
        source = Path(source)
        if key in self.entries or not source.is_file(): return
        size = source.stat().st_size
        if size > self.limit: return
        while self.entries and self.size + size > self.limit:
            _, (path, removed) = self.entries.popitem(last=False)
            path.unlink(); self.size -= removed
        path = Path(self.folder.name)/key
        try:
            shutil.copyfile(source, path)
        except OSError:
            path.unlink(missing_ok=True)
            return  # An optional cache must not invalidate a successful bake.
        self.entries[key] = (path, size); self.size += size

    def close(self):
        self.folder.cleanup(); self.entries.clear(); self.size = 0


def shared_cache():
    global _CACHE
    if _CACHE is None:
        _CACHE = TextureBakeCache()
        atexit.register(release)
    return _CACHE


def pool():
    global _POOL
    if _POOL is None:
        # A 2048px bake holds several float32 RGBA copies and its mips.
        _POOL = ProcessPoolExecutor(max_workers=worker_count(16, 768 * 1024**2, 'GLUEMAPPER_BAKE_WORKERS'))
        atexit.register(release)
    return _POOL


def release():
    global _POOL, _CACHE
    if _POOL is not None:
        _POOL.shutdown()
        _POOL = None
    if _CACHE is not None:
        _CACHE.close(); _CACHE = None


class Bakes:
    """One conversion's deferred bakes; wait() must run before its outputs are read.

    Matches the bake(path, image, material, specular=None) callback of
    convert_character_materials. Arguments are copied because they are pickled
    after submit() and the caller may reuse the images. Use it as a context
    manager: leaving the block waits for every bake, and if a bake or the
    conversion fails, queued bakes are cancelled and running ones joined before
    the original error propagates, so nothing writes into the outputs after a
    reported failure.
    """
    def __init__(self, *, reuse=False):
        self.pending = []
        self.cache = shared_cache() if reuse else None
        self.completed, self.total, self.reused = 0, 0, 0
        self.inflight = {}
        self.references = {}

    def plan(self, total):
        self.total = total
        self.report()

    def report(self):
        work_progress('Native textures', self.completed, self.total,
                      f'{self.reused} reused from identical source pixels')

    @property
    def stats(self):
        return dict(completed=self.completed, total=self.total, reused=self.reused)

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if kind is None:
            self.wait()
        else:
            self.drain()
        return False

    def __call__(self, path, image, material, specular=None):
        key = self.cache.key(image, material, specular) if self.cache else None
        self._submit(path, image, material, specular, key)

    def reference(self, path, image, material, specular=None):
        """Return one immutable output path for identical pixels in this batch.

        Material consumers can share this path instead of writing and packing
        another full texture. Ordinary __call__ keeps its independent-file
        contract. References never escape this conversion's staging directory.
        """
        key = TextureBakeCache.key(image, material, specular)
        if key in self.references:
            self.completed += 1; self.reused += 1; self.report()
            return self.references[key]
        self._submit(path, image, material, specular, key)
        self.references[key] = path
        return path

    def _submit(self, path, image, material, specular, key):
        if self.cache and self.cache.get(key, path):
            self.completed += 1; self.reused += 1; self.report()
            return
        executor = pool()
        # Bound the queued texture copies.
        while len(self.pending) >= 2 * executor._max_workers:
            self._finish_next()
        if key is not None and key in self.inflight:
            origin, future = self.inflight[key]
        else:
            origin = path
            future = executor.submit(bake_float_dds, path, image.copy(), copy.deepcopy(material),
                                     None if specular is None else specular.copy())
            if key is not None: self.inflight[key] = (path, future)
        self.pending.append((path, key, future, origin))

    def _finish_next(self):
        path, key, future, origin = self.pending.pop(0)
        try:
            future.result()
            if origin != path:
                shutil.copyfile(origin, path); self.reused += 1
            if self.cache: self.cache.put(key, path)
            self.completed += 1; self.report()
        except Exception as error:
            self.drain()
            raise RuntimeError('Character texture bake ' + str(path) + ': ' + str(error)) from error

    def drain(self):
        """Cancel queued bakes and wait for running ones; their own errors are dropped."""
        pending, self.pending = self.pending, []
        for _, _, future, _ in pending:
            future.cancel()
        wait_for([future for _, _, future, _ in pending])

    def wait(self):
        try:
            while self.pending:
                self._finish_next()
        except BaseException:
            self.drain()
            raise
