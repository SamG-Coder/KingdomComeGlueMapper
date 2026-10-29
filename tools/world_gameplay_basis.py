"""Register native open-world gameplay and its destination-owned bindings.

Package the selected native dependency closure beside its host. Archive lookups
must not depend on resolving parent-directory segments across mounted paks.
"""
import copy
import hashlib
import posixpath
import xml.etree.ElementTree as ET

from campaign_entity_links import append_links
from upgrade_map import read, xml

NATIVE = 'quests/final/barbora/open_world.xml'
# These have no campaign input ports or inherited regional asset dependencies.
# Expansion is deliberate: a dependency audit is not proof of runtime parity.
SYSTEMS = (
    'combat', 'boj_a_zraneni', 'reakce_na_stav_hrace', 'reputace',
    'spanek_npc', 'pozdravy_a_rozlouceni_mezi_npc',
    'pozdravy_na_zacatku_dialogu', 'rozlouceni_na_konci_dialogu',
    'reakce_pratelskych_nepratel', 'statistical_perks', 'perky',
    'requestplayerholsterweapon', 'generic_action_prompts',
    'battle_barks', 'punishment_barks', 'shop', 'repair', 'sharpening',
)


def validate_dependency_scopes(graphs, wrapper_path, native_definitions):
    """Resolve custom node types through their packaged lexical scopes.

    File presence alone cannot detect missing inherited types. Built-in node
    types come from this game's definitions rather than an assumed allowlist.
    """
    definitions = ET.fromstring(native_definitions)
    builtin = {n.get('TypeName') for n in definitions.findall('Nodes/Definition')} | {'Input', 'Output'}
    prefix = wrapper_path.rsplit('/', 1)[0] + '/'
    modules = {p: ET.fromstring(b).find('Skald')[0] for p, b in graphs.items() if p.startswith(prefix)}
    visited = set()

    def local(path):
        result = {}
        for node in modules[path].findall('Definitions/Definition'):
            child = path.rsplit('/', 1)[0] + '/' + node.get('File')
            if child not in modules: raise ValueError('Missing literal dependency: ' + child)
            result[modules[child].get('Name')] = child
        return result

    def walk(path, parents=(), stack=()):
        if path in stack: return
        visited.add(path)
        children = local(path)
        scope = (children, *parents)
        for node in modules[path].findall('Nodes/*'):
            if node.tag in builtin: continue
            namespace = node.get('Namespace', '')
            contexts = scope
            for part in namespace.split('.') if namespace else ():
                # Native modules can have a local "utils" and also refer to
                # the project-qualified utils.crime.punishment. Resolve the
                # complete namespace, not only its first matching component.
                contexts = tuple(local(s[part]) for s in contexts if part in s)
                if not contexts: raise ValueError(f'Unresolved namespace {namespace} in {path}')
            if not any(node.tag in s for s in contexts):
                raise ValueError(f'Unresolved node type {namespace}.{node.tag} in {path}')
        for child in children.values(): walk(child, scope, (*stack, path))

    walk(wrapper_path)
    return len(visited)


