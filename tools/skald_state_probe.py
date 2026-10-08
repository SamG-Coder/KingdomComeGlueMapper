"""Generate a bounded native Skald execution probe from an imported assignment.

This tests one conversion rule under manual activation, not its source branch's
eligibility or campaign startup. Unknown defaults/types/expressions are rejected.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET

from quest_import import literal, walk


def execution_context(root, selected_index):
    """Keep control ancestors and ordered predecessors of a selected operation.

    These are dependencies to translate, not a flattened list to execute.
    Only Sequence siblings imply ordered predecessors; branches stay nested.
    """
    counter = 0
    def visit(node, path, ancestors, predecessors):
        nonlocal counter
        index = counter
        counter += 1
        if index == selected_index:
            return dict(path=path, ancestors=ancestors, ordered_predecessors=predecessors)
        for i, child in enumerate(node['children']):
            prior = predecessors
            if node['op'] == 'Sequence':
                prior = predecessors + [dict(path=f'{path}/{j}', subtree=sibling)
                                        for j, sibling in enumerate(node['children'][:i])]
            found = visit(child, f'{path}/{i}', ancestors + [dict(
                path=path, operation=node['op'], attributes=node['attributes'])], prior)
            if found is not None: return found
        return None
    result = visit(root, 'Root', [], [])
    if result is None: raise ValueError('Selected operation is outside runtime tree')
    return result


def project_entities(project, concept_path):
    """Emit the two distinct native entities used by shipped Skald projects.

    This is a mission fragment; the packager must assign level-unique EntityIds.
    Stable GUIDs keep generated identities reproducible across rebuilds.
    """
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', project):
        raise ValueError('Invalid project name')
    path = concept_path.replace('\\', '/')
    if not path.startswith('Quests/') or '..' in path.split('/') or not path.endswith('.xml'):
        raise ValueError('Expected a relative Quests XML path')
    root = ET.Element('Objects')
    for name, cls in ((project, 'SmartObjectHolder'), (project + '_concept', 'Concept')):
        guid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'gluemapper/skald/' + name))
        entity = ET.SubElement(root, 'Entity', Name=name, EntityClass=cls,
                               EntityGuid=guid, Pos='0,0,0')
        props = ET.SubElement(entity, 'Properties')
        if cls == 'Concept':
            props.set('fileConcept', path)
            props.set('bIncludeModConcepts', '0')
        else:
            props.set('guidSmartObjectType', 'DEF0005E-0000-0000-0000-DEF00000005E')
            props.set('bSaved_by_game', '0')
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def convert_assignment(model, document, tree_name, variable, project):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', project): raise ValueError('Invalid project name')
    tree = model['behavior_documents'][document]['trees'][tree_name]
    declarations = [v for v in tree['variables'] if v.get('name') == variable]
    if len(declarations) != 1: raise ValueError('Variable declaration missing or ambiguous')
    declaration = declarations[0]
    if declaration.get('type') != '_bool' or declaration.get('form') != 'single' or declaration.get('isPersistent') != '1':
        raise ValueError('Only persistent scalar Boolean state is supported by this probe')
    default = declaration.get('values')
    if default not in ('true', 'false'): raise ValueError('An explicit Boolean default is required')
    pattern = re.compile(r'\$' + re.escape(variable) + r'\s*=\s*(true|false)')
    assignments = []
    for index, node in enumerate(walk(tree['root'])):
        if node['op'] != 'Expression': continue
        expression = literal(node['attributes'].get('expressions', ''))
        match = pattern.fullmatch(expression.strip())
        if match: assignments.append((index, expression, match[1]))
    if len(assignments) != 1: raise ValueError('Expected one exact supported assignment; compound expressions are not lowered')
    index, expression, value = assignments[0]
    root = ET.Element('Database', Name='brambora')
    target = ET.SubElement(ET.SubElement(root, 'Skald'), 'Project', Name=project)
    nodes = ET.SubElement(target, 'Nodes')
    ET.SubElement(nodes, 'HasteTrigger', Name='probe_read')
    ET.SubElement(nodes, 'HasteTrigger', Name='probe_assign')
    state = ET.SubElement(nodes, 'State', Name='source_state', TypeT='bool')
    ET.SubElement(state, 'Constant', Name='DefaultValue', Value=default)
    ET.SubElement(state, 'Edge', From='probe_assign.OnTrigger', To='SetTrue' if value == 'true' else 'SetFalse')
    trace = ET.SubElement(nodes, 'Trace', Name='source_state_proof', TypeT='bool')
    ET.SubElement(trace, 'Edge', From='source_state.State', To='Value')
    ET.SubElement(trace, 'Edge', From='probe_read.OnTrigger', To='Exec')
    ET.SubElement(trace, 'Edge', From='source_state.OnExec', To='Exec')
    ET.indent(root)
    blob = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    return blob, {'schema': 1, 'project': project, 'source_document': document,
                  'source_tree': tree_name, 'source_variable': declaration,
                  'source_expression': expression, 'preorder_index': index,
                  'source_execution_context': execution_context(tree['root'], index),
                  'source_provenance': model['behavior_documents'][document]['provenance'],
                  'target_sha256': hashlib.sha256(blob).hexdigest(),
                  'expected_default': default, 'expected_assigned': value,
                  'activation': 'manual diagnostic only; source guards are not executed',
                  'save_round_trip_validated': False, 'campaign_ready': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ir', type=Path, required=True)
    parser.add_argument('--document', required=True)
    parser.add_argument('--tree', required=True)
    parser.add_argument('--variable', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('Choose a new output folder')
    blob, report = convert_assignment(json.loads(args.ir.read_text(encoding='utf-8')),
                                      args.document, args.tree, args.variable, args.project)
    args.output.mkdir(parents=True)
    (args.output / (args.project + '.xml')).write_bytes(blob)
    (args.output / 'project-entities.xml').write_bytes(
        project_entities(args.project, 'Quests/' + args.project + '.xml'))
    (args.output / 'conversion.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
