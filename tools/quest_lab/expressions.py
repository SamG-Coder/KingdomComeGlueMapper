"""Small, allowlisted expression evaluator; never eval game/scenario text."""
import ast
import copy
import operator
import re

from . import Unsupported


def parts(text, delimiter=','):
    result, start, depth, quote, escaped = [], 0, 0, '', False
    for i, char in enumerate(text):
        if quote:
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == quote: quote = ''
        elif char in "'\"": quote = char
        elif char in '([{': depth += 1
        elif char in ')]}': depth -= 1
        elif char == delimiter and depth == 0:
            result.append(text[start:i].strip()); start = i + 1
        if depth < 0: raise Unsupported('Unbalanced expression')
    if quote or depth: raise Unsupported('Unbalanced expression')
    result.append(text[start:].strip())
    return result


def syntax(text):
    # Translate only outside quoted strings. A method or function call is never allowed.
    pattern = r"('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
    chunks = re.split(pattern, text)
    for i in range(0, len(chunks), 2):
        s = re.sub(r'\$([A-Za-z_]\w*)', r'\1', chunks[i])
        s = re.sub(r'\btrue\b', 'True', s)
        s = re.sub(r'\bfalse\b', 'False', s)
        s = re.sub(r'!(?!=)', ' not ', s).replace('&&', ' and ').replace('||', ' or ')
        s = s.replace('&', ' and ').replace('|', ' or ')
        chunks[i] = s
    try: return ast.parse(''.join(chunks).strip(), mode='eval').body
    except SyntaxError as exc: raise Unsupported('Unsupported expression: ' + text) from exc


BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Mod: operator.mod}
CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
       ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def enum_text(text, enum_resolver=None):
    if '$enum:' in text:
        def resolve(match):
            if enum_resolver is None: raise Unsupported('Behavior enum registry is required: '+match[1])
            value = enum_resolver(match[1], match[2])
            if type(value) is not int: raise Unsupported('Behavior enum ordinal must be an integer')
            return '('+str(value)+')'
        # Enum-looking text inside strings is data, not a registry lookup.
        chunks = re.split(r'''('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")''', text)
        text = ''.join(chunk if i % 2 else re.sub(r'\$enum:([A-Za-z_]\w*)\.([A-Za-z_]\w*)', resolve, chunk)
                       for i,chunk in enumerate(chunks))
    return text


def evaluate(text, values, enum_resolver=None):
    text = enum_text(text, enum_resolver)
    def visit(n):
        if isinstance(n, ast.Constant) and type(n.value) in (int, float, bool, str, type(None)):
            return n.value
        if isinstance(n, ast.Name):
            if n.id not in values: raise Unsupported('Unbound expression variable: ' + n.id)
            return values[n.id]
        if isinstance(n, ast.Attribute):
            obj = visit(n.value)
            if not isinstance(obj, dict) or n.attr not in obj:
                raise Unsupported('Unbound expression member: ' + n.attr)
            return obj[n.attr]
        if isinstance(n, ast.Subscript):
            obj, key = visit(n.value), visit(n.slice)
            if not isinstance(obj, (dict, list)): raise Unsupported('Unsupported indexed value')
            try: return obj[key]
            except (KeyError, IndexError, TypeError) as exc: raise Unsupported('Unbound expression index') from exc
        if isinstance(n, ast.UnaryOp):
            v = visit(n.operand)
            if isinstance(n.op, ast.Not): return not boolean(v)
            if isinstance(n.op, ast.USub) and type(v) in (int, float): return -v
        if isinstance(n, ast.BoolOp):
            for value in n.values:
                result = boolean(visit(value))
                if isinstance(n.op, ast.And) and not result: return False
                if isinstance(n.op, ast.Or) and result: return True
            return isinstance(n.op, ast.And)
        if isinstance(n, ast.BinOp) and type(n.op) in BIN:
            a, b = visit(n.left), visit(n.right)
            if type(a) not in (int, float) or type(b) not in (int, float):
                raise Unsupported('Arithmetic requires numbers')
            return BIN[type(n.op)](a, b)
        if isinstance(n, ast.Compare):
            a = visit(n.left)
            for op, right in zip(n.ops, n.comparators):
                if type(op) not in CMP: raise Unsupported('Unsupported comparison')
                b = visit(right)
                if not CMP[type(op)](a, b): return False
                a = b
            return True
        raise Unsupported('Unsupported expression operation: ' + type(n).__name__)
    return visit(syntax(text))


def boolean(value):
    if type(value) is not bool: raise Unsupported('Expected boolean, got ' + repr(value))
    return value


def assignments(text, values, enum_resolver=None):
    changes = {}
    for statement in parts(text):
        # Parse the target using the same allowlisted expression syntax, but
        # never execute Python assignments or allow arbitrary object mutation.
        fields = parts(statement, '=')
        if len(fields) < 2 or not fields[0]:
            raise Unsupported('Unsupported assignment: ' + statement)
        target, expr = fields[0], '='.join(fields[1:])
        node = syntax(enum_text(target, enum_resolver))
        def location(n):
            if isinstance(n, ast.Name): return values, n.id
            if not isinstance(n, (ast.Attribute, ast.Subscript)):
                raise Unsupported('Unsupported assignment target: ' + target)
            parent, key = location(n.value)
            try: obj = parent[key]
            except (KeyError, IndexError, TypeError) as error:
                raise Unsupported('Unbound assignment container: ' + target) from error
            if isinstance(n, ast.Attribute):
                if not isinstance(obj, dict) or n.attr not in obj:
                    raise Unsupported('Unbound assignment member: ' + n.attr)
                return obj, n.attr
            key = evaluate(ast.unparse(n.slice), values)
            if isinstance(obj, list):
                if type(key) is not int or not 0 <= key < len(obj):
                    raise Unsupported('Invalid assignment array index')
            elif isinstance(obj, dict):
                # JSON snapshots preserve string map keys. Numeric/custom-key
                # maps need a typed persistence adapter, not coercion to text.
                if type(key) is not str: raise Unsupported('Unsupported assignment map key')
            else: raise Unsupported('Unsupported assignment container')
            return obj, key
        container, key = location(node)
        value = copy.deepcopy(evaluate(expr, values, enum_resolver))
        container[key] = value
        changes[target.removeprefix('$')] = copy.deepcopy(value)
    return changes


def literal(value):
    if value == 'true': return True
    if value == 'false': return False
    if re.fullmatch(r'-?\d+', value): return int(value)
    if re.fullmatch(r'-?\d+\.\d+', value): return float(value)
    if value.startswith(("'", '"')):
        return evaluate(value, {})
    return value


def duration(value):
    """Integral microseconds avoid drift and make save/replay deterministic."""
    from decimal import Decimal
    match = re.fullmatch(r'(\d+(?:\.\d+)?)(us|ms|s|m|h)', str(value))
    if not match: raise Unsupported('Unsupported duration: ' + str(value))
    amount = Decimal(match[1]) * {'us': 1, 'ms': 1000, 's': 1000000, 'm': 60000000, 'h': 3600000000}[match[2]]
    if amount != int(amount): raise Unsupported('Duration below microsecond precision')
    return int(amount)
