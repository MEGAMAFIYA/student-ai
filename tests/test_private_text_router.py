"""Regressiya: AL Yordamchi routeri (group=-4) /developer > 📢 Reklama kabi
boshqa oqimlarning matnini "yutib" yubormasligi kerak."""
import asyncio
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._stub_telegram import install_stubs
install_stubs()
if "telegram.constants" not in sys.modules:  # stubda yo'q — faqat ParseMode kerak
    _const = types.ModuleType("telegram.constants")
    _const.ParseMode = types.SimpleNamespace(HTML="HTML", MARKDOWN="Markdown")
    sys.modules["telegram.constants"] = _const

import telegram  # noqa: E402
for _attr in ("BotCommand", "BotCommandScopeChat", "InlineKeyboardButton", "InlineKeyboardMarkup"):
    if not hasattr(telegram, _attr):  # stubda bo'lmasa — oddiy to'ldiruvchi
        setattr(telegram, _attr, type(_attr, (), {"__init__": lambda self, *a, **k: None}))

for _name in ("ai_clients", "al_yordamchi_db"):  # og'ir tashqi kutubxonalar (google.generativeai) kerak emas
    try:
        __import__(_name)
    except Exception:
        sys.modules[_name] = types.ModuleType(_name)

from handlers import al_yordamchi, business_autoreply  # noqa: E402


class _Msg:
    def __init__(self, text):
        self.text = text
        self.chat = types.SimpleNamespace(type="private", id=1)
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


def _update(text):
    msg = _Msg(text)
    return types.SimpleNamespace(
        effective_message=msg, message=msg,
        effective_user=types.SimpleNamespace(id=42),
    ), msg


def _ctx(user_data=None):
    return types.SimpleNamespace(user_data=user_data if user_data is not None else {})


class PrivateTextRouterTest(unittest.TestCase):
    def test_plain_text_is_not_consumed(self):
        # Hech qanday AL / bossiyons holati yo'q (masalan reklama uchun guruh ID yozildi)
        upd, msg = _update("-1001234567890")
        handled = asyncio.run(al_yordamchi.receive_setup_text(upd, _ctx()))
        self.assertFalse(handled)
        self.assertEqual(msg.replies, [])

    def test_bossiyons_text_is_still_consumed(self):
        upd, msg = _update("Salom, hozir band man")
        ctx = _ctx({business_autoreply._WAITING_TEXT: True})
        handled = asyncio.run(al_yordamchi.receive_setup_text(upd, ctx))
        self.assertTrue(handled)
        self.assertEqual(ctx.user_data[business_autoreply._DRAFT_KEY], "Salom, hozir band man")


if __name__ == "__main__":
    unittest.main()
