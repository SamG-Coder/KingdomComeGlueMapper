import struct
import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from retail_menu import ROOT_BODY, EVENT_ACTION, patch_actions, patch_gfx, patch_ui_xml, native_new_game_function


def function(name, body):
    header = name.encode() + b'\0\0\0' + struct.pack('<H', len(body))
    return b'\x9b' + struct.pack('<H', len(header)) + header + body


def gfx(actions):
    # Minimal RECT with 1-bit coordinates, then frame rate/count.
    head = b'\x08\x00\x00\x18\x01\x00'
    body = head + struct.pack('<HI', (12 << 6) | 63, len(actions)) + actions + b'\x40\x00\x00\x00'
    return b'CFX\x08' + struct.pack('<I', len(body) + 8) + zlib.compress(body)


class RetailMenuTests(unittest.TestCase):
    def test_preserves_other_functions_and_native_root_body(self):
        before = function('unrelated', b'\x17')
        actions = before + function('fc_showRootMenu', ROOT_BODY) + b'\0'
        result, count = patch_actions(actions)
        self.assertEqual(count, 1)
        self.assertEqual(result[:len(before)], before)
        self.assertIn(ROOT_BODY + EVENT_ACTION, result)
        self.assertEqual(len(result), len(actions) + len(EVENT_ACTION) + len(native_new_game_function()))
        self.assertIn(b'FSCommand:onBasicButton\0NewGame\0', result)

    def test_rejects_changed_native_function(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported retail'):
            patch_actions(function('fc_showRootMenu', ROOT_BODY + b'\x3e'))

    def test_rejects_second_application(self):
        first = patch_gfx(gfx(function('fc_showRootMenu', ROOT_BODY) + b'\0'))
        with self.assertRaises(ValueError):
            patch_gfx(first)

    def test_tag_lengths_are_rebuilt(self):
        original = gfx(function('fc_showRootMenu', ROOT_BODY) + b'\0')
        result = patch_gfx(original)
        data = zlib.decompress(result[8:])
        self.assertEqual(len(data) + 8, struct.unpack_from('<I', result, 4)[0])
        self.assertEqual(data[-4:], b'\x40\x00\x00\x00')
        self.assertEqual(len(result[:8]), 8)

    def test_missing_or_duplicate_root_fails(self):
        for actions in (function('other', b'\x17'), function('fc_showRootMenu', ROOT_BODY) * 2):
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                patch_gfx(gfx(actions + b'\0'))

    def test_ui_preserves_existing_api(self):
        xml = b'<UIElements><UIElement name="Menu"><functions><function name="AddBasicButton" funcname="fc_addBasicButton"/></functions><events><event name="OnButton" fscommand="onBasicButton"/></events></UIElement></UIElements>'
        root = ET.fromstring(patch_ui_xml(xml))
        self.assertIsNotNone(root.find(".//event[@name='OnButton']"))
        self.assertIsNotNone(root.find(".//event[@name='GlueMapperRootMenu']"))
        self.assertIsNotNone(root.find(".//function[@name='GlueMapperRemoveButton']"))


if __name__ == '__main__':
    unittest.main()
