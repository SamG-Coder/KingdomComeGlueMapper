"""Private native punishment controller with destination-owned regional bindings."""
import copy
import posixpath
import xml.etree.ElementTree as E

from upgrade_map import read, xml
from world_gameplay_basis import NativeGraphs, NATIVE, SYSTEMS, package_dependencies

PUNISHMENT = 'quests/final/barbora/open_world/nextnextgenpunishment.xml'
LOCATION_AI = 'AI/crime/getPunishmentLocationGUID.xml'
LINK_TYPES = 'AI/LinkTagDefinitions.xml'
LOCATION_LINK = 'gm_punishment_location'


def location_link_type(blob):
    """Declare the string payload before the engine loads regional links.

    An undeclared tag can still appear in the graph, but its bracket payload
    does not acquire the string contract required by LinkTagFilter.Data.
    Preserve all native and previously imported tag declarations.
    """
    root = E.fromstring(blob)
    if root.tag != 'LinkTagDefinitions' or root.get('version') != '1':
        raise ValueError('Unsupported AI link declaration schema')
    matches = [e for e in root if e.get('name', '').lower() == LOCATION_LINK]
    expected = dict(name=LOCATION_LINK, type='string', persistent='0')
    if matches:
        if len(matches) != 1 or matches[0].attrib != expected:
            raise ValueError('Conflicting punishment location link declaration')
        return blob
    E.SubElement(root, 'LinkTag', expected)
    return xml(root)


def location_adapter(blob):
    """Prefer a typed destination-area location link; retain native fallback.

    The same native function still works in unmodified KCD2 regions. No native
    enum value or location GUID is repurposed globally.
    """
    root = E.fromstring(blob)
    tree = root.find("BehaviorTree[@name='crime_getPunishmentLocationGUID']")
    if tree is None: raise ValueError('Native punishment location function changed')
    variables = tree.find('Variables')
    for name, typ in [('gm_punishmentArea','_wuid'), ('gm_punishmentLocation','enum:crime_punishmentLocation')]:
        if variables.find("Variable[@name='"+name+"']") is not None: raise ValueError('Crime adapter already present')
        E.SubElement(variables,'Variable',name=name,type=typ,values='',isPersistent='0',form='single')
    behavior = tree.find('Root/Behavior')
    if behavior is None or len(behavior) != 1 or behavior[0].tag != 'Switch':
        raise ValueError('Native punishment location switch changed')
    fallback = behavior[0]; behavior.remove(fallback)
    selector = E.SubElement(behavior,'Selector')
    sequence = E.SubElement(selector,'Sequence')
    E.SubElement(sequence,'Function_crime_getPunishmentArea',
        punishmentArea='$gm_punishmentArea',punishmentLocation='$gm_punishmentLocation')
    search = E.SubElement(sequence,'GraphSearch', Origin='$gm_punishmentArea', Borders='', depth='1',
        selection='true', SearchPattern='Depth', EdgePruning="'parent,inventory'", AllowedEdges='',
        SubGraph='', AllowSubtraph='false', includeOrigin='false', excludeOrigin='false',
        failOnEmpty='true', SetOperationChoice='Intersection|And', errorOnEmpty='false',
        shortCircuit='true', skipTraversed='true')
    E.SubElement(search,'LinkTagFilter',tag="'"+LOCATION_LINK+"'",prune='true',negprune='unknown',
                 Parent='',Child='',Data='$locationGUID')
    selector.append(fallback)
    # Editor-only mirrors are not executed and would describe the old tree.
    for section in ('EditorData','ForestContainer'):
        child=tree.find(section)
        if child is not None: tree.remove(child)
    return xml(root)


def prepare(entry_blob, wrapper_blob, entry_path, wrapper_path, scripts, faction):
    if not faction or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for c in faction):
        raise ValueError('Invalid destination punishment faction')
    native = NativeGraphs(scripts)
    original, project = native.get(NATIVE), native.get('quests/final/barbora.xml')
    wrapper = E.fromstring(wrapper_blob); host = wrapper.find('Skald')[0]
    nodes = host.find('Nodes')
    if {n.tag for n in nodes} != set(SYSTEMS) | {'utils'}:
        raise ValueError('Unexpected world controller registrations; explicit migration required')
    for n in list(nodes):
        if n.tag == 'utils': nodes.remove(n)
    definitions = host.find('Definitions'); definitions.clear()
    # Repackage from the shipped native definitions. Previously packaged paths
    # are private output names, not paths inside the source Scripts archive.
    for d in original.findall('Definitions/Definition'):
        path = posixpath.normpath(posixpath.join(posixpath.dirname(NATIVE),d.get('File')))
        if native.get(path).get('Name') in (*SYSTEMS, 'nextnextgenpunishment'):
            E.SubElement(definitions,'Definition',File=posixpath.relpath(path, posixpath.dirname(wrapper_path).lower()))
    E.SubElement(E.SubElement(nodes,'nextnextgenpunishment',Name='nextnextgenpunishment'),
                 'Constant',Name='currentlevel',Value='Trosecko')
    assets = host.find('Assets')
    for a in original.findall('Assets/*'):
        if a.get('Name','').startswith('punishment_') and assets.find("*[@Name='"+a.get('Name')+"']") is None:
            assets.append(copy.deepcopy(a))
    if assets.find("*[@Name='land']") is None:
        land = project.find("Assets/*[@Name='land']")
        if land is None: raise ValueError('Native land asset contract missing')
        assets.append(copy.deepcopy(land))
    graphs = package_dependencies({entry_path:entry_blob,wrapper_path:xml(wrapper)}, entry_path,wrapper_path,scripts)
    # Type declarations inherited from the original Barbora project must be
    # present in the private host, including nested library dependencies.
    used = {v for b in graphs.values() for e in E.fromstring(b).iter()
            for k,v in e.attrib.items() if k in ('Type','TypeT')}
    host = E.fromstring(graphs[wrapper_path]); gameplay=host.find('Skald')[0]
    types = gameplay.find('Types')
    if types is None: types=E.SubElement(gameplay,'Types')
    for t in project.findall('Types/Type'):
        if t.get('TypeName') in used and types.find("Type[@TypeName='"+t.get('TypeName')+"']") is None:
            types.append(copy.deepcopy(t))
    graphs[wrapper_path]=xml(host)
    adapted = []
    for path, blob in list(graphs.items()):
        root = E.fromstring(blob); module=root.find('Skald')[0]
        if module.get('Name') != 'playpunishment_cutscenebuffsmonolog': continue
        switches = [s for s in module.findall('Nodes/Switch') if s.get('ValueTypes')=='wh::rpgmodule::FactionID']
        if len(switches)!=1: raise ValueError('Native branding reputation contract changed')
        for constant in switches[0].findall('Constant'): constant.set('Value',faction)
        graphs[path]=xml(root); adapted.append(path)
    if len(adapted)!=1: raise ValueError('Missing unique native branding reputation controller')
    graphs[LOCATION_AI]=location_adapter(read(scripts,native.names[LOCATION_AI.lower()]))
    return graphs, dict(faction=faction,regional_reputation_graph=adapted[0],
        local_location_ai=LOCATION_AI,native_location_fallback_preserved=True)
