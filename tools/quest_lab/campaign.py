"""Persistent campaign conversion diagnostics; never claim gameplay completion."""
import json
from pathlib import Path

from .source_audit import run


def write(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
    temporary.replace(path)


def campaign(game, output, first=(), progress=None):
    output = Path(output)
    if any((output / name).exists() for name in ('campaign.json', 'checkpoint.json')):
        raise ValueError('Campaign output already contains a run; choose a new output directory')
    def checkpoint(results, total):
        write(output / 'checkpoint.json', dict(status='IN_PROGRESS',
              completed_records=len(results), total_records=total,
              native_runtime_verified=False, quests=results))
    result = run(game, progress, first=first, checkpoint=checkpoint)
    write(output / 'campaign.json', result)
    write(output / 'checkpoint.json', dict(status='AUDIT_FINISHED',
          completed_records=len(result['quests']), total_records=len(result['quests']),
          native_runtime_verified=False, quests=result['quests']))
    return result
