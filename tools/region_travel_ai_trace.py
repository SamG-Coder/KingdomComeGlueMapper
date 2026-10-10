"""Opt-in passive tracing of native command/reaction trees in an imported map.

Build from the installed native trees, keep their conditions and result status,
and fail closed if the expected insertion points change. No forced AI actions.
"""
import json
import xml.etree.ElementTree as ET
from upgrade_map import read, xml


def event(label, loaded=None):
    if label.startswith('human_host:') and loaded is None:
        # Native simulation objects can execute OnInit without a streamed body.
        # Query the engine's gate rather than inferring this from an entity ID.
        gate = ET.Element('IsLoadedGate', saveVersion='2')
        ET.SubElement(gate, 'Then', canSkip='1').append(event(label, True))
        ET.SubElement(gate, 'Else', canSkip='1').append(event(label, False))
        return gate
    return ET.Element('ExecuteLua', code=(
        'if GlueTravelAITrace then pcall(GlueTravelAITrace,entity,' +
        json.dumps(label) + (',' + str(loaded).lower() if loaded is not None else '') + ') end'))


def wrap(parent, child, label):
    """Log start/result; FuseBox explicitly propagates the original child status."""
    index = list(parent).index(child)
    parent.remove(child)
    seq = ET.Element('Sequence')
    seq.append(event(label + ':begin'))
    fuse = ET.SubElement(seq, 'FuseBox', StatusPropagation='Child', OneCleanup='false', saveVersion='2')
    ET.SubElement(fuse, 'Child', canSkip='1').append(child)
    ET.SubElement(fuse, 'OnSuccess', canSkip='1').append(event(label + ':success'))
    ET.SubElement(fuse, 'OnFail', canSkip='1').append(event(label + ':failure'))
    parent.insert(index, seq)


def resources(scripts, level):
    specs = {
        'AI/animal/basic/switch/animal_horseBrain.xml':
            ('switch_animal_horseBrain', 'Function_callInterrupt_animal_moveToPlayer', 'horse_command'),
        'AI/animal/basic/switch/callInterrupt_animal_moveToPlayer.xml':
            ('callInterrupt_animal_moveToPlayer', 'Function_crime_getRedkev', 'horse_host'),
        'AI/animal/basic/switch/interrupt_animal_moveToPlayer.xml':
            ('interrupt_animal_moveToPlayer', 'Move', 'horse_move'),
        'AI/npc/basic/switch/handleHitReaction.xml':
            ('switch_handleHitReaction', 'SendAIConceptSignal_hitReactionNotification', 'npc_hit'),
        'AI/npc/basic/switch/callInterrupt_flee.xml':
            ('callInterrupt_flee', 'Function_crime_getMrkev', 'npc_flee_host'),
        'AI/crime/getMrkev.xml': ('crime_getMrkev', 'GraphSearch', 'human_host'),
    }
    files = {}
    for path, (tree_name, tag, label) in specs.items():
        root = ET.fromstring(read(scripts, path))
        tree = root.find("BehaviorTree[@name='" + tree_name + "']/Root")
        if tree is None:
            raise ValueError('Native AI trace tree changed: ' + path)
        matches = [(p, c) for p in tree.iter() for c in p if c.tag == tag]
        expected = 3 if tag == 'Move' else 1
        if len(matches) != expected:
            raise ValueError('Native AI trace insertion point changed: ' + path)
        for parent, child in matches:
            wrap(parent, child, label)
        if tree_name == 'switch_animal_horseBrain':
            init = tree.find('OnInit')
            if init is not None:
                raise ValueError('Native horse brain initialization changed')
            init = ET.Element('OnInit', canSkip='1')
            init.append(event('horse_brain_ready'))
            tree.insert(0, init)
        files[path] = xml(root)
    # Loaded by the existing Player lifecycle hook. Separate counters per actor
    # and phase, with a hard session cap; no scans, timers, input hooks or moves.
    lua = '''
local glueTraceCounts,glueTraceTotal,glueHostTotal={},0,0
function GlueTravelAITrace(entity,phase,loaded)
    local map=tostring(System.GetCVar('sv_map')):lower():gsub('\\\\','/'):gsub('/+$',''):match('([^/]+)$')
    if map~=LEVEL then return end
    local host=phase:sub(1,11)=='human_host:'
    if (host and glueHostTotal>=40) or (not host and glueTraceTotal>=400) then return end
    local name=entity and entity:GetName() or 'none'
    local key=tostring(entity and entity.id)..':'..phase
    local count=(glueTraceCounts[key] or 0)+1
    if count>8 then return end
    glueTraceCounts[key]=count
    if host then glueHostTotal=glueHostTotal+1 else glueTraceTotal=glueTraceTotal+1 end
    local p=entity and entity:GetWorldPos() or {x=0,y=0,z=0}
    System.LogAlways(string.format('GLUE_AI_TRACE phase=%s actor=%s position=%.2f,%.2f,%.2f count=%d engine_loaded=%s',phase,name,p.x,p.y,p.z,count,tostring(loaded)))
end
System.LogAlways('GLUE_AI_TRACE installed; bounded native command/reaction tracing')
'''.replace('LEVEL', json.dumps(level))
    return files, lua.encode('utf-8')
