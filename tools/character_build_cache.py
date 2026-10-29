"""Read-only source inputs shared within one fresh character build batch.

The scope owns archive handles and tables. It never caches converted appearances
or survives a build, so changed installations and later jobs are read afresh.
"""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path

from audit_character_bodies import Assets
from campaign_sources import RetailSourceReader
from campaign_dependency_adapters import SourceTables

_ACTIVE = ContextVar('character_build_inputs', default=None)


@contextmanager
def appearance_batch():
    if _ACTIVE.get() is not None:
        yield
        return
    with ExitStack() as stack:
        token = _ACTIVE.set((stack, {}))
        try:
            yield
        finally:
            _ACTIVE.reset(token)


def character_inputs(source, target, stack):
    active = _ACTIVE.get()
    key = (Path(source).resolve(), Path(target).resolve())
    owner, cache = active if active is not None else (stack, {})
    if key not in cache:
        reader = owner.enter_context(RetailSourceReader(source))
        cache[key] = SourceTables(source, reader), Assets(Path(target), owner)
    return cache[key]
