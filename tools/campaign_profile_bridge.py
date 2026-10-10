"""Lower KCD1 EnableProfile into persistent native Skald layer ownership."""
import hashlib
import xml.etree.ElementTree as ET

from campaign_quest_bridge import UnsupportedOperation
from campaign_quest_graph import constant, edge, xml
from quest_import import literal


class ProfileBridge:
    def __init__(self, namespace, registered):
        self.namespace, self.registered, self.actions = namespace, registered, {}

    def lower(self, source):
        if source.get('children'): raise UnsupportedOperation('EnableProfile unexpectedly has children')
        if set(source['attributes']) - {'profileEnable', 'profileDisable'}:
            raise UnsupportedOperation('Unknown EnableProfile attributes')
        operations = []
        # Source attributes are literal profile names, not arbitrary Lua. Resolve
        # both first, so a failed conversion cannot register only half a switch.
        for field, active in [('profileEnable', True), ('profileDisable', False)]:
            name = literal(source['attributes'].get(field, ''))
            if not name: continue
            target = self.registered.get(name)
            if not isinstance(target, str) or not target:
                raise UnsupportedOperation('Profile dependencies need conversion: ' + name)
            operations.append((target, active))
        if len(operations) == 2 and operations[0][0] == operations[1][0]:
            raise UnsupportedOperation('Conflicting enable/disable of the same profile')
        node = ET.Element('Sequence')
        for target, active in operations:
            identity = hashlib.sha256(target.encode()).hexdigest()[:20]
            signal = self.namespace + '_profile_' + identity + ('_enable' if active else '_disable')
            self.actions[signal] = dict(profile=target, active=active, identity=identity)
            ET.SubElement(node, 'SendAIConceptSignal_' + signal)
        if not operations: return ET.Element('Success')
        return node

    def attach(self, files):
        if not self.actions: return files
        files = dict(files)
        path = 'Quests/' + self.namespace + '_quest_bridge.xml'
        root = ET.fromstring(files[path]); host = root.find('Skald/Module')
        assets = ET.SubElement(host, 'Assets'); nodes = host.find('Nodes')
        signal_path = 'Libs/Tables/ai/AIConceptSignalDatabase__' + self.namespace.lower() + '.xml'
        signals = ET.fromstring(files[signal_path]); rows = signals.find('AIConceptSignalDatabase')
        states = {}
        for name, action in sorted(self.actions.items()):
            identity = action['identity']
            if identity not in states:
                alias = 'profile_' + identity
                ET.SubElement(assets, 'ProfileAsset', Name=alias, AssetProfiles=action['profile'])
                state = ET.SubElement(nodes, 'State', Name=alias + '_active', TypeT='bool')
                constant(state, 'DefaultValue', 'false')
                layer = ET.SubElement(nodes, 'Layer', Name=alias + '_layer')
                ET.SubElement(layer, 'Asset', Name='Profiles', Alias=alias)
                edge(layer, alias + '_active.State', 'IsActive')
                states[identity] = state
            ET.SubElement(rows, 'AIConceptSignal', Name=name)
            trigger = ET.SubElement(nodes, 'AIConceptSignalTrigger', Name=name, NotificationName=name)
            constant(trigger, 'IsActive', 'true')
            edge(states[identity], name + '.OnNotification', 'SetTrue' if action['active'] else 'SetFalse')
        files[path], files[signal_path] = xml(root), xml(signals)
        return files
