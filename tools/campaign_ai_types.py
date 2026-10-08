"""Import script-defined KDC1 types and enums into KDC2's native registries.

Nested type names, members, defaults and explicit enum ordinals are source
data. Every imported type is namespaced; an unrelated same-named KDC2 type is
never assumed to have the same layout. Engine-owned types need explicit ABI
mappings and are not reconstructed from a similarly named script structure.
"""
import copy
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from campaign_dependencies import ImportPlan
from campaign_quest_graph import identifier, xml


SCALARS = {'bool':'bool', '_bool':'bool', 'int':'int', 'int64':'int64', 'float':'float',
           'string':'string', '_string':'string', 'common:wuid':'wuid', 'common:vec3':'vec3'}
ENUM_REF = re.compile(r'\$enum:([A-Za-z_]\w*)\.([A-Za-z_]\w*)')


def attributes(node):
    return {k.lower():v for k,v in node.attrib.items()}


class AITypeSource:
    def __init__(self, document, provenance, namespace, cache):
        self.namespace = identifier(namespace).lower()
        self.provenance = provenance
        self.types, self.enums = {}, {}
        self.selected_types, self.selected_enums = {}, {}
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        if document.tag != 'TypeDefinitions' or document.get('version') != '1':
            raise ValueError('Unsupported retail AI type definition format')

        def visit(parent, path=''):
            for node in parent:
                tag = node.tag.lower()
                if tag == 'member':
                    continue
                if tag not in ('type','enum'):
                    raise ValueError('Unexpected AI type declaration: ' + node.tag)
                attrs = attributes(node)
                if set(attrs) != {'name'}:
                    raise ValueError('Unconverted AI type metadata')
                identifier(attrs['name'])
                name = path + attrs['name']
                index = self.enums if tag == 'enum' else self.types
                key = name.lower()
                if key in index:
                    existing_name, existing = index[key]
                    if existing_name != name:
                        raise ValueError('Case-colliding source AI type identity: ' + name)
                    if tag == 'enum':
                        left=[(n.tag,attributes(n),(n.text or '').strip()) for n in existing]
                        right=[(n.tag,attributes(n),(n.text or '').strip()) for n in node]
                        if left != right:raise ValueError('Conflicting source enum: ' + name)
                    else:
                        # Shipped files reopen namespaces and repeat a few
                        # identical definitions. Preserve the union of members
                        # while rejecting conflicting declarations of a field.
                        members={attributes(n)['name']:n for n in existing if n.tag.lower()=='member'}
                        for member in node:
                            if member.tag.lower()!='member':continue
                            member_name=attributes(member)['name']
                            if member_name in members:
                                old=members[member_name]
                                if attributes(old)!=attributes(member) or (old.text or '').strip()!=(member.text or '').strip():
                                    raise ValueError('Conflicting source type member: ' + name+'.'+member_name)
                            else:existing.append(copy.deepcopy(member))
                else:
                    index[key] = (name, copy.deepcopy(node))
                if tag == 'type':
                    visit(node, name + ':')
        visit(document)

    @classmethod
    def load(cls, source, reader, namespace, cache):
        record = reader(source, {'types':('Scripts.pak','Libs/AI/TypeDefinitions.xml')})['types']
        return cls(ET.fromstring(record['data']), record['candidates'], namespace, cache)

    def type_name(self, identity):
        if identity in SCALARS:
            return SCALARS[identity]
        if identity.startswith('enum:'):
            return 'enum:' + self.enum_name(identity[5:])
        return self.namespace + ':' + self.types[identity.lower()][0]

    def enum_name(self, identity):
        name = self.enums[identity.lower()][0]
        if ':' in name:
            raise ValueError('Nested source enum needs an ABI adapter')
        return self.namespace + '_' + name

    def enum_expression(self, value, registered):
        def replace(match):
            name, member = match.groups()
            target = registered.get('ai_enums',{}).get(name.lower())
            if target is None:
                raise ValueError('Unregistered source enum: ' + name)
            original = self.enums[name.lower()][1]
            if member not in {n.tag for n in original}:
                raise ValueError('Unknown source enum value: ' + name + '.' + member)
            return '$enum:' + target + '.' + member
        return ENUM_REF.sub(replace, value)

    def output(self, group):
        """One adapter-owned file avoids repeated nested type redeclarations.

        The shared staged Path is updated as requests arrive. Packaging occurs
        after the resolver finishes, so the importer sees its final contents.
        """
        root = ET.Element('database')
        if group == 'ai_types':
            types = ET.SubElement(root,'Types',version='1')
            owner = ET.SubElement(types,'Type',Name=self.namespace)
            nodes = {'':owner}
            for name,node in sorted(self.selected_types.items(), key=lambda p:(p[0].count(':'),p[0])):
                parent = name.rpartition(':')[0]
                if parent not in nodes:
                    raise ValueError('Missing imported parent type: ' + parent)
                output = copy.deepcopy(node)
                nodes[parent].append(output)
                nodes[name] = output
        else:
            enums = ET.SubElement(root,'Enums',version='1')
            for name,node in sorted(self.selected_enums.items()):
                enums.append(copy.deepcopy(node))
        name = 'Libs/Tables/ai/' + group + '__' + self.namespace + '.xml'
        path = self.cache / (group + '.xml')
        path.write_bytes(xml(root))
        return {name:path}


