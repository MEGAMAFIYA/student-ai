import unittest

import config
import drawing_game


class DrawingRoomUrlTest(unittest.TestCase):
    def test_room_url_uses_runtime_username(self):
        old_runtime = drawing_game._RUNTIME_BOT_USERNAME
        old_short = config.DRAWING_APP_SHORT_NAME
        try:
            drawing_game.set_bot_username("RealStudentBot")
            config.DRAWING_APP_SHORT_NAME = "rasim"
            url = drawing_game.room_url("a1b2c3d4e5f6")
            self.assertEqual(
                url,
                "https://t.me/RealStudentBot/rasim?startapp=draw_a1b2c3d4e5f6&mode=fullscreen",
            )
        finally:
            drawing_game._RUNTIME_BOT_USERNAME = old_runtime
            config.DRAWING_APP_SHORT_NAME = old_short

    def test_room_url_rejects_bad_room(self):
        with self.assertRaises(ValueError):
            drawing_game.room_url("../bad")


if __name__ == "__main__":
    unittest.main()