def package_dependencies(graphs, entry_path, wrapper_path, scripts):
    """Relocate the native closure with literal, downward-only Definition paths.

    The runtime XML reader does not normalize the chained ../ paths that Python's
    dependency audit accepts. Keep copied native graphs private to this mod.
    Also used when migrating an already-built package.
    """
    graphs = dict(graphs)
    entry = ET.fromstring(graphs[entry_path])
    wrapper = ET.fromstring(graphs[wrapper_path])
    for section, attr, value in (('Definitions', 'File', 'native/utils.xml'), ('Nodes', 'Name', 'utils')):
        parent = wrapper.find('Skald')[0].find(section)
        if parent is not None:
            for child in list(parent):
                if child.get(attr) == value: parent.remove(child)
    destination = posixpath.dirname(entry_path) + '/world/open_world.xml'
    names = {n.lower(): n for n in scripts.namelist()}
    copied = {}
    requested = set()
    libraries = {}
    library_symbols = {}

    def library_shell(namespace):
        if namespace not in libraries:
            path = 'quests/final/barbora/' + namespace.replace('.', '/') + '.xml'
            if path not in names:
                raise ValueError('Missing native namespace: ' + namespace)
            shell = ET.fromstring(read(scripts, names[path]))
            target = shell.find('Skald')[0]
            for section in ('Definitions', 'Nodes'):
                node = target.find(section)
                if node is not None: target.remove(node)
                ET.SubElement(target, section)
            libraries[namespace] = shell
        return libraries[namespace].find('Skald')[0]

    def symbols(namespace):
        if namespace not in library_symbols:
            path = 'quests/final/barbora/' + namespace.replace('.', '/') + '.xml'
            if path not in names:
                raise ValueError('Missing native namespace: ' + namespace)
            host = ET.fromstring(read(scripts, names[path])).find('Skald')[0]
            result = {}
            for definition in host.findall('Definitions/Definition'):
                child = posixpath.normpath(posixpath.join(posixpath.dirname(path), definition.get('File'))).lower()
                module = ET.fromstring(read(scripts, names[child])).find('Skald')[0]
                symbol = module.get('Name')
                if symbol in result: raise ValueError('Ambiguous native library symbol: ' + symbol)
                result[symbol] = child
            library_symbols[namespace] = result
        return library_symbols[namespace]

    def utility(namespace, symbol):
        key = (namespace, symbol)
        if key in requested:
            return
        requested.add(key)
        path = 'quests/final/barbora/' + namespace.replace('.', '/') + '.xml'
        if path not in names:
            raise ValueError('Missing native namespace: ' + namespace)
        source = ET.fromstring(read(scripts, names[path]))
        host = source.find('Skald')[0]
        target = library_shell(namespace)
        child = symbols(namespace).get(symbol)
        if child is None:
            raise ValueError(f'Cannot resolve native namespace symbol {namespace}.{symbol}')
        filename = copy_graph(child, namespace)
        ET.SubElement(target.find('Definitions'), 'Definition', File=filename)
        instances = [n for n in host.findall('Nodes/*') if n.tag == symbol]
        if not instances:
            raise ValueError(f'Missing native library instance {namespace}.{symbol}')
        target.find('Nodes').extend(copy.deepcopy(instances))

    def copy_graph(path, inherited_namespace=None):
        path = posixpath.normpath(path).lower()
        filename = hashlib.sha256(path.encode()).hexdigest()[:24] + '.xml'
        if path in copied:
            return filename
        if path not in names:
            raise ValueError('Missing packaged native gameplay dependency: ' + path)
        copied[path] = filename
        root = ET.fromstring(read(scripts, names[path]))
        local = {}
        for definition in root.findall('.//Definitions/Definition'):
            child = posixpath.normpath(posixpath.join(posixpath.dirname(path), definition.get('File'))).lower()
            module = ET.fromstring(read(scripts, names[child])).find('Skald')[0]
            local[module.get('Name')] = child
        for node in root.findall('.//Nodes/*'):
            namespace = node.get('Namespace', '')
            if namespace.startswith('utils.'):
                utility(namespace, node.tag)
            elif namespace and inherited_namespace and namespace.split('.')[0] not in local:
                # Resolve relative namespaces from the enclosing library out
                # to utils, preserving lexical precedence at arbitrary depth.
                parts = inherited_namespace.split('.')
                for size in range(len(parts), 0, -1):
                    candidate = '.'.join(parts[:size]) + '.' + namespace
                    if ('quests/final/barbora/' + candidate.replace('.', '/') + '.xml') in names:
                        utility(candidate, node.tag)
                        break
            elif not namespace and inherited_namespace and node.tag not in local:
                # An empty Namespace means lexical lookup, not no dependency.
                # Nested libraries can also call helpers in their parents.
                parts = inherited_namespace.split('.')
                for size in range(len(parts), 1, -1):
                    candidate = '.'.join(parts[:size])
                    if node.tag in symbols(candidate):
                        utility(candidate, node.tag)
                        break
        for definition in root.findall('.//Definitions/Definition'):
            child = posixpath.normpath(posixpath.join(posixpath.dirname(path), definition.get('File')))
            definition.set('File', copy_graph(child, inherited_namespace))
        graphs[posixpath.dirname(destination) + '/native/' + filename] = xml(root)
        return filename

    for definition in wrapper.findall('.//Definitions/Definition'):
        path = posixpath.normpath(posixpath.join(posixpath.dirname(wrapper_path), definition.get('File')))
        # Recipe 13 already has hashed private includes. Recover their source
        # identity so migration can add namespace dependencies as well.
        if path.lower() not in names:
            stem = posixpath.basename(path)
            matches = [n for n in names if hashlib.sha256(n.encode()).hexdigest()[:24] + '.xml' == stem]
            if len(matches) != 1: raise ValueError('Unknown packaged graph: ' + path)
            path = matches[0]
        definition.set('File', 'native/' + copy_graph(path))
    if libraries:
        # A nested namespace needs each intervening library instance. Copy its
        # native declaration, but only include the requested branch; copying the
        # whole crime library would activate unrelated regional controllers.
        for namespace in sorted(tuple(libraries), key=lambda n: -n.count('.')):
            child = namespace
            while child.count('.') > 1:
                parent, symbol = child.rsplit('.', 1)
                target = library_shell(parent)
                filename = 'utils_' + child[len('utils.'):] + '.xml'
                if not any(d.get('File') == filename for d in target.findall('Definitions/Definition')):
                    ET.SubElement(target.find('Definitions'), 'Definition', File=filename)
                    source = ET.fromstring(read(scripts, names['quests/final/barbora/' + parent.replace('.', '/') + '.xml']))
                    instances = [n for n in source.findall('.//Skald/*/Nodes/*') if n.tag == symbol]
                    if len(instances) != 1:
                        raise ValueError('Missing or ambiguous native library instance: ' + child)
                    target.find('Nodes').append(copy.deepcopy(instances[0]))
                child = parent
        root = ET.Element('Database', Name='brambora')
        module = ET.SubElement(ET.SubElement(root, 'Skald'), 'Module', Name='utils', VoiceNamespace='true')
        definitions = ET.SubElement(module, 'Definitions')
        nodes = ET.SubElement(module, 'Nodes')
        for namespace, library in sorted(libraries.items()):
            name = namespace[len('utils.'):]
            filename = 'utils_' + name + '.xml'
            if namespace.count('.') == 1:
                ET.SubElement(definitions, 'Definition', File=filename)
                ET.SubElement(nodes, name, Name=name)
            graphs[posixpath.dirname(destination) + '/native/' + filename] = xml(library)
        graphs[posixpath.dirname(destination) + '/native/utils.xml'] = xml(root)
        host = wrapper.find('Skald')[0]
        ET.SubElement(host.find('Definitions'), 'Definition', File='native/utils.xml')
        ET.SubElement(host.find('Nodes'), 'utils', Name='utils')
    found = False
    for definition in entry.findall('.//Definitions/Definition'):
        path = posixpath.normpath(posixpath.join(posixpath.dirname(entry_path), definition.get('File')))
        if path.lower() == wrapper_path.lower():
            definition.set('File', 'world/open_world.xml')
            found = True
    if not found:
        raise ValueError('Missing world gameplay Definition in entry graph')
    if wrapper_path != destination:
        del graphs[wrapper_path]
    graphs[entry_path], graphs[destination] = xml(entry), xml(wrapper)
    return graphs


