"""Initialize explicit behavior declarations without guessing engine defaults."""
import copy
import math
import re

from . import Unsupported
from .expressions import evaluate, parts


class MissingDefault(Unsupported):
    pass


def record_value(source, typ, raw=None, stack=(), member_text=False):
    """Construct only declared fields with explicit defaults/overrides."""
    scalar = typ.removeprefix('_')
    if scalar in ('bool','int','int64','float','string') or typ.startswith('enum:'):
        if raw is None: raise MissingDefault('Missing declared default for '+typ)
        value = raw if scalar == 'string' and member_text else evaluate(raw,{},source.enum_value)
        valid = {'bool':type(value) is bool,'string':type(value) is str,
            'int':type(value) is int and -(2**31)<=value<2**31,
            'int64':type(value) is int and -(2**63)<=value<2**63,
            'float':type(value) is int or (type(value) is float and math.isfinite(value))}
        if (scalar in valid and not valid[scalar]) or (typ.startswith('enum:') and type(value) is not int):
            raise Unsupported('Record initializer type mismatch: '+typ)
        return value
    if typ in stack or len(stack)>=64: raise Unsupported('Recursive behavior record: '+typ)
    declarations=source.types.get(typ,[])
    if not declarations:
        raise MissingDefault('Missing record initializer adapter: '+typ)
    if len(declarations)!=1: raise Unsupported('Ambiguous behavior record: '+typ)
    _, version, node=declarations[0]
    if version!='1' or set(node.attrib)!={'Name'}:
        raise Unsupported('Unsupported behavior record schema: '+typ)
    fields={}
    for field in node:
        if field.tag=='Type': continue
        name=field.get('Name','')
        if (field.tag!='Member' or len(field) or not name.isidentifier() or name in fields
                or not field.get('Type') or set(field.attrib)-{'Name','Type','InitialValue'}):
            raise Unsupported('Malformed behavior record member: '+typ)
        fields[name]=field
    overrides={}
    if raw:
        for expression in parts(raw):
            match=re.fullmatch(r'([A-Za-z_]\w*)\((.*)\)',expression,re.S)
            if not match or match[1] not in fields or match[1] in overrides:
                raise Unsupported('Invalid behavior record initializer: '+typ)
            overrides[match[1]]=match[2]
    return {name:record_value(source, field.get('Type'),
                overrides.get(name,field.get('InitialValue')), stack+(typ,),
                member_text=name not in overrides)
            for name,field in fields.items()}


def initialize(declarations, inputs, enum_resolver, source=None):
    result = {}
    seen = set()
    for node in declarations:
        name, typ = node.get('name',''), node.get('type','')
        if (node.tag != 'Variable' or len(node) or not name.isidentifier() or name in seen
                or set(node.attrib)-{'name','type','form','values','isPersistent'}
                or node.get('isPersistent','0') not in ('0','1','true','false')):
            raise Unsupported('Unsupported behavior variable declaration: '+name)
        seen.add(name)
        form = node.get('form','single')
        if form not in ('single','array','associative') or not typ:
            raise Unsupported('Unsupported behavior variable form/type: '+name)
        if name in inputs:
            # Explicit scenario fixtures may supply engine-owned records. Their
            # initialization is not counted as emulated native construction.
            value = copy.deepcopy(inputs[name])
        elif form in ('array','associative') and not node.get('values',''):
            value = [] if form == 'array' else {}
        else:
            raw = node.get('values','')
            if form == 'single' and source is not None and typ in source.types:
                try: value = record_value(source,typ,raw or None)
                except MissingDefault:
                    if raw: raise
                    continue
                result[name] = value
                continue
            if not raw and form == 'single':
                # Output-only records can be assigned by a message handler.
                # Keep them unbound; an attempted read still fails explicitly.
                continue
            if form != 'single':
                raise Unsupported('Behavior variable requires explicit initialization: '+name)
            if typ not in ('_bool','_int','_int64','_float','_string') and not typ.startswith('enum:'):
                raise Unsupported('Behavior record initialization requires a type adapter: '+typ)
            value = evaluate(raw, {}, enum_resolver)
        if form == 'array' and not isinstance(value,list):
            raise Unsupported('Behavior array input must be a list: '+name)
        if form == 'associative' and (not isinstance(value,dict) or any(type(k) is not str for k in value)):
            raise Unsupported('Behavior map input requires string keys: '+name)
        if form == 'single':
            valid = {'_bool': type(value) is bool, '_string': type(value) is str,
                '_int': type(value) is int and -(2**31) <= value < 2**31,
                '_int64': type(value) is int and -(2**63) <= value < 2**63,
                '_float': type(value) is int or (type(value) is float and math.isfinite(value))}
            if typ in valid and not valid[typ]:
                raise Unsupported('Behavior initializer type mismatch: '+name)
            if typ.startswith('enum:') and type(value) is not int:
                raise Unsupported('Behavior enum initializer must be an ordinal: '+name)
        result[name] = value
    return result
