"""Optional progress sink shared by CLI tooling and the desktop worker."""
sink = None


def progress(label, current, total, detail=''):
    if sink is not None:
        sink(label, current, total, detail)

