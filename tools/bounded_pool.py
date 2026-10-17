"""Ordered process-pool map with a bounded number of jobs in flight.

Inputs are consumed lazily and results are yielded in input order, so callers
that write archives in source order produce the same bytes as a serial run.
"""
from collections import deque
from concurrent.futures import ProcessPoolExecutor
import ctypes
import importlib
import os


def available_memory():
    """Free physical memory in bytes, or None when the platform does not say."""
    if os.name == 'nt':
        class Status(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        status = Status(); status.dwLength = ctypes.sizeof(Status)
        return status.ullAvailPhys if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    try:
        return os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    except (ValueError, OSError, AttributeError):
        return None


def worker_count(jobs, per_worker, override=None, limit=16, share=.5):
    """Workers for `jobs` tasks needing about `per_worker` bytes each.

    Uses at most `share` of the currently free memory. An environment variable
    named by `override` replaces the computed count.
    """
    if override and os.environ.get(override):
        return max(1, int(os.environ[override]))
    count = min(limit, os.cpu_count() or 1, max(1, jobs))
    free = available_memory()
    if free:
        count = min(count, max(1, int(free * share // per_worker)))
    return max(1, count)


def ordered_map(function, items, workers, window=2):
    """Yield function(item) in input order with at most workers*window items submitted.

    workers <= 1 runs in this process. `function` must be a module-level callable.
    """
    if workers <= 1:
        for item in items:
            yield function(item)
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = deque()
        try:
            for item in items:
                pending.append(pool.submit(function, item))
                if len(pending) >= workers * window:
                    yield pending.popleft().result()
            while pending:
                yield pending.popleft().result()
        finally:
            for future in pending:
                future.cancel()


def starcall(job):
    """Run (function, args) in a worker; function must be module-level."""
    function, args = job
    return function(*args)


def importable(target):
    """Self-test task: resolve 'module:attribute' inside a worker process."""
    module, attribute = target.split(':')
    return callable(getattr(importlib.import_module(module), attribute))