class NativeGraphs:
    def __init__(self, archive):
        self.archive = archive
        self.names = {n.lower(): n for n in archive.namelist()}
        self.cache = {}

    def get(self, path):
        path = path.lower()
        if path not in self.cache:
            if path not in self.names:
                raise ValueError('Missing native gameplay dependency: ' + path)
            payload = read(self.archive, self.names[path])
            root = ET.fromstring(payload)
            modules = list(root.find('Skald'))
            if len(modules) != 1:
                raise ValueError('Expected one Skald module: ' + path)
            self.cache[path] = (modules[0], hashlib.sha256(payload).hexdigest())
        return self.cache[path][0]

    def audit(self, path, inherited, stack=()):
        path = path.lower()
        if path in stack:
            raise ValueError('Cyclic native Definition dependency: ' + path)
        module = self.get(path)
        scope = set(inherited) | {a.get('Name') for a in module.findall('Assets/*')}
        missing = {a.get('Alias') for a in module.iter('Asset')} - scope
        paths = {path}
        for definition in module.findall('Definitions/Definition'):
            child = posixpath.normpath(posixpath.join(posixpath.dirname(path), definition.get('File'))).lower()
            child_paths, child_missing = self.audit(child, scope, (*stack, path))
            paths |= child_paths
            missing |= child_missing
        return paths, missing


