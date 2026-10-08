"""Add a campaign-menu lifecycle event to the user's own retail Scaleform UI.

No game UI is distributed. Existing actions and tags are preserved; unsupported
function bodies fail closed rather than attempting a blind binary replacement.
"""
import hashlib
import struct
import xml.etree.ElementTree as ET
import zlib

ROOT_FUNCTION = b'fc_showRootMenu'
# Verified 1.5.6 implementation: updateMenuLevel(0). No branches/early returns.
ROOT_BODY = bytes.fromhex('961000060000000000000000070100000008c23d17')
EVENT_ACTION = b'\x83' + struct.pack('<H', len(b'FSCommand:onGlueMapperRootMenu\0\0')) + b'FSCommand:onGlueMapperRootMenu\0\0'


def patch_actions(actions):
    result = bytearray()
    offset, changes = 0, 0
    while offset < len(actions):
        start = offset
        opcode = actions[offset]
        offset += 1
        length = 0
        if opcode >= 0x80:
            length = struct.unpack_from('<H', actions, offset)[0]
            offset += 2
        payload_start = offset
        end = offset + length
        if end > len(actions):
            raise ValueError('Truncated Scaleform action')
        if opcode in (0x8E, 0x9B):
            name_end = actions.index(0, offset, end)
            name = actions[offset:name_end]
            offset = name_end + 1
            count = struct.unpack_from('<H', actions, offset)[0]
            offset += 2
            if opcode == 0x8E:
                offset += 3  # RegisterCount, flags.
            for _ in range(count):
                if opcode == 0x8E:
                    offset += 1
                offset = actions.index(0, offset, end) + 1
            size_at = offset
            body_size = struct.unpack_from('<H', actions, size_at)[0]
            body_start = size_at + 2
            body_end = body_start + body_size
            if body_start != end or body_end > len(actions):
                raise ValueError('Unexpected Scaleform function encoding')
            if name == ROOT_FUNCTION:
                body = actions[body_start:body_end]
                if count or body != ROOT_BODY:
                    raise ValueError('Unsupported retail root-menu function; inspect this game version')
                header = bytearray(actions[start:body_start])
                struct.pack_into('<H', header, size_at - start, body_size + len(EVENT_ACTION))
                result += header + body + EVENT_ACTION
                changes += 1
            else:
                result += actions[start:body_end]
            offset = body_end
        else:
            result += actions[start:end]
            offset = end
        if opcode == 0:
            result += actions[offset:]
            break
    return bytes(result), changes


def patch_gfx(blob):
    if blob[:3] != b'CFX' or blob[3] != 8:
        raise ValueError('Expected retail compressed Scaleform v8')
    expected = struct.unpack_from('<I', blob, 4)[0]
    if expected > 32 * 1024 * 1024:
        raise ValueError('Unexpected menu size')
    body = zlib.decompress(blob[8:])
    if len(body) + 8 != expected:
        raise ValueError('Scaleform size mismatch')
    # RECT bit length, frame rate and frame count.
    offset = (5 + 4 * (body[0] >> 3) + 7) // 8 + 4
    result = bytearray(body[:offset])
    changed = 0
    while offset < len(body):
        start = offset
        record = struct.unpack_from('<H', body, offset)[0]
        offset += 2
        code, length = record >> 6, record & 63
        if length == 63:
            length = struct.unpack_from('<I', body, offset)[0]
            offset += 4
        end = offset + length
        if end > len(body):
            raise ValueError('Truncated Scaleform tag')
        payload = body[offset:end]
        if code == 12:  # DoAction; root function is defined on the main timeline.
            patched, count = patch_actions(payload)
            changed += count
            if count:
                result += struct.pack('<HI', (code << 6) | 63, len(patched)) + patched
            else:
                result += body[start:end]
        else:
            result += body[start:end]
        offset = end
    if changed != 1:
        raise ValueError(f'Expected exactly one root-menu function, found {changed}')
    return blob[:4] + struct.pack('<I', len(result) + 8) + zlib.compress(result, 9)


def patch_ui_xml(blob):
    root = ET.fromstring(blob)
    menu = root.find("./UIElement[@name='Menu']")
    if menu is None or menu.find("./functions/function[@name='AddBasicButton']") is None:
        raise ValueError('Retail menu interface is not supported')
    events = menu.find('events')
    if events.find("./event[@name='GlueMapperRootMenu']") is not None:
        raise ValueError('Menu already patched')
    ET.SubElement(events, 'event', name='GlueMapperRootMenu', fscommand='onGlueMapperRootMenu')
    functions = menu.find('functions')
    remove = ET.SubElement(functions, 'function', name='GlueMapperRemoveButton', funcname='fc_removeBasicButton')
    ET.SubElement(remove, 'param', name='buttonId', type='string')
    ET.SubElement(remove, 'param', name='containerIndex', type='int')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def menu_assets(archive, read):
    gfx = read(archive, 'Libs/UI/Menu.gfx')
    xml = read(archive, 'Libs/UI/UIElements/Menu.xml')
    return ({'Libs/UI/Menu.gfx': patch_gfx(gfx), 'Libs/UI/UIElements/Menu.xml': patch_ui_xml(xml)},
            {'source_gfx_sha256': hashlib.sha256(gfx).hexdigest(),
             'source_interface_sha256': hashlib.sha256(xml).hexdigest()})
