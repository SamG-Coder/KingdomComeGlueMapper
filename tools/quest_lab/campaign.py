"""Persistent campaign conversion diagnostics; never claim gameplay completion."""
import json
import hashlib
from pathlib import Path

from .source_audit import run
from campaign_quest_graph import emit_project


def compile_linked(models, inventory=None):
    """Compile accepted source quests together so their predicates can connect."""
    files, report = emit_project(models, 'QuestLabCampaign')
    from campaign_quest_items import bind, plan
    if inventory is not None:
        if not isinstance(inventory, dict) or set(inventory) != {'item_map', 'registered', 'player_asset'}:
            raise ValueError('Inventory bindings require item_map, registered and player_asset')
        if not isinstance(inventory['item_map'], dict) or not isinstance(inventory['registered'], list):
            raise ValueError('Inventory bindings require a mapping and registered item list')
    for model, quest in zip(models, report['quests']):
        if inventory is None:
            tracking = plan(model, {}, [])
        else:
            path = 'QuestLabCampaign/' + model['quest'] + '.xml'
            files[path], tracking = bind(files[path], model, inventory['item_map'],
                                        inventory['registered'], inventory['player_asset'])
            converted = {r['objective'] for r in tracking['converted']}
            quest['unsupported'] = [r for r in quest['unsupported'] if not (
                r['kind'] in {'tracked_completion', 'completion_policy'} and r['objective'] in converted)]
        quest['inventory_tracking'] = tracking
    report['files'] = {name: hashlib.sha256(blob).hexdigest() for name, blob in files.items()}
    report['native_runtime_verified'] = False
    report['campaign_completed'] = False
    return files, report


def write(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
    temporary.replace(path)


def campaign(game, output, first=(), progress=None, inventory=None):
    output = Path(output)
    if any((output / name).exists() for name in ('campaign.json', 'checkpoint.json')):
        raise ValueError('Campaign output already contains a run; choose a new output directory')
    def checkpoint(results, total):
        write(output / 'checkpoint.json', dict(status='IN_PROGRESS',
              completed_records=len(results), total_records=total,
              native_runtime_verified=False, quests=results))
    models = []
    result = run(game, progress, first=first, checkpoint=checkpoint, model_ready=models.append)
    if models:
        files, linked = compile_linked(models, inventory)
        for name, blob in files.items():
            path = output / 'compiled' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        write(output / 'linked-conversion.json', linked)
        result['linked_conversion'] = dict(quests=len(models),
            conditions=sum(len(q['converted_linked_conditions']) for q in linked['quests']),
            excluded_records=[q['name'] for q in result['quests']
                              if q['name'] not in {m['quest'] for m in models}],
            campaign_completed=False, native_runtime_verified=False)
    write(output / 'campaign.json', result)
    write(output / 'checkpoint.json', dict(status='AUDIT_FINISHED',
          completed_records=len(result['quests']), total_records=len(result['quests']),
          native_runtime_verified=False, quests=result['quests']))
    return result
