"""Resolve component inheritance and native equipment contracts for the lab."""
import base64
from functools import lru_cache
import hashlib
import json
import posixpath
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from character_person import native_xml
from character_skin_upgrade import CompiledSkin
from clothing_coverage import normals
from upgrade_map import read
from .assets import Assets, key


def resolved_nodes(document, roles_only=False):
    """Yield named leaves with inherited attributes and per-part/layer overrides."""
    def walk(node, attrs, elements, path, family):
        attrs = dict(attrs, **node.attrib)
        if roles_only and attrs.get('Race','Human') != 'Human': return
        path = node.get('FilePath', path)
        if node.tag == 'Component' and node.get('FilePath'): family = node.get('Name', family)
        elements = {k:dict(v) for k,v in elements.items()}
        for element in node.findall('Elements/*'):
            if element.tag not in ('SkinElement', 'VClothElement'): continue
            slot = (element.get('EquipmentPart'), element.get('BodyLayerId'))
            previous = [k for k in elements if k[0] == slot[0]]
            if slot[1] is None and len(previous) == 1: slot = previous[0]
            elif slot[1] is None and len(previous) > 1: raise ValueError('Ambiguous inherited layer: '+node.get('Name',''))
            if slot[1] is not None and slot not in elements and (slot[0],None) in elements:
                elements[slot]=elements.pop((slot[0],None))
            merged = dict(elements.get(slot, {}), **element.attrib, element_type=element.tag)
            for field in ('Model','Material'):
                if field in element.attrib:
                    value = element.get(field)
                    merged[field+'Resolved'] = key(posixpath.normpath('objects/characters/'+path.rstrip('/')+'/'+value)) if value else ''
            elements[slot] = merged
        if node.tag in ('Body','Head','Hair','Beard','Clothing') and node.get('Name'):
            yield dict(id=node.get('Name'), kind=node.tag, family=family, gender=attrs.get('Gender','Unknown'),
                       attributes=attrs, elements=list(elements.values()))
            if roles_only and node.get('Name') == attrs.get('ArmorType'): return
        for child in node.findall('DerivedComponents/*'):
            yield from walk(child, attrs, elements, path, family)
        if node.tag in ('database','CharacterComponents'):
            for child in node: yield from walk(child, attrs, elements, path, family)
    yield from walk(document, {}, {}, '', '')