class AIEnumAdapter:
    def __init__(self, source): self.source = source

    def plan(self, identity):
        name, node = self.source.enums[identity]
        values=[];ordinal=-1;seen=set()
        for value in node:
            attrs=attributes(value)
            if set(attrs)-{'value'} or len(value) or (value.text or '').strip():
                raise ValueError('Unconverted source enum value metadata')
            identifier(value.tag)
            ordinal=int(attrs.get('value',ordinal+1))
            if value.tag in seen:raise ValueError('Duplicate source enum value')
            seen.add(value.tag);values.append(dict(name=value.tag,ordinal=ordinal))
        return ImportPlan(source=dict(name=name,values=values,provenance=self.source.provenance))

    def convert(self, identity, plan, registered):
        target=self.source.enum_name(identity)
        node=ET.Element('Enum',Name=target)
        for value in plan.source['values']:
            ET.SubElement(node,'Value',Name=value['name'],Value=str(value['ordinal']))
        self.source.selected_enums[plan.source['name']]=node
        return target,self.source.output('ai_enums'),dict(values=plan.source['values'],source_name=plan.source['name'])


class AIStructureAdapter:
    def __init__(self, source): self.source=source

    def plan(self, identity):
        if identity in SCALARS:
            return ImportPlan(source=dict(scalar=SCALARS[identity],source_name=identity))
        if identity.startswith('enum:'):
            return ImportPlan([('ai_enums',identity[5:])],dict(enum=identity[5:]))
        if identity not in self.source.types:
            raise ValueError('Source type is engine-owned or absent from retail definitions: ' + identity)
        name,node=self.source.types[identity]
        dependencies=[];parent=name.rpartition(':')[0]
        if parent:dependencies.append(('ai_types',parent))
        members=[];seen=set()
        for member in node:
            if member.tag.lower() in ('type','enum'):continue
            attrs=attributes(member)
            if member.tag.lower()!='member' or set(attrs)!={'name','type'} or len(member):
                raise ValueError('Unconverted source type member metadata')
            identifier(attrs['name'])
            if attrs['name'] in seen:raise ValueError('Duplicate source type member')
            seen.add(attrs['name'])
            dependencies.append(('ai_types',attrs['type']))
            default=(member.text or '').strip()
            for match in ENUM_REF.finditer(default):dependencies.append(('ai_enums',match[1]))
            if default and SCALARS.get(attrs['type'])=='string':
                raise ValueError('Nonempty string default requires verified native serialization: ' + name+'.'+attrs['name'])
            members.append(dict(name=attrs['name'],type=attrs['type'],default=default))
        return ImportPlan(dependencies,dict(name=name,members=members,provenance=self.source.provenance))

    def convert(self, identity, plan, registered):
        source=plan.source
        if 'scalar' in source:return source['scalar'],{},dict(native_scalar=True)
        if 'enum' in source:return 'enum:'+registered['ai_enums'][source['enum'].lower()],{},dict(enum=source['enum'])
        node=ET.Element('Type',Name=source['name'].split(':')[-1])
        for member in source['members']:
            attrs=dict(Name=member['name'],Type=registered['ai_types'][member['type'].lower()])
            if member['default']:
                attrs['InitialValue']=self.source.enum_expression(member['default'],registered)
            ET.SubElement(node,'Member',attrs)
        self.source.selected_types[source['name']]=node
        return self.source.type_name(identity),self.source.output('ai_types'),dict(source_name=source['name'],members=source['members'])
