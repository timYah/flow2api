import types
import unittest

from src.core.model_resolver import resolve_model_name, get_base_model_aliases
from src.services.generation_handler import MODEL_CONFIG


class OmniModelTests(unittest.TestCase):
    def test_omni_model_configs_exist(self):
        for key in (
            "omni",
            "omni_portrait",
            "omni_landscape",
            "omni_8s",
            "omni_8s_portrait",
            "omni_8s_landscape",
            "omni_10s",
            "omni_10s_portrait",
            "omni_10s_landscape",
            "omini",
            "omini_10s",
        ):
            with self.subTest(model_key=key):
                self.assertIn(key, MODEL_CONFIG)
                cfg = MODEL_CONFIG[key]
                self.assertEqual(cfg["type"], "video")
                self.assertEqual(cfg["video_type"], "omni")
                self.assertTrue(cfg["supports_images"])
                self.assertEqual(cfg["max_images"], 3)

    def test_omni_10s_model_keys(self):
        cfg_landscape = MODEL_CONFIG["omni_10s"]
        self.assertEqual(cfg_landscape["model_key"], "abra_t2v_10s")
        self.assertEqual(cfg_landscape["reference_model_key"], "abra_r2v_10s")
        self.assertEqual(cfg_landscape["reference_duration"], 10)
        self.assertEqual(cfg_landscape["aspect_ratio"], "VIDEO_ASPECT_RATIO_LANDSCAPE")

        cfg_portrait = MODEL_CONFIG["omni_10s_portrait"]
        self.assertEqual(cfg_portrait["model_key"], "abra_t2v_10s")
        self.assertEqual(cfg_portrait["reference_model_key"], "abra_r2v_10s")
        self.assertEqual(cfg_portrait["reference_duration"], 10)
        self.assertEqual(cfg_portrait["aspect_ratio"], "VIDEO_ASPECT_RATIO_PORTRAIT")

    def test_omni_8s_model_keys(self):
        cfg = MODEL_CONFIG["omni_8s"]
        self.assertEqual(cfg["model_key"], "abra_t2v_8s")
        self.assertEqual(cfg["reference_model_key"], "abra_r2v_8s")
        self.assertEqual(cfg["reference_duration"], 8)

    def test_resolve_omni_defaults_to_landscape(self):
        resolved = resolve_model_name("omni", model_config=MODEL_CONFIG)
        self.assertEqual(resolved, "omni")

    def test_resolve_omni_portrait(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="portrait")
        )
        resolved = resolve_model_name("omni", request=request, model_config=MODEL_CONFIG)
        self.assertEqual(resolved, "omni_portrait")

    def test_resolve_omni_10s_variants(self):
        resolved_landscape = resolve_model_name("omni_10s", model_config=MODEL_CONFIG)
        self.assertEqual(resolved_landscape, "omni_10s")

        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="9:16")
        )
        resolved_portrait = resolve_model_name("omni_10s", request=request, model_config=MODEL_CONFIG)
        self.assertEqual(resolved_portrait, "omni_10s_portrait")

    def test_resolve_omni_hyphen_aliases(self):
        resolved_8s = resolve_model_name("omni-8s", model_config=MODEL_CONFIG)
        self.assertEqual(resolved_8s, "omni_8s")

        resolved_10s = resolve_model_name("omni-10s", model_config=MODEL_CONFIG)
        self.assertEqual(resolved_10s, "omni_10s")

        resolved_flash = resolve_model_name("omni-flash", model_config=MODEL_CONFIG)
        self.assertEqual(resolved_flash, "omni")

    def test_resolve_omini_typo_aliases(self):
        resolved = resolve_model_name("omini", model_config=MODEL_CONFIG)
        self.assertEqual(resolved, "omni")

        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="portrait")
        )
        resolved_portrait = resolve_model_name("omini", request=request, model_config=MODEL_CONFIG)
        self.assertEqual(resolved_portrait, "omni_portrait")

        resolved_10s = resolve_model_name("omini_10s", model_config=MODEL_CONFIG)
        self.assertEqual(resolved_10s, "omni_10s")

    def test_base_model_aliases_include_omni(self):
        aliases = get_base_model_aliases()
        self.assertIn("omni", aliases)
        self.assertIn("omni_8s", aliases)
        self.assertIn("omni_10s", aliases)


if __name__ == "__main__":
    unittest.main()
