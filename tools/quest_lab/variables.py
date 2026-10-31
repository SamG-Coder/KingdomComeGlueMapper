"""Initialize explicit behavior declarations without guessing engine defaults."""
import copy
import math

from . import Unsupported
from .expressions import evaluate


def initialize(declarations, inputs, enum_resolver):
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
