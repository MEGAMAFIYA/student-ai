import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._stub_telegram import install_stubs
install_stubs()

import telegram_effects  # noqa: E402


class EffectMapTests(unittest.TestCase):
    def test_real_data_file_loads_and_has_default_emojis(self):
        telegram_effects.reload()
        for emoji in ["😍", "🥳", "🎉", "❤️", "✨"]:
            effect_id = telegram_effects.get_effect_id(emoji)
            self.assertIsNotNone(effect_id, f"{emoji} uchun effect_id data/telegram_message_effects.json'da topilishi kerak")
            self.assertTrue(str(effect_id).isdigit() or isinstance(effect_id, str))

    def test_unknown_emoji_returns_none(self):
        telegram_effects.reload()
        self.assertIsNone(telegram_effects.get_effect_id("🦄🦄🦄-not-a-real-emoji-key"))

    def test_missing_file_falls_back_to_empty_map_without_crash(self):
        old_path = telegram_effects.EFFECTS_JSON_PATH
        try:
            telegram_effects.EFFECTS_JSON_PATH = "/nonexistent/path/effects.json"
            telegram_effects.reload()
            self.assertIsNone(telegram_effects.get_effect_id("😍"))
        finally:
            telegram_effects.EFFECTS_JSON_PATH = old_path
            telegram_effects.reload()

    def test_malformed_json_falls_back_to_empty_map_without_crash(self):
        old_path = telegram_effects.EFFECTS_JSON_PATH
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not valid json!!")
            tmp_path = f.name
        try:
            telegram_effects.EFFECTS_JSON_PATH = tmp_path
            telegram_effects.reload()
            self.assertIsNone(telegram_effects.get_effect_id("😍"))
        finally:
            telegram_effects.EFFECTS_JSON_PATH = old_path
            telegram_effects.reload()
            os.unlink(tmp_path)

    def test_first_matching_variant_is_kept(self):
        old_path = telegram_effects.EFFECTS_JSON_PATH
        data = {"effects": [
            {"emoji": "😍", "effect_id": "111"},
            {"emoji": "😍", "effect_id": "222"},
        ]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            tmp_path = f.name
        try:
            telegram_effects.EFFECTS_JSON_PATH = tmp_path
            telegram_effects.reload()
            self.assertEqual(telegram_effects.get_effect_id("😍"), "111")
        finally:
            telegram_effects.EFFECTS_JSON_PATH = old_path
            telegram_effects.reload()
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
