"""Native region/layer inheritance shared by clothing conversion and repair."""


def region_layers(node, parents):
    """Resolve both rigid and simulated elements; neither is a different slot.

    An absent layer remains None if no ancestor declares it. Never infer a
    numeric layer from the KCD1 mesh name or from a neighbouring body region.
    """
    chain = []
    while node is not None:
        chain.append(node)
        node = parents.get(node)
    result = {}
    for ancestor in reversed(chain):
        for element in ancestor.findall('Elements/*'):
            if element.tag not in ('SkinElement', 'VClothElement'):
                continue
            region = element.get('EquipmentPart')
            if not region:
                continue
            if element.get('BodyLayerId') is not None:
                result[region] = element.get('BodyLayerId')
            else:
                result.setdefault(region, None)
    return result


def project_regions(regions, contract, primary='torso'):
    """Keep complete source geometry in the regions authored by the native role."""
    if primary not in contract:
        raise ValueError('Native role has no primary region: ' + primary)
    return [region if region in contract else primary for region in regions]


def source_mesh_regions(node, parents, equipment, archetype):
    """Resolve a source mesh even when the native role is material-only.

    Some KCD2 roles have Features but no visual Elements. Their RPG occupancy
    still defines the region and layer for a converted standalone source mesh.
    Never replace an authored visual contract with RPG occupancy (a caparison
    occupies several slots but its native mesh is attached to horse_torso).
    """
    contract = region_layers(node, parents)
    if contract:
        return contract
    if not node.findall('Features/Feature'):
        raise ValueError('Native role has no mesh or material-feature contract: ' + node.get('Name', ''))
    for region, layer in equipment.layers.get(archetype, ()):
        if region in contract and contract[region] != layer:
            raise ValueError('Ambiguous native equipment layer: ' + node.get('Name', '') + '/' + region)
        contract[region] = layer
    if not contract:
        raise ValueError('Native material-only role has no equipment occupancy: ' + node.get('Name', ''))
    return contract


def source_mesh_region(requested, contract):
    """Use the authored single attachment when a source category names another slot."""
    if requested in contract:
        return requested
    if len(contract) == 1:
        return next(iter(contract))
    raise ValueError('Source garment region is absent or ambiguous in native role: ' + requested)
