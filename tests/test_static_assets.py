"""Asset-free regression checks for the observed building material mismatch."""
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from static_assets import convert_material_features, preserve_legacy_decal_shadows


class StaticMaterialTests(unittest.TestCase):
    def test_legacy_decal_displacement_does_not_enable_new_self_shadows(self):
        features = {"%DECAL": 1, "%PARALLAX_OCCLUSION_MAPPING": 2}
        m = ET.fromstring('''<Material Shader="Illum"
          StringGenMask="%DECAL%PARALLAX_OCCLUSION_MAPPING">
          <PublicParams SelfShadowStrength="2.814" PomDisplacement="0.0145" HeightBias="0.425"/>
          <Textures><Texture Map="Heightmap" File="mud_displ.dds"/></Textures></Material>''')
        preserve_legacy_decal_shadows(m, set(features))
        self.assertEqual(m.find("PublicParams").get("SelfShadowStrength"), "0")
        self.assertEqual(m.find("PublicParams").get("PomDisplacement"), "0.0145")
        self.assertEqual(m.find("PublicParams").get("HeightBias"), "0.425")
        self.assertEqual(m.find("Textures/Texture").get("File"), "mud_displ.dds")
        self.assertIn("%PARALLAX_OCCLUSION_MAPPING", m.get("StringGenMask"))

    def test_non_decal_parallax_retains_authored_self_shadowing(self):
        features = {"%PARALLAX_OCCLUSION_MAPPING": 2}
        m = ET.fromstring('''<Material Shader="Illum" StringGenMask="%PARALLAX_OCCLUSION_MAPPING">
          <PublicParams SelfShadowStrength="2.814"/></Material>''')
        preserve_legacy_decal_shadows(m, set(features))
        self.assertEqual(m.find("PublicParams").get("SelfShadowStrength"), "2.814")

    def test_named_features_override_unrelated_serialized_bits(self):
        m = ET.fromstring('<Material Shader="Illum" GenMask="40" StringGenMask="%SNDUVS"/>')
        convert_material_features(m, {"%VERTCOLORS": 64}, {"%VERTCOLORS": 8, "%SNDUVS": 4})
        self.assertEqual(m.get("GenMask"), "4")
        self.assertEqual(m.get("StringGenMask"), "%SNDUVS")

    def test_second_uv_replaces_old_opacity_without_changing_texture(self):
        m = ET.fromstring('''<Material Shader="Illum" StringGenMask="%SNDUVS">
          <Textures><Texture Map="Diffuse" File="roof.dds"/>
          <Texture Map="[1] Diffuse" File="baked_rgba.dds"><TexMod TileU="1"/></Texture>
          <Texture Map="Opacity" File="old_tiled_mask.dds"/></Textures></Material>''')
        convert_material_features(m, {}, {"%SNDUVS": 4})
        slots = m.findall('./Textures/Texture[@Map="Opacity"]')
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].get("File"), "baked_rgba.dds")
        self.assertEqual(slots[0].find("TexMod").get("TileU"), "1")
        self.assertEqual(m.find('./Textures/Texture[@Map="Diffuse"]').get("File"), "roof.dds")

    def test_regular_opacity_is_preserved_without_second_uv_feature(self):
        m = ET.fromstring('<Material Shader="Illum" StringGenMask=""><Textures><Texture Map="Opacity" File="mask.dds"/></Textures></Material>')
        convert_material_features(m, {}, {"%SNDUVS": 4})
        self.assertEqual(m.find('./Textures/Texture').get("File"), "mask.dds")


if __name__ == "__main__":
    unittest.main()
