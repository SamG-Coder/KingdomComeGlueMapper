"""Convert legacy tree shadow proxies without changing mesh material indices."""


def convert_shadow_proxies(document):
    """Use visible foliage for shadows instead of a legacy duplicate canopy.

    Legacy tree CGFs include shadow_proxy subsets alongside their visible
    subsets, even in LOD0. This candidate addresses coarse proxy polygons
    appearing in the target camera pass; visual validation is pending. Keep every slot
    in place, hide only explicitly named vegetation shadow proxies, and allow
    the corresponding visible vegetation to cast shadows. This trades the
    cheap dedicated shadow mesh for correct visible geometry.
    """
    converted = 0
    for submaterials in document.iter('SubMaterials'):
        proxies = [m for m in submaterials
                   if m.get('Name', '').lower() in ('shadow_proxy', 'shadowproxy')
                   and m.get('Shader', '').lower() == 'vegetation']
        if not proxies:
            continue
        for material in submaterials:
            if material in proxies:
                material.set('Shader', 'Nodraw')
                material.set('GenMask', '0')
                material.set('StringGenMask', '')
                material.set('MtlFlags', str(int(material.get('MtlFlags', '0')) | 0x400))
                converted += 1
            elif material.get('Shader', '').lower() == 'vegetation':
                # MTL_FLAG_NOSHADOW. Preserve every other material flag.
                material.set('MtlFlags', str(int(material.get('MtlFlags', '0')) & ~0x20))
    return converted
