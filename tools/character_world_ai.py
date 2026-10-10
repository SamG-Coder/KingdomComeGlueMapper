"""Native region-wide interrupt hosts required by imported NPC brains."""
import copy
import xml.etree.ElementTree as ET


def register_interrupt_services(source, land_source, land, place, waiting):
    """Preserve the native land -> human/animal interrupt host contract.

    These are non-spatial service objects. Destination parking spots and guards
    must be linked separately; importing Trosecko's coordinates would be wrong.
    The native human host exposes attack/flee/watch/arrest interrupt behaviours.
    """
    by_id = {e.get('EntityId'): e for e in source}
    links = ET.SubElement(land, 'EntityLinks')
    services = {}
    omitted = []

    def connect(a, b, name):
        container = a.find('EntityLinks')
        if container is None: container = ET.SubElement(a, 'EntityLinks')
        ET.SubElement(container, 'Link', TargetId=b.get('EntityId'),
                      TargetGuid=b.get('EntityGuid'), Name=name)
        waiting.append((a.get('EntityGuid'), b.get('EntityGuid'), name))

    for name in ('mrkev', 'redkev'):
        found = [l for l in land_source.findall('EntityLinks/Link') if l.get('Name') == name]
        if len(found) != 1 or found[0].get('TargetId') not in by_id:
            raise ValueError('Missing native world interrupt service: ' + name)
        template = by_id[found[0].get('TargetId')]
        props = template.find('Properties')
        if props is None or not props.get('guidSmartObjectType'):
            raise ValueError('Native interrupt host has no smart-object type')
        # Use the native non-rendered holder, as the animal host already does.
        service = place(template.get('Name'), 'SmartObjectHolder')
        service.set('Pos', land.get('Pos'))
        service.append(copy.deepcopy(props))
        connect(land, service, name)
        services[name] = dict(name=service.get('Name'), guid=service.get('EntityGuid'),
                             type=props.get('guidSmartObjectType'))
        for child in template.findall('EntityLinks/Link'):
            if child.get('Name') == 'debugger':
                debug_template = by_id[child.get('TargetId')]
                debugger = place(debug_template.get('Name'), debug_template.get('EntityClass'))
                debugger.set('Pos', land.get('Pos'))
                if debug_template.find('Properties') is not None:
                    debugger.append(copy.deepcopy(debug_template.find('Properties')))
                connect(service, debugger, 'debugger')
            else:
                omitted.append(dict(service=name, label=child.get('Name'), source_id=child.get('TargetId')))
    return dict(services=services, spatial_dependencies_pending=omitted, runtime_verified=False)
