"""
🧪 Sinov muhiti uchun MINIMAL `telegram`/`httpx` stub'lari.

MUHIM (halollik uchun): bu fayl HAQIQIY python-telegram-bot kutubxonasi
o'rniga ISHLAMAYDI va Telegramning haqiqiy xatti-harakatini simulyatsiya
QILMAYDI. U faqat quyidagi maqsadda ishlatiladi: ushbu offline (tarmoqsiz)
sandbox muhitida `python-telegram-bot==22.8` paketi o'rnatilmagani uchun
(pip tarmoqqa chiqa olmaydi), bizning SOF BIZNES-LOGIKAMIZni (recipient
resolve, rights check, lock/concurrency, storage) haqiqiy kutubxona
klasslariga faqat "shakl" (interfeys) jihatidan mos fake obyektlar bilan
sinash mumkin bo'lsin.

Bu stub orqali o'tgan testlar: "Telegramda 100% ishlaydi" DEGANI EMAS.
Ular faqat: "bizning kodimiz — recipient_user_id==chat_id logikasi,
rights tekshiruvi, per-recipient lock, retry/delete oqimi — o'zi to'g'ri
yozilgan" ekanini tasdiqlaydi. Haqiqiy Business API xatti-harakati (masalan
sendMessage business_connection_id bilan chindan ishlashi) faqat real
Telegram Bot API bilan (yoki hech bo'lmaganda `pip install
python-telegram-bot==22.8` qilingan muhitda) tekshirilishi mumkin — buni
FINAL_REPORT.md'da "REAL TELEGRAM SMOKE TEST BAJARILMADI" deb ochiq
yozamiz.
"""

import sys
import types


def install_stubs():
    if "httpx" not in sys.modules:
        httpx_mod = types.ModuleType("httpx")

        class _HTTPStatusError(Exception):
            pass

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **kw):
                raise RuntimeError("stub httpx.Client: network yo'q (offline sandbox)")

            def post(self, *a, **kw):
                raise RuntimeError("stub httpx.Client: network yo'q (offline sandbox)")

        httpx_mod.Client = _Client
        httpx_mod.HTTPStatusError = _HTTPStatusError
        sys.modules["httpx"] = httpx_mod

    if "telegram" not in sys.modules:
        telegram_mod = types.ModuleType("telegram")

        class InlineKeyboardButton:
            def __init__(self, text, callback_data=None, url=None):
                self.text = text
                self.callback_data = callback_data
                self.url = url

        class InlineKeyboardMarkup:
            def __init__(self, inline_keyboard):
                self.inline_keyboard = inline_keyboard

        telegram_mod.InlineKeyboardButton = InlineKeyboardButton
        telegram_mod.InlineKeyboardMarkup = InlineKeyboardMarkup

        error_mod = types.ModuleType("telegram.error")

        class TelegramError(Exception):
            pass

        class BadRequest(TelegramError):
            pass

        class Forbidden(TelegramError):
            pass

        class RetryAfter(TelegramError):
            def __init__(self, retry_after=1):
                super().__init__(f"Flood control: retry after {retry_after}")
                self.retry_after = retry_after

        error_mod.TelegramError = TelegramError
        error_mod.BadRequest = BadRequest
        error_mod.Forbidden = Forbidden
        error_mod.RetryAfter = RetryAfter
        telegram_mod.error = error_mod

        sys.modules["telegram"] = telegram_mod
        sys.modules["telegram.error"] = error_mod