class Catalog:
    def __init__(self, package, target=None, overlay=None):
        self.assets = Assets(package, overlay)
        self.parts, self.presets, self.groups, self.souls, self.errors = {}, {}, {}, {}, []
        self.private_groups = set()
        self.nonvisual_parts = set()
        self.native_roles, self.rule_evidence, self.layer_order = {}, [], {}
        self.equipment = None
        if target:
            path = Path(target)/'Data/Tables.pak'
            with zipfile.ZipFile(path) as archive:
                from clothing_equipment import NativeEquipment
                self.equipment = NativeEquipment.from_archive(archive)
                for table in ('armor_archetype', 'armor_archetype2body_subpart', 'body_subpart', 'equipment_part'):
                    member = 'Libs/Tables/item/' + table + '.xml'
                    self.rule_evidence.append(dict(file=str(path), member=member,
                        sha256=hashlib.sha256(read(archive, member)).hexdigest()))
                name = 'Libs/Tables/Character/CharacterComponent.xml'
                data = read(archive, name)
                for part in resolved_nodes(native_xml(data), roles_only=True):
                    attrs = part['attributes']
                    role = attrs.get('ArmorType')
                    if role and part['id'] == role:
                        self.native_roles[role] = dict(archetype=attrs.get('ArmorArchetypeId'), gender=part['gender'],
                            layers=sorted({(e.get('EquipmentPart'), e.get('BodyLayerId')) for e in part['elements']}, key=str))
                self.rule_evidence.append(dict(file=str(path), member=name, sha256=hashlib.sha256(data).hexdigest()))
                name = 'Libs/Tables/Character/ClothingHidingGroup.xml'
                group_data = read(archive, name)
                self._groups(native_xml(group_data))
                self.rule_evidence.append(dict(file=str(path), member=name, sha256=hashlib.sha256(group_data).hexdigest()))
                name = 'Libs/Tables/item/body_layer.xml'
                data = read(archive, name)
                for row in native_xml(data).iter('body_layer'):
                    identity = row.get('body_layer_id')
                    if identity in self.layer_order:
                        raise ValueError('Duplicate native body-layer identity: '+identity)
                    self.layer_order[identity] = int(row.attrib['layer_order'])
                if not self.layer_order:
                    raise ValueError('Native body-layer order table is empty')
                self.rule_evidence.append(dict(file=str(path), member=name, sha256=hashlib.sha256(data).hexdigest()))
        names = list(self.assets.index)
        imported_people = {}
        for name in names:
            if not name.startswith('libs/gluemapper/personimport/') or not name.endswith('.json'):
                continue
            try:
                receipt = json.loads(self.assets.read(name))
                namespace, person = receipt.get('namespace'), receipt.get('person')
                if not isinstance(namespace,str) or not isinstance(person,str) or not person:
                    raise ValueError('Missing imported appearance identity')
                if name != 'libs/gluemapper/personimport/'+namespace.lower()+'.json':
                    raise ValueError('Imported appearance receipt name differs')
                if namespace in imported_people and imported_people[namespace] != person:
                    raise ValueError('Conflicting imported appearance identity')
                imported_people[namespace] = person
            except (ValueError, OSError) as exc:
                self.errors.append(dict(asset=name,error=str(exc)))
        for name in names:
            if not name.startswith('libs/tables/') or not name.endswith('.xml'): continue
            try:
                if '/soul__' in name:
                    for soul in ET.fromstring(self.assets.read(name)).iter('soul'):
                        self.souls[soul.get('soul_id','').replace('-','')] = dict(soul.attrib)
                elif '/clothinghidinggroup__' in name:
                    doc = ET.fromstring(self.assets.read(name))
                    self.private_groups.update(g.get('Name') for g in doc.iter('HidingGroup'))
                    self._groups(doc)
                elif '/charactercomponent__' in name:
                    for part in resolved_nodes(ET.fromstring(self.assets.read(name))):
                        if not part['elements']:
                            self.nonvisual_parts.add(part['id'])
                            continue
                        if part['id'] in self.parts: raise ValueError('Duplicate component: '+part['id'])
                        part['table'] = name
                        part['label'] = part['id']
                        self.parts[part['id']] = part
            except (ValueError, ET.ParseError) as exc: self.errors.append(dict(asset=name, error=str(exc)))
        for part in self.parts.values():
            identity = part['family'].removesuffix('_root').removeprefix('gm_person_')
            actor = imported_people.get(part['family'].removesuffix('_root'),
                self.souls.get(identity, {}).get('soul_name', part['family'].removesuffix('_root')))
            part['actor'] = actor
            part['label'] = actor+' · '+part['id'].removeprefix(part['family'].removesuffix('_root')+'_')
            self.presets.setdefault(part['family'], dict(id=part['family'], label=actor, gender=part['gender'], parts=[]))['parts'].append(part['id'])
        self.presets = {k:v for k,v in self.presets.items() if any(self.parts[p]['kind']=='Body' for p in v['parts'])}
        # Underwear is a fallback, not an additional equipped copy of its source dress.
        for preset in self.presets.values():
            preset['parts'] = [p for p in preset['parts'] if not (self.parts[p]['kind']=='Clothing' and not self.parts[p]['attributes'].get('ArmorType'))]
        from clothing_inventory import audit_assets
        self.inventory_checks = audit_assets(self.assets, imported_people)
        for check in self.inventory_checks:
            preset = self.presets.get(check['namespace']+'_root')
            if preset is not None:
                preset['equipment_status'] = check['status']
                if check['status'] == 'checked':
                    missing = set(check['components'])-set(self.parts)-self.nonvisual_parts
                    if missing:
                        self.errors.append(dict(asset=check['namespace'],
                            error='Equipped clothing has no renderable component: '+', '.join(sorted(missing))))
                    else:
                        preset['parts'] = [p for p in preset['parts'] if self.parts[p]['kind'] != 'Clothing'] + [
                            p for p in check['components'] if p in self.parts]
            for issue in check['issues']:
                self.errors.append(dict(asset=check['namespace'], error=issue['message'],
                                        code=issue['code'], person=check['person']))

    def _groups(self, doc):
        for g in doc.iter('HidingGroup'):
            value = dict(index=int(g.get('Index')), region=g.get('EquipmentPart'))
            if g.get('Name') in self.groups and self.groups[g.get('Name')] != value:
                raise ValueError('Conflicting hiding group definition: '+g.get('Name'))
            self.groups[g.get('Name')] = value

    def summary(self):
        return dict(package=str(self.assets.root), signature=self.assets.signature(), parts=list(self.parts.values()),
                    presets=list(self.presets.values()), groups=self.groups, errors=self.errors,
                    native_rules=bool(self.native_roles and self.layer_order), layer_order=self.layer_order,
                    rule_evidence=self.rule_evidence, inventory_checks=self.inventory_checks)

    @lru_cache(maxsize=1024)
    def mesh(self, name):
        return CompiledSkin(self.assets.read(name))

    @lru_cache(maxsize=2048)
    def model_at_lod(self, name, requested):
        """Preview authored per-part LODs, explicitly retaining a shorter chain."""
        from character_lods import source_lods
        if requested not in range(5):
            raise ValueError('Invalid LOD')
        if name not in self.assets.index:
            raise ValueError('Missing base skin: ' + name)
        family = {0: name, **source_lods(self.assets.index, name)}
        effective = min(requested, max(family))
        return family[effective], effective

    @lru_cache(maxsize=256)
    def materials(self, name, textures=False):
        if not name: raise ValueError('Component has no resolved material')
        root = ET.fromstring(self.assets.read(name))
        nodes = list(root.find('SubMaterials')) if root.find('SubMaterials') is not None else [root]
        features = set()
        for node in nodes:
            for declaration in node.findall('FeatureSlots'):
                duplicate = features.intersection(declaration.attrib)
                if duplicate:
                    raise ValueError('Native feature slot declared in multiple submaterials: ' + ', '.join(sorted(duplicate)))
                features.update(declaration.attrib)
        output = []
        for node in nodes:
            diffuse = node.find("Textures/Texture[@Map='Diffuse']")
            item = dict(name=node.get('Name',''), shader=node.get('Shader',''), threshold=float(node.get('AlphaTest','0')),
                        two_sided=bool(int(node.get('MtlFlags','0')) & 2),
                        opacity=float(node.get('Opacity','1')), texture=diffuse.get('File') if diffuse is not None else None)
            if textures and item['texture']:
                macro='CLOTHING_SYSTEM' in node.get('StringGenMask','')
                item['image'] = 'data:image/png;base64,'+base64.b64encode(self.assets.texture(item['texture'],macro)).decode()
            output.append(item)
        return output

    @lru_cache(maxsize=512)
    def coverage_surface(self, model, material, mask):
        from clothing_coverage import alpha_intersections
        from .geometry import Surface
        mesh = self.mesh(model)
        alpha = np.frombuffer(mesh.streams[3].data[24:],np.uint8).reshape(-1,4)[:,3]
        hidden = ((255 ^ alpha) & mask) != 0
        surfaces = []
        for mat in self.materials(material):
            if mat['shader'].lower() == 'nodraw':
                surfaces.append((0,[np.zeros((1,1))])); continue
            if mat['opacity'] != 1 or not mat['texture']:
                raise ValueError('Material cannot prove opaque coverage')
            pixels,_ = self.assets.pixels(mat['texture'])
            alpha_pixels=pixels[:,:,3]
            threshold=mat['threshold']
            if np.all(alpha_pixels > threshold):
                alpha_pixels=np.full((1,1),threshold+1.)
            elif np.all(alpha_pixels <= threshold):
                alpha_pixels=np.full((1,1),threshold)
            else:
                alpha_pixels=alpha_pixels.copy()
            surfaces.append((threshold,[alpha_pixels]))
        visible = ~hidden[mesh.faces].any(axis=1)
        return Surface(mesh.vertices[mesh.faces[visible]],alpha_intersections(mesh,surfaces,visible))

    @lru_cache(maxsize=8192)
    def coverage_hits(self, source, cover, material, mask, inward=.06, outward=.12):
        mesh = self.mesh(source)
        referenced = np.unique(mesh.faces)
        result = np.zeros(mesh.n,bool)
        result[referenced] = self.coverage_surface(cover,material,mask).covers(
            mesh.vertices[referenced],normals(mesh)[referenced],inward,outward)
        return result

    def scene(self, selected, lod=0):
        from .validation import element_replaced
        if not isinstance(selected,list) or not 1 <= len(selected) <= 64: raise ValueError('Select 1–64 components')
        if lod not in range(5): raise ValueError('Invalid LOD')
        rows, errors = [], []
        for identity in dict.fromkeys(selected):
            part = self.parts[identity]
            for e in part['elements']:
                name = e.get('ModelResolved','')
                try:
                    name, effective_lod = self.model_at_lod(name, lod)
                    skin = self.mesh(name)
                    uv = np.frombuffer(skin.streams[2].data[24:], '<f4').reshape(-1,2)
                    alpha = np.frombuffer(skin.streams[3].data[24:],np.uint8).reshape(-1,4)[:,3]
                    if len(uv) != skin.n or len(alpha) != skin.n: raise ValueError('Invalid vertex stream size')
                    def buffer(a, dtype): return base64.b64encode(np.asarray(a,dtype=dtype).tobytes()).decode()
                    rows.append(dict(id=identity, kind=part['kind'], element=e, model=name, requested_lod=lod,
                        effective_lod=effective_lod, replaced=element_replaced(self,selected,part,e), provenance=self.assets.provenance(name),
                        positions=buffer(skin.vertices,'<f4'), normals=buffer(normals(skin),'<f4'), uv=buffer(uv,'<f4'),
                        indices=buffer(skin.faces,'<u4'), alpha=buffer(alpha,'u1'), bones=skin.info['bones'],
                        weights=buffer(skin.weights,'<f4'), joints=buffer(skin.ids,'<u2'), influences=skin.ids.shape[1],
                        subsets=skin.info['subset_records'], materials=self.materials(e.get('MaterialResolved'),True)))
                except (ValueError, KeyError, OSError, NotImplementedError) as exc:
                    errors.append(dict(component=identity, asset=name, error=str(exc)))
        return dict(meshes=rows, errors=errors, lod=lod)
