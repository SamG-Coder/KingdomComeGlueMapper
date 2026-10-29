"""Optional progress sink shared by CLI tooling and the desktop worker."""
sink = None
work_sink = None


def progress(label, current, total, detail=''):
    if sink is not None:
        sink(label, current, total, detail)


def work_progress(label, current, total, detail=''):
    """Measured work within the current item, separate from the batch counter."""
    if work_sink is not None:
        work_sink(label, current, total, detail)

