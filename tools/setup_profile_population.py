"""Use the profile-driven population builder in the shared setup finalizer.

Only newly produced actors go through the streamed texture writer. This does
not migrate textures in existing installed or cached actor archives.
"""
import os
from pathlib import Path
import tempfile

from setup_campaign import RECEIPT
from filesystem_paths import filesystem_path

VERSION = 1
MODE = 'selected_static_profiles'


def upgrade(package, source, target, profiles, notify=lambda *a: None, cancelled=lambda: False):
    from build_profile_population_upgrade import build
    from build_recipe import apply_overlay
    from setup_gameplay import ENTRY, LEVEL

    if source is None or target is None:
        raise ValueError('Both source games are required for profile population conversion')
    if cancelled(): raise InterruptedError('Cancelled before profile population conversion')
    # Actor folders repeat their namespace in the output and virtual asset
    # path. Use extended paths for the whole stage, including temp cleanup,
    # rather than depending on a machine-wide Windows registry setting.
    package = filesystem_path(package)
    notify('Import source-profile people and shops', 0, 0, 'Discovering selected profiles: ' + ', '.join(profiles))
    with tempfile.TemporaryDirectory(prefix='profile-population-', dir=package.parent) as temporary:
        work = Path(temporary)
        report = build(source, target, package, work/'build', list(profiles),
            level=LEVEL, entry=ENTRY, update_receipt=False, cancelled=cancelled, progress=notify,
            notify=lambda message: notify('Import source-profile people and shops', 0, 0, message))
        if (report.get('version') != VERSION or report.get('profiles') != list(profiles)
                or report.get('level') != LEVEL or report.get('people', 0) <= 0
                or not isinstance(report.get('service_validation'), list)):
            raise ValueError('Population builder did not validate the selected profile')
        if cancelled(): raise InterruptedError('Cancelled before publishing profile population')
        notify('Publish source-profile people and shops', 0, 0, 'Publishing validated population and shop bindings')
        staged = work/'build/package'
        overlay = work/'overlay'
        # The builder clones the full baseline through hardlinks. Publish only
        # detached/new outputs, never the cloned receipt or unchanged world PAKs.
        # The finalizer owns receipt hashes until all its stages have succeeded.
        for path in sorted(staged.rglob('*')):
            if not path.is_file(): continue
            if path.is_symlink() or not path.resolve().is_relative_to(staged):
                raise ValueError('Unsafe population output: ' + str(path))
            relative = path.relative_to(staged)
            if relative.as_posix() == RECEIPT: continue
            original = package/relative
            if original.exists() and os.path.samefile(original, path): continue
            destination = overlay/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.replace(destination)
        changed = apply_overlay(package, overlay, consume=True) if overlay.exists() else {}
    notify('Import source-profile people and shops', 1, 1, 'Population and shop bindings validated')
    return changed, dict(version=VERSION, mode=MODE, profiles=list(profiles), people=report['people'],
        shops=len(report['service_validation']), scheduler_validation=True, runtime_verified=False,
        work=report['work'])
