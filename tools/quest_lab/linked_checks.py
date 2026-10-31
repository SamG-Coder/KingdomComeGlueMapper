"""Exercise compiled cross-quest completion with explicit diagnostic inputs."""
import re


def scenarios(condition, entry='QuestLabCampaign'):
    match = re.fullmatch(r"\s*IsQuestCompleted\('([A-Za-z_]\w*)'\)\s*", condition['expression'])
    if not match:
        raise ValueError('This scenario requires one literal IsQuestCompleted predicate')
    owner, objective, dependency = condition['quest'], condition['objective'], match[1]
    from campaign_quest_graph import identifier, objective_identifier
    identifier(owner); identifier(dependency)
    objective = objective_identifier(objective)
    prefix = owner + '.'
    start = dict(action='fire', port=prefix + 'start_' + objective)
    cancel = dict(action='fire', port=prefix + 'cancel_' + objective)
    complete = dict(action='fire', port=dependency + '.complete')
    failed = dict(action='fire', port=dependency + '.fail')
    def check(done, canceled=False):
        return dict(action='assert', values={prefix + objective + '_Completed': done,
                                            prefix + objective + '_Canceled': canceled})
    cases = {
        'wait and replay': [start, check(False), dict(action='save', name='waiting'),
                            failed, check(False), complete, check(True),
                            dict(action='load', name='waiting'), check(False), complete, check(True)],
        'completion before objective starts': [complete, check(False), start, check(True)],
        'cancel and restart': [start, cancel, complete, check(False, True), start, check(True)],
    }
    return [dict(schema=1, name=owner + ':' + objective + ': ' + name, entry=entry,
                 description='Diagnostic public inputs; dependency gameplay is not simulated.',
                 budget=1000000, steps=steps) for name, steps in cases.items()]