def apply(files, graphs, graph_path, level, native_level, scripts, identity):
    files, graphs = dict(files), dict(graphs)
    native = NativeGraphs(scripts)
    original = native.get(NATIVE)
    project = native.get('quests/final/barbora.xml')
    parent = graph_path.rsplit('/', 1)[0]
    entry_path = parent + '/entry/' + level + '.xml'
    wrapper_path = parent + '/world/open_world.xml'
    entry = ET.fromstring(graphs[entry_path])
    host = entry.find('Skald/Level')
    if host is None or host.find("Nodes/*[@Name='open_world']") is not None:
        raise ValueError('Expected destination Level without gameplay basis')
    wrapper = ET.Element('Database', Name='brambora')
    gameplay = ET.SubElement(ET.SubElement(wrapper, 'Skald'), 'Gameplay',
                            Name='open_world', VoiceNamespace='true', HasteNamespace='true')
    assets = ET.SubElement(gameplay, 'Assets')
    for alias in ('player', 'player_any'):
        template = project.find("Assets/SoulAsset[@Name='" + alias + "']")
        if template is None or not template.get('SharedSoulGuids'):
            raise ValueError('Native player alias missing: ' + alias)
        assets.append(copy.deepcopy(template))
    assets.append(copy.deepcopy(original.find("Assets/SmartObjectAsset[@Name='openworld']")))
    definitions = ET.SubElement(gameplay, 'Definitions')
    nodes = ET.SubElement(gameplay, 'Nodes')
    inventory, selected_paths = [], set()
    for definition in original.findall('Definitions/Definition'):
        path = posixpath.normpath(posixpath.join(posixpath.dirname(NATIVE), definition.get('File'))).lower()
        module = native.get(path)
        name = module.get('Name')
        paths, missing = native.audit(path, {'player', 'player_any', 'openworld'})
        ports = [p.get('Name') for p in module.findall('Ports/Port') if p.get('Direction') == 'In']
        selected = name in SYSTEMS
        if selected:
            if missing or ports:
                raise ValueError(f'Unadapted native dependencies for {name}: assets={sorted(missing)}, ports={ports}')
            instances = [n for n in original.findall('Nodes/*') if n.tag == name]
            if len(instances) != 1 or list(instances[0]):
                raise ValueError('Native system requires parent wiring: ' + name)
            ET.SubElement(definitions, 'Definition', File=posixpath.relpath(path, posixpath.dirname(wrapper_path)))
            nodes.append(copy.deepcopy(instances[0]))
            selected_paths |= paths
        inventory.append(dict(name=name, registered=selected, input_ports=ports,
                              inherited_assets=sorted(missing), graph_count=len(paths), runtime_verified=False))
    if {r['name'] for r in inventory if r['registered']} != set(SYSTEMS):
        raise ValueError('Native baseline system set changed')
    # Copy only inherited type declarations actually used by selected graphs.
    used_types = {v for p in selected_paths for e in native.get(p).iter()
                  for k, v in e.attrib.items() if k in ('Type', 'TypeT')}
    types = [t for t in project.findall('Types/Type') if t.get('TypeName') in used_types]
    if types:
        section = ET.SubElement(gameplay, 'Types')
        section.extend(copy.deepcopy(types))
    section = host.find('Definitions')
    if section is None: section = ET.SubElement(host, 'Definitions')
    ET.SubElement(section, 'Definition', File='../world/open_world.xml')
    ET.SubElement(host.find('Nodes'), 'open_world', Name='open_world')

    mission = ET.fromstring(files['objects_mission0.xml'])
    source = ET.fromstring(read(native_level, 'objects_mission0.xml'))
    def one(root, name):
        matches = [e for e in root if e.get('Name') == name]
        if len(matches) != 1: raise ValueError('Expected one world entity: ' + name)
        return matches[0]
    holder, land = one(mission, level), one(mission, 'sa_land')
    def place(name):
        if any(e.get('Name') == name for e in mission):
            raise ValueError('Gameplay service already present: ' + name)
        entity = copy.deepcopy(one(source, name))
        for child in list(entity):
            if child.tag != 'Properties': entity.remove(child)
        for key in ('Layer', 'EditorLayer', 'ParentId'): entity.attrib.pop(key, None)
        entity.set('EntityGuid', identity('world-gameplay/' + name))
        if any(e.get('EntityGuid') == entity.get('EntityGuid') for e in mission):
            raise ValueError('Gameplay service identity collision: ' + name)
        entity.set('EntityId', str(max(int(e.get('EntityId', '0')) for e in mission) + 1))
        entity.set('Pos', holder.get('Pos'))
        mission.append(entity)
        return entity
    world, surrender = place('open_world'), place('surrenderHolder')
    links = [(holder, world, 'module'), (world, world, "asset['openworld']"),
             (world, surrender, "asset['surrenderDH']"), (land, world, 'punishment_openworldQSO')]
    for a, b, label in links:
        section = a.find('EntityLinks')
        if section is None: section = ET.SubElement(a, 'EntityLinks')
        ET.SubElement(section, 'Link', Name=label, TargetId=b.get('EntityId'), TargetGuid=b.get('EntityGuid'))
    files['objects_mission0.xml'] = xml(mission)
    files['waitinglinks.xml'] = append_links(files['waitinglinks.xml'],
        [(a.get('EntityGuid'), b.get('EntityGuid'), label) for a, b, label in links])
    graphs[entry_path], graphs[wrapper_path] = xml(entry), xml(wrapper)
    graphs = package_dependencies(graphs, entry_path, wrapper_path, scripts)
    return files, graphs, dict(systems=inventory, native_dependencies={p: native.cache[p][1] for p in sorted(selected_paths)},
        service_bindings=[dict(source=a.get('Name'), target=b.get('Name'), label=l) for a,b,l in links],
        runtime_verified=False, full_trosky_parity=False)
