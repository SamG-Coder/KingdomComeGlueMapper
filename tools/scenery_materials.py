"""Restore the rejected shadow-material mutation by matching source materials.

Matching ignores only the historical patch's exact edits and texture namespace;
geometry slots, textures, colors, surfaces, and parameters must still agree.
"""
from copy import deepcopy
from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET

from static_assets import convert_material_features
from upgrade_map import read, xml

PROXIES = {'shadow_proxy', 'shadowproxy'}


def signature(document):
    def item(element):
        attrs = dict(element.attrib)
        attrs.pop('GenMask', None)
        if element.tag == 'Texture' and attrs.get('File'):
            name = attrs['File'].replace('\\', '/').lower()
            if not name.startswith('$'):
                name = re.sub(r'^t\d+_', '', PurePosixPath(name).name)
                attrs['File'] = str(PurePosixPath(name).with_suffix('.dds'))
        if element.tag == 'Material':
            if attrs.get('Name','').lower() in PROXIES:
                attrs.pop('Shader',None);attrs.pop('StringGenMask',None)
                attrs['MtlFlags'] = str(int(attrs.get('MtlFlags','0')) & ~0x400)
            elif attrs.get('Shader','').lower() == 'vegetation':
                attrs['MtlFlags'] = str(int(attrs.get('MtlFlags','0')) & ~0x20)
        return element.tag,tuple(sorted(attrs.items())),tuple(item(c) for c in element)
    return item(document)


def restore(document, source):
    if signature(document) != signature(source):
        raise ValueError('Material identity does not match source')
    changed = 0
    for target,original in zip(document.iter('Material'),source.iter('Material')):
        if (original.get('Name','').lower() in PROXIES and original.get('Shader','').lower() == 'vegetation'
                and target.get('Shader','').lower() == 'nodraw'):
            for name in ('Shader','StringGenMask','GenMask','MtlFlags'):
                if name in original.attrib:target.set(name,original.get(name))
                else:target.attrib.pop(name,None)
            changed += 1
        elif original.get('Shader','').lower() == 'vegetation':
            flags = int(target.get('MtlFlags','0'))
            flags = (flags & ~0x20) | (int(original.get('MtlFlags','0')) & 0x20)
            target.set('MtlFlags',str(flags))
    return changed


def repair_materials(pack, index, shaders):
    candidates = {}
    for name in pack.namelist():
        if not name.lower().startswith(('glueveg/','gluebuild/')) or not name.lower().endswith('.mtl'):continue
        doc=ET.fromstring(pack.read(name))
        if any(m.get('Name','').lower() in PROXIES and m.get('Shader','').lower()=='nodraw' and m.get('GenMask')=='0'
               for m in doc.iter('Material')):
            candidates[name]=doc
    needed = {signature(d) for d in candidates.values()}; originals={}
    for name,(archive,entry) in index.items():
        if not name.endswith('.mtl'):continue
        payload=read(archive,entry.filename)
        if b'shadow_proxy' not in payload.lower() and b'shadowproxy' not in payload.lower():continue
        doc=ET.fromstring(payload)
        if not any(m.get('Name','').lower() in PROXIES and m.get('Shader','').lower()=='vegetation' for m in doc.iter('Material')):continue
        for m in doc.iter('Material'):
            shader=m.get('Shader','').lower()
            if shader in shaders[0] and shader in shaders[1]:convert_material_features(m,shaders[0][shader],shaders[1][shader])
            else:m.attrib.pop('GenMask',None)
        key=signature(doc)
        if key in needed:
            if key in originals and xml(originals[key][1])!=xml(doc):
                raise ValueError('Ambiguous source material restoration: '+name)
            originals[key]=(name,doc)
    changes={};report=[]
    for name,doc in candidates.items():
        key=signature(doc)
        if key not in originals:raise ValueError('Cannot identify patched scenery material: '+name)
        source_name,original=originals[key]
        count=restore(doc,original)
        if count:changes[name]=xml(doc);report.append(dict(material=name,source=source_name,restored_shadow_slots=count))
    return changes,report
