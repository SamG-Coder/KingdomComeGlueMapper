"""Emit a native quest-progress diagnostic from a retail quest identity.

Manual transitions test target registration; they do not translate source quest
activation semantics or execute objectives, rewards, dialogue or initialization.
"""
import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def xml(root):
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def generate(model, project, objective_id=None):
    name = model['quest']
    if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', s) for s in (name, project)):
        raise ValueError('Invalid native identifier')
    rows = model['tables']['quest']['rows']
    if len(rows) != 1 or rows[0]['quest_name'] != name or rows[0]['quest_id'] != model['quest_id']:
        raise ValueError('Quest identity mismatch')
    root = ET.Element('Database', Name='brambora')
    quest = ET.SubElement(ET.SubElement(root, 'Skald'), 'Quest', Name=name)
    nodes = ET.SubElement(quest, 'Nodes')
    state = ET.SubElement(nodes, 'State', Name='progress', TypeT='wh::questmodule::QuestProgress')
    ET.SubElement(state, 'Constant', Name='DefaultValue', Value='None')
    for operation, target in [('start', 'Active'), ('complete', 'Done'), ('fail', 'Failed')]:
        ET.SubElement(nodes, 'HasteTrigger', Name='probe_' + operation)
        ET.SubElement(state, 'Edge', From='probe_' + operation + '.OnTrigger', To='Set' + target)
    ET.SubElement(nodes, 'HasteTrigger', Name='probe_read')
    trace = ET.SubElement(nodes, 'Trace', Name='progress_proof', TypeT='wh::questmodule::QuestProgress')
    ET.SubElement(trace, 'Edge', From='progress.State', To='Value')
    ET.SubElement(trace, 'Edge', From='progress.OnExec', To='Exec')
    ET.SubElement(trace, 'Edge', From='probe_read.OnTrigger', To='Exec')
    output = ET.SubElement(nodes, 'Output', Name='Output')
    ET.SubElement(output, 'Edge', From='progress.State', To='Progress')
    ET.SubElement(quest, 'QuestName', Text=name)
    host = ET.Element('Database', Name='brambora')
    proj = ET.SubElement(ET.SubElement(host, 'Skald'), 'Project', Name=project)
    definitions = ET.SubElement(proj, 'Definitions')
    ET.SubElement(definitions, 'Definition', File=project + '/' + name + '.xml')
    ET.SubElement(ET.SubElement(proj, 'Nodes'), name, Name=name)
    objective_record = None
    if objective_id is not None:
        matches = [r for r in model['tables']['quest_objective']['rows']
                   if r['objective_id'] == str(objective_id) and r['quest_id'] == model['quest_id']]
        if len(matches) != 1: raise ValueError('Objective identity missing or ambiguous')
        objective_record = matches[0]
        objective_name = objective_record['objective_name']
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', objective_name):
            raise ValueError('Invalid objective identifier')
        progress_type = project + 'ObjectiveProgress'
        types = ET.SubElement(proj, 'Types')
        enum = ET.SubElement(types, 'Type', TypeName=progress_type)
        for value, status in [('None', 'None'), ('Active', 'Started'), ('Done', 'Completed')]:
            ET.SubElement(enum, 'StateTypeEnumeration', Name=value, ObjectiveValueType=status)
        values = ET.SubElement(proj, 'ObjectiveValueTypes')
        for status, past, icon in [('None', 'false', 'Exclamation'), ('Started', 'false', 'Exclamation'), ('Completed', 'true', 'Check')]:
            ET.SubElement(values, 'ObjectiveValueType', Type=status, IsPast=past, Icon=icon)
        declaration = ET.SubElement(ET.SubElement(quest, 'Objectives'), 'Objective',
                                    Name=objective_name, TypeT=progress_type)
        # Source localization and hidden-objective policy are separate adapters.
        ET.SubElement(declaration, 'LocalizedName', Text=objective_name)
        logs = ET.SubElement(declaration, 'Logs')
        for value, status in [('None', 'None'), ('Active', 'Started'), ('Done', 'Completed')]:
            ET.SubElement(logs, 'EnumLog', Name=value, Type=status)
        objective_state = ET.SubElement(nodes, 'State', Name='objective_progress', TypeT=progress_type)
        ET.SubElement(objective_state, 'Constant', Name='DefaultValue', Value='None')
        for operation, value in [('start', 'Active'), ('complete', 'Done')]:
            trigger = 'probe_objective_' + operation
            ET.SubElement(nodes, 'HasteTrigger', Name=trigger)
            ET.SubElement(objective_state, 'Edge', From=trigger + '.OnTrigger', To='Set' + value)
        visual = ET.SubElement(nodes, objective_name, Name='objective_binding')
        ET.SubElement(visual, 'Edge', From='objective_progress.State', To='Progress')
        proof = ET.SubElement(nodes, 'Trace', Name='objective_proof', TypeT=progress_type)
        ET.SubElement(proof, 'Edge', From='objective_progress.State', To='Value')
        ET.SubElement(proof, 'Edge', From='objective_progress.OnExec', To='Exec')
    report = dict(source_quest=name, source_quest_id=model['quest_id'], project=project,
                  provenance=model['tables']['quest']['provenance'],
                  activation='manual target diagnostic', source_activation_translated=False,
                  objectives_translated=False, campaign_ready=False)
    report['diagnostic_objective'] = objective_record
    return {project + '.xml': xml(host), project + '/' + name + '.xml': xml(root)}, report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ir', type=Path, required=True)
    p.add_argument('--project', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--objective-id', help='Optional source objective for a manual native binding test')
    a = p.parse_args()
    files, report = generate(json.loads(a.ir.read_text(encoding='utf-8')), a.project, a.objective_id)
    if a.output.exists(): raise ValueError('Choose a new output folder')
    for name, payload in files.items():
        target = a.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    (a.output/'conversion.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
