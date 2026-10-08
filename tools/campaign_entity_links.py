"""KCD2 persistent entity identifiers and exported AI link-network records.

Entity GUID text is low32-mid16-high16, whereas KCD1's text is a hexadecimal
uint64. These representations must not be confused when binding binary areas.
The native AI link network loads waitinglinks.xml, independently of CryEngine's
EntityLinks records in objects_mission0.xml.
"""
import re
import xml.etree.ElementTree as ET


def native_guid(value):
    if not isinstance(value, int) or not 0 < value <= 0xffffffffffffffff:
        raise ValueError('Expected a nonzero uint64 entity GUID')
    return f'{value & 0xffffffff:08x}-{(value >> 32) & 0xffff:04x}-{value >> 48:04x}'


def guid_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}', value):
        raise ValueError('Expected a native 8-4-4 entity GUID')
    a, b, c = (int(v, 16) for v in value.split('-'))
    result = a | b << 32 | c << 48
    if not result: raise ValueError('Zero entity GUID')
    return result


def append_links(data, links):
    """Merge explicit (source GUID, target GUID, definition) links, deduplicated.

    Existing level links are retained verbatim. Only the supplied records are
    converted; callers must resolve source IDs and profile/layer dependencies.
    """
    root = ET.fromstring(data) if data else ET.Element('StaticLinksInfo', version='1')
    if root.tag != 'StaticLinksInfo' or root.get('version') != '1':
        raise ValueError('Unsupported native link-network format')
    waiting = root.find('WaitingLinks')
    if waiting is None: waiting = ET.SubElement(root, 'WaitingLinks')
    index = {(guid_value(n.get('SourceId')), guid_value(n.get('TargetId'))): n
             for n in waiting.findall('WaitingLink')}
    for source, target, definition in links:
        key = (guid_value(source), guid_value(target))
        if not isinstance(definition, str) or not definition.strip():
            raise ValueError('An explicit AI link definition is required')
        if key not in index:
            index[key] = ET.SubElement(waiting, 'WaitingLink', SourceId=native_guid(key[0]),
                                      TargetId=native_guid(key[1]))
        node = index[key]
        if definition not in [n.text for n in node.findall('LinkDefinition')]:
            ET.SubElement(node, 'LinkDefinition').text = definition
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
