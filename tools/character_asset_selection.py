"""Select generated appearance assets from the final component definitions.

Source materials and textures staged for fitting/baking are intermediate inputs,
not runtime dependencies. Keep every authored LOD and every selected material
slot, and leave equipment and other namespaces outside this selection alone.
"""
from pathlib import PurePosixPath
import posixpath
import re
import xml.etree.ElementTree as ET

from character_lods import source_lods


def runtime_paths(appearance, paths, namespace):
    definition = appearance / ('Libs/Tables/Character/CharacterComponent__' + namespace + '.xml')
    if not definition.is_file():
        return paths  # Other callers can package a texture-only/additive stage.
    root = ET.parse(definition).getroot()
    component = root.find('CharacterComponents/Component')
    prefix = 'objects/characters/gluetravel/' + namespace.lower() + '/'
    if component is None or ('objects/characters/' + component.get('FilePath', '').rstrip('/') + '/').lower() != prefix:
        return paths  # Unknown/additive definitions retain the original writer contract.
    index = {}
    for path in paths:
        key = path.relative_to(appearance).as_posix().lower()
        if key in index:
            raise ValueError('Duplicate character staging path: ' + key)
        index[key] = path
    keep = set()
    def require(name):
        name = name.replace('\\', '/').lower()
        if '..' in PurePosixPath(name).parts or ':' in name:
            raise ValueError('Unsafe character asset reference: ' + name)
        if not name.startswith(prefix) or name not in index:
            raise ValueError('Missing generated character asset: ' + name)
        keep.add(name)
        return name
    materials = set()
    for element in component.findall('DerivedComponents/*/Elements/*'):
        if element.tag not in ('SkinElement', 'VClothElement'):
            raise ValueError('Unsupported generated character element: ' + element.tag)
        for attr in ('Model', 'SimBinding'):
            value = element.get(attr)
            if value:
                model = require(prefix + value)
                if model.endswith('.skin'):
                    for lod in source_lods(index, model).values():
                        require(lod)
        if not element.get('Material'):
            raise ValueError('Generated character element lacks explicit material')
        materials.add(require(prefix + element.get('Material')))
    if not materials:
        raise ValueError('Generated character has no selected materials')
    families = {}
    for name in index:
        match = re.fullmatch(r'(.+\.dds)(?:\.(?:a|\d+a?))?', name)
        if match: families.setdefault(match[1], []).append(name)
    for name in materials:
        for texture in ET.parse(index[name]).getroot().iter('Texture'):
            file = texture.get('File', '').replace('\\', '/').lower()
            if file.startswith('./'):
                file = posixpath.join(posixpath.dirname(name), file[2:])
            if file.startswith(prefix):
                base = require(file)
                keep.update(families.get(base, ()))
    return [path for key, path in index.items() if not key.startswith(prefix) or key in keep]
