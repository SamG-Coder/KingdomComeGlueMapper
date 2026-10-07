"""Fail-closed matching for local shader override generation."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from fix_instanced_shader import patch_include, patch_shadow_include


class InstancedShaderTests(unittest.TestCase):
    def test_vertex_patch_is_limited_to_expected_function(self):
        source='prefix\nuint Get_BindlessBoneOffset_Prev()\n{\n return asuint(CD_CustomData.z);\n}\nsuffix'
        patched=patch_include(source)
        self.assertTrue(patched.startswith('prefix\n'))
        self.assertTrue(patched.endswith('\nsuffix'))
        self.assertIn('SInstancingData[0].CI_CustomData.z',patched)
        self.assertIn('#else\n\treturn asuint(CD_CustomData.z);',patched)
        with self.assertRaises(ValueError):patch_include('different source')

    def test_shadow_patch_preserves_noninstanced_expression(self):
        source='return clamp(CD_CustomData2.w * motionBias, 0, 3);'
        patched=patch_shadow_include(source)
        self.assertIn('#else\n\t'+source,patched)
        self.assertIn('return clamp(motionBias, 0, 3);',patched)
        with self.assertRaises(ValueError):patch_shadow_include(source+source)


if __name__=='__main__':unittest.main()
