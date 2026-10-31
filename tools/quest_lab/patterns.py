"""Bounded XGen string interpolation evidenced by native Concatenation nodes."""
import re

from . import Unsupported
from .expressions import evaluate, literal


REFERENCE = re.compile(r'\$[A-Za-z_]\w*(?:(?:\.[A-Za-z_]\w*)|(?:\[\d+\]))*')


def concatenate(pattern, values):
    pattern = literal(pattern)
    if type(pattern) is not str:
        raise Unsupported('Concatenation requires a string pattern')
    output, position = [], 0
    while position < len(pattern):
        if pattern[position] == ';':
            raise Unsupported('Literal pattern separator needs native formatting verification')
        if pattern[position] != '$':
            output.append(pattern[position]); position += 1; continue
        match = REFERENCE.match(pattern, position)
        if match is None:
            raise Unsupported('Unsupported Concatenation variable marker')
        position = match.end()
        if position < len(pattern) and pattern[position] in '[.(':
            raise Unsupported('Unsupported Concatenation reference syntax')
        value = evaluate(match[0], values)
        # WUIDs, vectors, floats and engine-owned records need their own
        # formatting contracts. Python repr is not an engine representation.
        if type(value) not in (str, int):
            raise Unsupported('Concatenation supports explicit strings and integers only')
        output.append(str(value))
        if position < len(pattern) and pattern[position] == ';': position += 1
    return ''.join(output)
