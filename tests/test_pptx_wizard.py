"""Slayd yaratish ustasi (vizual tanlov) testlari.

Ishga tushirish (loyiha ildizidan):  python3 -m unittest tests.test_pptx_wizard -v

HALOLLIK UCHUN ESLATMA: bu sandbox'da `python-telegram-bot` o'rnatilmagan va tarmoq yo'q, shuning
uchun handlerlar HAQIQIY Telegram bilan emas, quyidagi SOXTA (faqat interfeysga mos) qatlam bilan
sinaladi. Bu bizning mantiqimiz (navigatsiya, saqlash, xatolarni ushlash, parallel bosishlar,
callback patternlari) to'g'riligini tasdiqlaydi, lekin Telegram serveri xatti-harakatining
o'zini simulyatsiya QILMAYDI. Real botda qo'lda smoke-test tavsiya etiladi.

Preview qismi (PreviewEngineTests) Telegram'ga umuman bog'liq emas va to'liq haqiqiy.
"""
import asyncio
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image
from pptx import Presentation

import slide_previews as sp
from slide_themes import THEMES, PALETTES, FRAMES, FONTS
from pptx_tools import build_presentation
from slide_images import make_illustration

_TMP = tempfile.TemporaryDirectory(prefix="px_preview_cache_")
sp.CACHE_DIR = _TMP.name                      # test keshi alohida papkada


def _px(jpeg_bytes, xy):
    return Image.open(io.BytesIO(jpeg_bytes)).convert("RGB").getpixel(xy)


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _close(a, b, tol=14):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# =============================================================================
# 1) Preview yaratish (haqiqiy, Telegram'siz)
# =============================================================================
class PreviewEngineTests(unittest.TestCase):
    def _assert_valid(self, data, label=""):
        self.assertTrue(data[:2] == b"\xff\xd8", f"{label}: JPEG emas")
        im = Image.open(io.BytesIO(data))
        self.assertEqual(im.size, (1280, 720), label)          # 16:9, 1280x720
        self.assertLess(len(data), 400_000, f"{label}: Telegram uchun juda katta")

    def test_counts_all_variants(self):
        seen = set()
        for i, n in enumerate(sp.COUNT_OPTIONS):
            data = sp.get_preview(sp.STEP_COUNT, i)
            self._assert_valid(data, f"count {n}")
            seen.add(hashlib.md5(data).hexdigest())
        self.assertEqual(sp.COUNT_OPTIONS, [5, 7, 10, 12, 15, 20, 25, 30])
        self.assertEqual(len(seen), 8, "Har bir son uchun namuna alohida bo'lishi kerak")

    def test_all_twenty_designs_have_distinct_previews(self):
        self.assertEqual(len(sp.DESIGN_KEYS), 20)
        seen = {}
        for i, key in enumerate(sp.DESIGN_KEYS):
            data = sp.get_preview(sp.STEP_DESIGN, i)
            self._assert_valid(data, key)
            seen[key] = hashlib.md5(data).hexdigest()
        self.assertEqual(len(set(seen.values())), 20, "Hamma dizayn o'ziga xos bo'lishi kerak")

    def test_design_preview_uses_real_theme_background(self):
        # Interyer piksel (ramka ichida) dizayn foni bilan mos bo'lishi shart.
        for i, key in enumerate(sp.DESIGN_KEYS):
            bg = THEMES[key][1]
            px = _px(sp.get_preview(sp.STEP_DESIGN, i), (40, 400))
            self.assertTrue(_close(px, _hex_rgb(bg)), f"{key}: {px} != {bg}")

    def test_all_frames_have_distinct_previews(self):
        seen = set()
        for i, key in enumerate(sp.FRAME_KEYS):
            data = sp.get_preview(sp.STEP_FRAME, i, {"design": "academic", "font": "Arial"})
            self._assert_valid(data, key)
            seen.add(hashlib.md5(data).hexdigest())
        self.assertEqual(len(sp.FRAME_KEYS), len(FRAMES))
        self.assertEqual(len(seen), len(FRAMES))

    def test_frame_preview_changes_only_the_frame(self):
        # none va thin: markaz (matn/rasm) bir xil, faqat chekka farq qiladi.
        a = Image.open(io.BytesIO(sp.get_preview(sp.STEP_FRAME, sp.FRAME_KEYS.index("none"), {"design": "academic"}))).convert("L")
        b = Image.open(io.BytesIO(sp.get_preview(sp.STEP_FRAME, sp.FRAME_KEYS.index("thin"), {"design": "academic"}))).convert("L")
        center = (150, 120, 1130, 600)
        ca, cb = a.crop(center), b.crop(center)
        diff_center = sum(abs(x - y) for x, y in zip(ca.getdata(), cb.getdata())) / (ca.width * ca.height)
        self.assertLess(diff_center, 2.0, "Markaz bir xil qolishi kerak")
        diff_all = sum(abs(x - y) for x, y in zip(a.getdata(), b.getdata())) / (a.width * a.height)
        self.assertGreater(diff_all, 0.0, "Ramka farqi ko'rinishi kerak")

    def test_all_palettes_including_design_default_and_custom(self):
        keys = sp.PALETTE_KEYS
        self.assertEqual(keys[0], "design_default")
        self.assertEqual(keys[-1], "custom")
        self.assertEqual(set(keys[1:-1]), set(PALETTES))
        seen = set()
        for i, key in enumerate(keys):
            data = sp.get_preview(sp.STEP_PALETTE, i, {"design": "modern_blue", "frame": "thin"})
            self._assert_valid(data, key)
            seen.add(hashlib.md5(data).hexdigest())
        self.assertEqual(len(seen), len(keys))

    def test_palette_preview_matches_real_palette_colors(self):
        # Pastdagi 3 ta chip: Fon / Sarlavha va matn / Urg'u — PALETTES bilan bir xil ranglar.
        for key in PALETTES:
            i = sp.PALETTE_KEYS.index(key)
            data = sp.get_preview(sp.STEP_PALETTE, i, {"design": "modern_blue", "frame": "thin"})
            _, bg, accent, fg = PALETTES[key]
            chips = [(120 + 14 + 35, 655), (120 + 14 + 35 + 344, 655), (120 + 14 + 35 + 688, 655)]
            x0 = (1280 - (3 * 320 + 2 * 24)) // 2
            for j, expected in enumerate((bg, fg, accent)):
                x = x0 + j * 344 + 14 + 35
                self.assertTrue(_close(_px(data, (x, 655)), _hex_rgb(expected)), f"{key} chip {j}")

    def test_all_fonts_have_previews(self):
        self.assertEqual(len(sp.FONT_KEYS), len(FONTS))
        digests = []
        for i, name in enumerate(FONTS):
            data = sp.get_preview(sp.STEP_FONT, i, {"design": "modern_blue", "frame": "thin", "palette": "design_default"})
            self._assert_valid(data, name)
            digests.append(hashlib.md5(data).hexdigest())
        self.assertGreaterEqual(len(set(digests)), 5, "Shriftlar orasida ko'zga ko'rinadigan farq bo'lishi kerak")

    def test_font_sample_text_is_the_required_uzbek_text(self):
        self.assertEqual(sp.SAMPLE_FONT_TEXT, "Zamonaviy texnologiyalar va ta'lim rivoji")

    def test_unknown_font_uses_safe_fallback_without_error(self):
        path, fam, exact = sp.resolve_font("Mavjud Bo'lmagan Shrift", False)
        self.assertFalse(exact)
        data = sp.get_preview(sp.STEP_FONT, 0, {})
        self._assert_valid(data)

    def test_final_preview_matches_selected_settings(self):
        s = {"design": "academic", "frame": "gold", "palette": "black_gold", "font": "Georgia"}
        data = sp.get_preview(sp.STEP_FONT, 0, s, final=True)
        self._assert_valid(data)
        self.assertTrue(_close(_px(data, (40, 400)), _hex_rgb(PALETTES["black_gold"][1])))

    def test_custom_hex_colors_flow_into_preview(self):
        s = {"design": "modern_blue", "frame": "thin", "palette": "custom",
             "custom_bg": "#123456", "custom_accent": "#AABBCC", "custom_text": "#FFEEDD"}
        data = sp.get_preview(sp.STEP_FONT, 0, s, final=True)
        self.assertTrue(_close(_px(data, (40, 400)), _hex_rgb("#123456")))

    def test_cache_memory_and_disk(self):
        key = sp.cache_key(sp.STEP_DESIGN, 3)
        a = sp.get_preview(sp.STEP_DESIGN, 3)
        self.assertTrue(os.path.exists(os.path.join(sp.CACHE_DIR, key + ".jpg")))
        with mock.patch.object(sp, "_render", side_effect=AssertionError("qayta chizilmasligi kerak")):
            self.assertEqual(sp.get_preview(sp.STEP_DESIGN, 3), a)           # xotira keshi
            sp._mem.clear()
            self.assertEqual(sp.get_preview(sp.STEP_DESIGN, 3), a)           # disk keshi

    def test_render_failure_returns_fallback_image_and_is_not_cached(self):
        unique = {"design": "gradient", "frame": "classic", "palette": "red_white"}
        with mock.patch.object(sp, "_render", side_effect=RuntimeError("ataylab")):
            data = sp.get_preview(sp.STEP_FONT, 5, unique)
        self._assert_valid(data)
        self.assertFalse(os.path.exists(os.path.join(sp.CACHE_DIR, sp.cache_key(sp.STEP_FONT, 5, unique) + ".jpg")))

    def test_callback_data_limits_and_parsing(self):
        total_checked = 0
        for step in sp.STEP_ORDER:
            for idx in range(sp.total(step)):
                for act in sp.ACTIONS:
                    cb = sp.make_cb(step, act, idx)
                    self.assertLessEqual(len(cb.encode()), 64)
                    self.assertEqual(sp.parse_cb(cb), (step, act, idx))
                    total_checked += 1
        self.assertGreater(total_checked, 100)
        for bad in (None, "", "px:dsg:n", "px:dsg:n:999", "px:zzz:n:1", "pptx:design_next", "px:dsg:x:1", "px:dsg:n:-1", "px:dsg:n:a"):
            self.assertIsNone(sp.parse_cb(bad), bad)

    def test_navigation_wraps_around(self):
        for step in sp.STEP_ORDER:
            n = sp.total(step)
            self.assertEqual(sp.nav_index(n - 1, "n", n), 0)          # oxirgidan birinchiga
            self.assertEqual(sp.nav_index(0, "p", n), n - 1)          # birinchidan oxirgiga
            self.assertEqual(sp.nav_index(2, "n", n), 3)
            self.assertEqual(sp.nav_index(2, "p", n), 1)

    def test_pptx_keeps_the_chosen_font_name(self):
        slides = [{"heading": "Kirish", "bullets": ["A", "B"], "layout": "split_right"}]
        img = {0: make_illustration("T", "H", "#1677C8", 0)}
        for font in FONTS:
            prs = Presentation(build_presentation("Mavzu", slides, "Student AI", {"design": "academic", "frame": "thin", "font": font}, img))
            names = {r.font.name for sl in prs.slides for sh in sl.shapes if sh.has_text_frame
                     for p in sh.text_frame.paragraphs for r in p.runs}
            self.assertEqual(names, {font}, f"{font} PPTX'ga to'g'ri yozilmadi: {names}")


# =============================================================================
# 2) Handlerlar — SOXTA Telegram qatlami bilan
# =============================================================================
class TelegramError(Exception):
    pass


class BadRequest(TelegramError):
    pass


class NetworkError(TelegramError):
    pass


class RetryAfter(TelegramError):
    def __init__(self, retry_after):
        super().__init__(f"Flood control, retry in {retry_after}")
        self.retry_after = retry_after


class _Btn:
    def __init__(self, text, callback_data=None, url=None):
        self.text, self.callback_data, self.url = text, callback_data, url


class _Markup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


class _InputFile:
    def __init__(self, obj, filename=None):
        self.obj = obj.read() if hasattr(obj, "read") else obj
        self.filename = filename


class _InputMediaPhoto:
    def __init__(self, media, caption=None, parse_mode=None):
        self.media, self.caption, self.parse_mode = media, caption, parse_mode


class _Photo:
    pass


_FAKED = ["telegram", "telegram.error", "telegram.ext", "telegram.constants", "config", "ai_clients", "storage",
          "handlers.menu", "handlers.wallet_ui", "handlers.pptx_gen", "slide_export_stub"]
_SAVED = {}
pptx_gen = None
FAKE_STATE = {"success": 0, "failure": 0, "usage": []}


def setUpModule():
    global pptx_gen
    for name in _FAKED:
        _SAVED[name] = sys.modules.pop(name, None)

    tg = types.ModuleType("telegram")
    tg.Update = type("Update", (), {})
    tg.InputFile, tg.InputMediaPhoto = _InputFile, _InputMediaPhoto
    tg.InlineKeyboardButton, tg.InlineKeyboardMarkup = _Btn, _Markup
    err = types.ModuleType("telegram.error")
    err.TelegramError, err.BadRequest, err.RetryAfter, err.NetworkError = TelegramError, BadRequest, RetryAfter, NetworkError
    ext = types.ModuleType("telegram.ext")
    ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    ext.ConversationHandler = types.SimpleNamespace(END=-1)
    const = types.ModuleType("telegram.constants")
    const.ParseMode = types.SimpleNamespace(HTML="HTML", MARKDOWN="Markdown")
    const.ChatAction = types.SimpleNamespace(UPLOAD_PHOTO="upload_photo")
    tg.error, tg.ext, tg.constants = err, ext, const
    sys.modules.update({"telegram": tg, "telegram.error": err, "telegram.ext": ext, "telegram.constants": const})

    cfg = types.ModuleType("config")
    cfg.PPTX_AI, cfg.KEY_POOLS = "fake", {}
    sys.modules["config"] = cfg

    async def ask_ai(provider, prompt, system):
        m = re.search(r"aynan (\d+) ta", prompt)
        n = int(m.group(1))
        return json.dumps({"slides": [{"heading": f"Bo‘lim {i + 1}", "bullets": ["Birinchi fikr", "Ikkinchi fikr"]} for i in range(n)],
                           "references": []})
    ai = types.ModuleType("ai_clients")
    ai.ask_ai = ask_ai
    sys.modules["ai_clients"] = ai

    st = types.ModuleType("storage")
    st.record_file = lambda *a, **k: FAKE_STATE["usage"].append(("file",) + a)
    st.record_usage = lambda *a, **k: FAKE_STATE["usage"].append(("usage",) + a)
    sys.modules["storage"] = st

    import handlers  # haqiqiy paket (bo'sh __init__)
    menu = types.ModuleType("handlers.menu")
    menu.main_menu_keyboard = lambda: _Markup([[_Btn("🏠 Menyu", "menu:main")]])
    menu._cancel_everything = lambda update, context: context.user_data.clear() or 0
    wallet = types.ModuleType("handlers.wallet_ui")

    async def fin_ok(context, update=None, chat_id=None, extra_note=""):
        FAKE_STATE["success"] += 1
        return True

    async def fin_fail(context, update=None, chat_id=None, reason="", silent_if_missing=False):
        FAKE_STATE["failure"] += 1
        return True
    wallet.finalize_success, wallet.finalize_failure = fin_ok, fin_fail
    sys.modules["handlers.menu"], sys.modules["handlers.wallet_ui"] = menu, wallet
    handlers.menu, handlers.wallet_ui = menu, wallet

    import importlib
    pptx_gen = importlib.import_module("handlers.pptx_gen")


def tearDownModule():
    for name in _FAKED:
        sys.modules.pop(name, None)
        if _SAVED.get(name) is not None:
            sys.modules[name] = _SAVED[name]
    h = sys.modules.get("handlers")
    if h is not None:
        for attr in ("menu", "wallet_ui", "pptx_gen"):
            if hasattr(h, attr) and attr in ("menu", "wallet_ui", "pptx_gen"):
                try:
                    delattr(h, attr)
                except AttributeError:
                    pass
    _TMP.cleanup()


class FakeBot:
    """Telegram Bot'ning yozib boruvchi nusxasi: xabarlar ombori + xato kiritish imkoniyati."""

    def __init__(self):
        self.messages = {}        # (chat_id, message_id) -> dict
        self.next_id = 100
        self.log = []
        self.edit_errors = []     # edit_message_media navbatdagi xatolar (Exception obyektlari)
        self.edit_delay = 0.0
        self.edit_calls = 0
        self.documents = []

    def _new(self, chat_id, **kw):
        self.next_id += 1
        self.messages[(chat_id, self.next_id)] = kw
        msg = FakeMessage(self, chat_id, self.next_id)
        msg.document = types.SimpleNamespace(file_id="FILEID")
        return msg

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        self.log.append(("send_photo", chat_id))
        return self._new(chat_id, kind="photo", data=photo.obj, caption=caption, kb=reply_markup)

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.log.append(("send_message", chat_id, text))
        return self._new(chat_id, kind="text", text=text, kb=reply_markup)

    async def send_document(self, chat_id, document, caption=None, reply_markup=None):
        self.log.append(("send_document", chat_id, document.filename))
        self.documents.append((document.filename, document.obj))
        return self._new(chat_id, kind="doc", caption=caption)

    async def edit_message_media(self, chat_id, message_id, media, reply_markup=None):
        self.edit_calls += 1
        if self.edit_delay:
            await asyncio.sleep(self.edit_delay)
        self.log.append(("edit_media", chat_id, message_id))
        if self.edit_errors:
            raise self.edit_errors.pop(0)
        if (chat_id, message_id) not in self.messages:
            raise BadRequest("Message to edit not found")
        assert isinstance(media.media, (bytes, bytearray)), "edit_media uchun media bytes bo'lishi shart"
        self.messages[(chat_id, message_id)].update(kind="photo", data=bytes(media.media), caption=media.caption, kb=reply_markup)

    async def edit_message_caption(self, chat_id, message_id, caption=None, parse_mode=None, reply_markup=None):
        self.log.append(("edit_caption", chat_id, message_id))
        self.messages[(chat_id, message_id)].update(caption=caption, kb=reply_markup)
        return True

    async def delete_message(self, chat_id, message_id):
        self.log.append(("delete", chat_id, message_id))
        if (chat_id, message_id) not in self.messages:
            raise BadRequest("Message to delete not found")
        del self.messages[(chat_id, message_id)]
        return True

    def alive(self, chat_id):
        return {k[1]: v for k, v in self.messages.items() if k[0] == chat_id}


class FakeMessage:
    def __init__(self, bot, chat_id, message_id, text=None):
        self.bot, self.chat_id, self.message_id, self.text = bot, chat_id, message_id, text

    async def delete(self):
        return await self.bot.delete_message(self.chat_id, self.message_id)

    async def reply_text(self, text, **kw):
        return await self.bot.send_message(self.chat_id, text)

    async def reply_chat_action(self, action):
        return True

    async def edit_text(self, text, reply_markup=None, **kw):
        msg = self.bot.messages.get((self.chat_id, self.message_id))
        if msg is None:
            raise BadRequest("Message to edit not found")
        msg.update(text=text, kb=reply_markup)


class FakeQuery:
    def __init__(self, bot, chat_id, message_id, data, answer_error=None):
        self.bot, self.data = bot, data
        self.message = FakeMessage(bot, chat_id, message_id)
        self.answers, self.answer_error = [], answer_error

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))
        if self.answer_error:
            raise self.answer_error

    async def edit_message_text(self, text, reply_markup=None, **kw):
        msg = self.bot.messages.get((self.message.chat_id, self.message.message_id))
        if msg is None or msg.get("kind") != "text":
            raise BadRequest("There is no text in the message to edit")
        msg.update(text=text, kb=reply_markup)

    async def edit_message_reply_markup(self, reply_markup=None):
        msg = self.bot.messages.get((self.message.chat_id, self.message.message_id))
        if msg is None:
            raise BadRequest("Message to edit not found")
        msg["kb"] = reply_markup


class FakeUpdate:
    def __init__(self, bot, chat_id, user_id, query=None, text=None, message_id=None):
        self.callback_query = query
        self.effective_user = types.SimpleNamespace(id=user_id)
        self.effective_chat = types.SimpleNamespace(id=chat_id)
        self.message = FakeMessage(bot, chat_id, message_id, text) if text is not None else None


class Harness:
    """Bitta foydalanuvchi uchun suhbat haydovchisi (ConversationHandler holatini o'zi yuritadi)."""

    def __init__(self, bot=None, chat_id=7, user_id=7):
        self.bot = bot or FakeBot()
        self.chat_id, self.user_id = chat_id, user_id
        self.context = types.SimpleNamespace(bot=self.bot, user_data={})
        self.state = None
        self.last_query = None
        self.all_callbacks = []

    def _record_kb(self, kb):
        if kb is None:
            return
        for row in kb.inline_keyboard:
            for b in row:
                if b.callback_data:
                    self.all_callbacks.append(b.callback_data)

    def sync_callbacks(self):
        for m in self.bot.messages.values():
            self._record_kb(m.get("kb"))

    # --- boshlash ---
    async def start(self):
        mid = self.bot._new(self.chat_id, kind="text", text="menu", kb=None).message_id
        q = FakeQuery(self.bot, self.chat_id, mid, "menu:pptx")
        self.state = await pptx_gen.entry(FakeUpdate(self.bot, self.chat_id, self.user_id, q), self.context)
        return mid

    async def send_text(self, text, mid=900):
        upd = FakeUpdate(self.bot, self.chat_id, self.user_id, text=text, message_id=mid)
        if self.state == pptx_gen.PX_TOPIC:
            self.state = await pptx_gen.receive_topic(upd, self.context)
        elif self.state == pptx_gen.PX_CUSTOM_COLOR:
            self.state = await pptx_gen.receive_custom_color(upd, self.context)
        else:
            raise AssertionError(f"matn kutilmagan holat: {self.state}")
        self.sync_callbacks()

    def wizard_msg(self):
        return self.context.user_data["px_msg_id"]

    def current(self):
        return self.bot.messages[(self.chat_id, self.wizard_msg())]

    async def press(self, data, message_id=None, answer_error=None):
        """Callback'ni mos holat handleriga yo'naltiradi (ConversationHandler vazifasi)."""
        mid = message_id or self.wizard_msg()
        q = FakeQuery(self.bot, self.chat_id, mid, data, answer_error)
        self.last_query = q
        upd = FakeUpdate(self.bot, self.chat_id, self.user_id, q)
        handler = self._route(data)
        new_state = await handler(upd, self.context)
        if new_state is not None:
            self.state = new_state
        self.sync_callbacks()
        return q

    def _route(self, data):
        P = pptx_gen
        if data == "cancel:all":
            return P.cancel_cb
        table = {P.PX_COUNT: (r"^px:cnt:", P.receive_count), P.PX_DESIGN: (r"^px:dsg:", P.choose_design),
                 P.PX_FRAME: (r"^px:frm:", P.choose_frame), P.PX_PALETTE: (r"^px:pal:", P.choose_palette),
                 P.PX_FONT: (r"^px:fnt:", P.choose_font), P.PX_CONFIRM: (r"^pptx:pdf:", P.choose_export)}
        if self.state in table and re.match(table[self.state][0], data):
            return table[self.state][1]
        return P.stale_cb                                       # fallbacks

    # --- qulay amallar ---
    def caption(self):
        return self.current()["caption"]

    def kb_data(self, which):
        kb = self.current()["kb"]
        flat = [b for row in kb.inline_keyboard for b in row]
        return {"p": flat[0].callback_data, "n": flat[1].callback_data, "ok": flat[2].callback_data}[which]

    async def nav(self, which):
        return await self.press(self.kb_data(which))

    async def goto(self, n_steps_next):
        for _ in range(n_steps_next):
            await self.nav("n")


def run(coro):
    return asyncio.run(coro)


class WizardFlowTests(unittest.TestCase):
    def setUp(self):
        FAKE_STATE.update(success=0, failure=0, usage=[])
        sp._mem.clear()

    async def _to_count(self, h):
        await h.start()
        await h.send_text("Sun’iy intellekt va ta’lim")

    # ---- 1. Tartib va vizual namuna ----
    def test_step_order_and_every_step_shows_a_photo_with_counter(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            seen = []
            expect = [("cnt", 8), ("dsg", 20), ("frm", 10), ("pal", 11), ("fnt", 8)]
            for step, n in expect:
                msg = h.current()
                self.assertEqual(msg["kind"], "photo")
                self.assertTrue(msg["data"][:2] == b"\xff\xd8")
                self.assertIn(f"Namuna 1 / {n}", msg["caption"])
                seen.append(h.context.user_data["px_step"])
                if step == "pal":
                    await h.nav("n")                     # 2-variantni tasdiqlaymiz (oddiy palitra)
                await h.press(h.kb_data("ok"))
            self.assertEqual(seen, [s for s, _ in expect])
            self.assertEqual(h.state, pptx_gen.PX_CONFIRM)
            final = h.current()
            self.assertEqual(final["kind"], "photo")
            for word in ("Mavzu:", "Slaydlar:", "Dizayn:", "Ramka:", "Ranglar:", "Shrift:", "PDF"):
                self.assertIn(word, final["caption"])
            labels = [b.callback_data for row in final["kb"].inline_keyboard for b in row]
            self.assertIn("pptx:pdf:yes", labels)
            self.assertIn("pptx:pdf:no", labels)
        run(go())

    def test_old_prompt_and_chat_stay_clean(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            for _ in range(4):
                await h.nav("n")
            await h.press(h.kb_data("ok"))
            await h.nav("n")
            await h.press(h.kb_data("ok"))
            alive = h.bot.alive(h.chat_id)
            photos = [m for m in alive.values() if m["kind"] == "photo"]
            self.assertEqual(len(photos), 1, "Chatda faqat BITTA namuna xabari bo'lishi kerak")
            sends = [e for e in h.bot.log if e[0] == "send_photo"]
            self.assertEqual(len(sends), 1, "Keyingi bosqichlar edit_media orqali — yangi xabar yuborilmaydi")
        run(go())

    # ---- 2. Keyingisi / Orqaga / aylanish ----
    def test_next_back_and_wraparound_for_every_step(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            for step, n in [("cnt", 8), ("dsg", 20), ("frm", 10), ("pal", 11), ("fnt", 8)]:
                self.assertIn(f"Namuna 1 / {n}", h.caption())
                await h.nav("n")
                self.assertIn(f"Namuna 2 / {n}", h.caption())
                await h.nav("p")
                self.assertIn(f"Namuna 1 / {n}", h.caption())
                await h.nav("p")                                       # birinchidan orqaga -> OXIRGI
                self.assertIn(f"Namuna {n} / {n}", h.caption())
                await h.nav("n")                                       # oxirgidan keyingisi -> BIRINCHI
                self.assertIn(f"Namuna 1 / {n}", h.caption())
                self.assertEqual(h.state, pptx_gen._STEP_STATE[step])
                await h.press(h.kb_data("ok"))
        run(go())

    def test_every_variant_of_every_step_can_be_reached_and_rendered(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            for step, n in [("cnt", 8), ("dsg", 20), ("frm", 10), ("pal", 11), ("fnt", 8)]:
                digests = set()
                for i in range(n):
                    self.assertIn(f"Namuna {i + 1} / {n}", h.caption())
                    self.assertEqual(h.current()["data"][:2], b"\xff\xd8")
                    digests.add(hashlib.md5(h.current()["data"]).hexdigest())
                    await h.nav("n")
                self.assertEqual(len(digests), n, f"{step}: har bir namuna rasmi alohida bo'lishi kerak")
                self.assertIn(f"Namuna 1 / {n}", h.caption())          # to'liq aylana
                if step == "pal":
                    await h.nav("n")
                await h.press(h.kb_data("ok"))
        run(go())

    # ---- 3. Tasdiqlash faqat ekrandagi variantni saqlaydi; navigatsiya sozlamani o'zgartirmaydi ----
    def test_navigation_does_not_change_settings_and_confirm_saves_displayed(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            await h.nav("n"); await h.nav("n")                  # 10 ta
            self.assertNotIn("px_count", h.context.user_data)    # hali tasdiqlanmagan
            await h.press(h.kb_data("ok"))
            self.assertEqual(h.context.user_data["px_count"], 10)
            s = h.context.user_data["pptx_settings"]
            self.assertEqual(s["design"], "modern_blue")
            for _ in range(6):
                await h.nav("n")
            self.assertEqual(s["design"], "modern_blue", "Ko'rish paytida dizayn o'zgarmasligi kerak")
            shown = list(THEMES)[6]
            self.assertIn(THEMES[shown][0], h.caption())
            await h.press(h.kb_data("ok"))
            self.assertEqual(s["design"], shown)
        run(go())

    def test_confirm_from_stale_keyboard_is_rejected(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            old_ok = h.kb_data("ok")                    # 1-variant uchun tasdiqlash tugmasi
            await h.nav("n")                            # ekranda endi 2-variant
            await h.press(old_ok)                       # eskirgan «Tasdiqlash»
            self.assertNotIn("px_count", h.context.user_data)
            self.assertEqual(h.state, pptx_gen.PX_COUNT)
        run(go())

    def test_user_cannot_skip_ahead_without_confirming(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            # keyingi bosqich tugmasini qo'lda yuborish (tasdiqlanmagan) — holat o'zgarmaydi
            q = await h.press(sp.make_cb("dsg", "ok", 0))
            self.assertEqual(h.state, pptx_gen.PX_COUNT)
            self.assertNotIn("px_count", h.context.user_data)
            self.assertEqual(q.answers[-1][1], True)             # ogohlantirish
        run(go())

    # ---- 4. Callback xavfsizligi ----
    def test_all_callback_data_within_64_bytes_and_registered_for_state(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            for step, n in [("cnt", 8), ("dsg", 20), ("frm", 10), ("pal", 11), ("fnt", 8)]:
                for _ in range(n):
                    await h.nav("n")
                if step == "pal":
                    await h.nav("n")
                await h.press(h.kb_data("ok"))
            h.sync_callbacks()
            self.assertGreater(len(h.all_callbacks), 50)
            for cb in h.all_callbacks:
                self.assertLessEqual(len(cb.encode()), 64, cb)
        run(go())

    def test_bot_py_patterns_match_all_real_callbacks_and_nothing_else(self):
        src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
        block = src[src.index("def build_pptx_conv"):src.index("def build_essay_conv")]
        pats = dict(re.findall(r'pptx_gen\.(PX_[A-Z_]+): \[CallbackQueryHandler\(pptx_gen\.\w+, pattern=r?"([^"]+)"\)\]', block))
        self.assertEqual(set(pats), {"PX_COUNT", "PX_DESIGN", "PX_FRAME", "PX_PALETTE", "PX_FONT", "PX_CONFIRM"})
        by_step = {"cnt": "PX_COUNT", "dsg": "PX_DESIGN", "frm": "PX_FRAME", "pal": "PX_PALETTE", "fnt": "PX_FONT"}
        for step, state in by_step.items():
            for idx in range(sp.total(step)):
                for act in sp.ACTIONS:
                    cb = sp.make_cb(step, act, idx)
                    self.assertTrue(re.match(pats[state], cb), (state, cb))
                    for other, ostate in by_step.items():
                        if other != step:
                            self.assertFalse(re.match(pats[ostate], cb), (ostate, cb))
        kb = pptx_gen._confirm_keyboard()
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertTrue(re.match(pats["PX_CONFIRM"], "pptx:pdf:yes") and re.match(pats["PX_CONFIRM"], "pptx:pdf:no"))
        self.assertIn("cancel:all", data)
        self.assertIn('pptx_gen.cancel_cb, pattern=r"^cancel:all$"', block)
        self.assertIn("pptx_gen.stale_cb", block)
        self.assertIn("pptx_gen.receive_custom_color", block)
        self.assertNotIn("design_prev", block)                    # eski 20 tugmali ro'yxat/callbacklar olib tashlangan

    def test_query_answer_called_on_every_callback_and_failures_are_survived(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            q = await h.nav("n")
            self.assertEqual(len(q.answers), 1)
            q = await h.press(h.kb_data("n"), answer_error=BadRequest("Query is too old and response timeout expired"))
            self.assertEqual(len(q.answers), 1)                    # xato yutildi, bot yiqilmadi
            self.assertIn("Namuna 3 / 8", h.caption())
        run(go())

    def test_rapid_taps_do_not_run_in_parallel(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            h.bot.edit_delay = 0.05
            data = h.kb_data("n")
            before = h.bot.edit_calls
            qs = await asyncio.gather(*[h.press(data) for _ in range(6)])
            self.assertEqual(h.bot.edit_calls - before, 1, "Faqat bitta so'rov bajarilishi kerak")
            for q in qs:
                self.assertEqual(len(q.answers), 1)                # hammasi javob oldi (spinner qotib qolmaydi)
            self.assertIn("Namuna 2 / 8", h.caption())
            self.assertFalse(pptx_gen._user_lock(7).locked())
        run(go())

    def test_different_users_do_not_block_each_other(self):
        async def go():
            bot = FakeBot()
            a, b = Harness(bot, chat_id=1, user_id=1), Harness(bot, chat_id=2, user_id=2)
            await self._to_count(a)
            await self._to_count(b)
            bot.edit_delay = 0.03
            before = bot.edit_calls
            await asyncio.gather(a.nav("n"), b.nav("n"))
            self.assertEqual(bot.edit_calls - before, 2, "Ikkala foydalanuvchi ham o'z namunasini olishi kerak")
            self.assertIn("Namuna 2 / 8", a.caption())
            self.assertIn("Namuna 2 / 8", b.caption())
        run(go())

    # ---- 5. edit_media xatolari va zaxira ----
    def test_edit_media_failure_falls_back_to_delete_and_send(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            old = h.wizard_msg()
            h.bot.edit_errors = [BadRequest("Message can't be edited")]
            await h.nav("n")
            self.assertNotEqual(h.wizard_msg(), old, "Yangi xabar yuborilgan bo'lishi kerak")
            self.assertNotIn((h.chat_id, old), h.bot.messages, "Eski xabar o'chirilgan bo'lishi kerak")
            self.assertIn("Namuna 2 / 8", h.caption())
            await h.nav("n")                                        # yangi xabardagi tugmalar ishlaydi
            self.assertIn("Namuna 3 / 8", h.caption())
        run(go())

    def test_message_not_modified_is_not_an_error(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            old = h.wizard_msg()
            h.bot.edit_errors = [BadRequest("Message is not modified: specified new message content and reply markup are exactly the same")]
            await h.nav("n")
            self.assertEqual(h.wizard_msg(), old)
            self.assertEqual([e for e in h.bot.log if e[0] == "send_photo"].__len__(), 1)
        run(go())

    def test_retry_after_is_retried_once_then_succeeds(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            h.bot.edit_errors = [RetryAfter(0.01)]
            old = h.wizard_msg()
            await h.nav("n")
            self.assertEqual(h.wizard_msg(), old)
            self.assertIn("Namuna 2 / 8", h.caption())
        run(go())

    def test_network_error_uses_fallback_and_errors_are_logged(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            h.bot.edit_errors = [NetworkError("timed out")]
            with self.assertLogs("handlers.pptx_gen", level="ERROR") as cm:
                await h.nav("n")
            self.assertTrue(any("edit_media" in line for line in cm.output))
            self.assertIn("Namuna 2 / 8", h.caption())
        run(go())

    def test_preview_render_failure_does_not_break_the_wizard(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            with mock.patch.object(sp, "_render", side_effect=RuntimeError("chizish xatosi")):
                sp._mem.clear()
                await h.nav("n")
                await h.nav("n")
            self.assertIn("Namuna 3 / 8", h.caption())
            self.assertEqual(h.current()["data"][:2], b"\xff\xd8")
            self.assertEqual(h.state, pptx_gen.PX_COUNT)
        run(go())

    # ---- 6. Eskirgan callback ----
    def test_stale_callback_from_old_message_is_rejected_quietly(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            old = h.wizard_msg()
            old_data = h.kb_data("n")
            h.bot.edit_errors = [BadRequest("Message can't be edited")]
            await h.nav("n")                                          # xabar almashdi
            cur_caption = h.caption()
            ghost = h.bot._new(h.chat_id, kind="photo", data=b"x", caption="old", kb=h.current()["kb"]).message_id
            q = await h.press(old_data, message_id=ghost)             # begona/eski xabardan bosish
            self.assertEqual(q.answers[-1][1], True)
            self.assertEqual(h.caption(), cur_caption)
            self.assertEqual(h.state, pptx_gen.PX_COUNT)
        run(go())

    def test_callback_after_session_cleared_does_not_crash(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            data, mid = h.kb_data("n"), h.wizard_msg()
            h.context.user_data.clear()
            q = await h.press(data, message_id=mid)
            self.assertEqual(h.state, -1)
            self.assertEqual(q.answers[-1][1], True)
            q2 = FakeQuery(h.bot, h.chat_id, mid, "px:dsg:n:3")        # suhbatdan tashqari global handler
            self.assertIsNone(await pptx_gen.stale_cb(FakeUpdate(h.bot, 7, 7, q2), h.context))
        run(go())

    def test_legacy_callbacks_from_old_version_are_handled_by_stale(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            q = await h.press("pptx:design_next")
            self.assertEqual(q.answers[-1][1], True)
            self.assertEqual(h.state, pptx_gen.PX_COUNT)
        run(go())

    # ---- 7. Bekor qilish va qayta boshlash ----
    def test_cancel_removes_photo_and_restart_works(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            await h.nav("n")
            await h.press("cancel:all")
            self.assertEqual(h.state, -1)
            self.assertEqual(h.context.user_data, {})
            alive = h.bot.alive(h.chat_id)
            self.assertFalse([m for m in alive.values() if m["kind"] == "photo"])
            self.assertTrue(any("Bekor qilindi" in str(m.get("text")) for m in alive.values()))
            # qayta boshlash
            await h.start()
            self.assertEqual(h.state, pptx_gen.PX_TOPIC)
            await h.send_text("Yangi mavzu")
            self.assertIn("Namuna 1 / 8", h.caption())
            self.assertEqual(h.context.user_data["pptx_settings"]["design"], "modern_blue")
        run(go())

    # ---- 8. HEX ranglar ----
    def test_custom_hex_flow(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            for _ in range(3):
                await h.press(h.kb_data("ok"))                       # count, design, frame
            for _ in range(10):
                await h.nav("n")                                      # 11-variant: «O‘z ranglarim (HEX)»
            self.assertIn("O‘z ranglarim (HEX)", h.caption())
            self.assertIn("Namuna 11 / 11", h.caption())
            await h.press(h.kb_data("ok"))
            self.assertEqual(h.state, pptx_gen.PX_CUSTOM_COLOR)
            self.assertIn("HEX", h.current()["caption"])
            await h.send_text("qizil sariq")                          # noto'g'ri
            self.assertEqual(h.state, pptx_gen.PX_CUSTOM_COLOR)
            await h.send_text("#102a43  38bdf8 #FFFFFF")              # to'g'ri (bittasi # siz)
            self.assertEqual(h.state, pptx_gen.PX_FONT)
            s = h.context.user_data["pptx_settings"]
            self.assertEqual((s["custom_bg"], s["custom_accent"], s["custom_text"]), ("#102A43", "#38BDF8", "#FFFFFF"))
            self.assertEqual(s["palette"], "custom")
            self.assertIn("Namuna 1 / 8", h.caption())
            self.assertTrue(_close(_px(h.current()["data"], (40, 400)), _hex_rgb("#102A43")))
            # xato xabari qolib ketmagan
            self.assertFalse([m for m in h.bot.alive(h.chat_id).values() if m.get("kind") == "text" and "HEX rang kiriting" in str(m.get("text"))])
        run(go())

    def test_choosing_a_normal_palette_clears_old_custom_colors(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            h.context.user_data["pptx_settings"].update(custom_bg="#000000", custom_accent="#111111", custom_text="#222222")
            for _ in range(3):
                await h.press(h.kb_data("ok"))
            await h.nav("n")
            await h.press(h.kb_data("ok"))
            s = h.context.user_data["pptx_settings"]
            self.assertEqual(s["palette"], "blue_white")
            self.assertNotIn("custom_bg", s)
        run(go())

    # ---- 9. Yakuniy: PPTX / PDF ----
    async def _full_flow(self, pdf, design_steps=6, frame_steps=3, palette_steps=2, font_steps=3, count_steps=2):
        h = Harness()
        await self._to_count(h)
        for steps in (count_steps, design_steps, frame_steps, palette_steps, font_steps):
            for _ in range(steps):
                await h.nav("n")
            await h.press(h.kb_data("ok"))
        self.assertEqual(h.state, pptx_gen.PX_CONFIRM)
        await h.press("pptx:pdf:yes" if pdf else "pptx:pdf:no")
        return h

    def test_selected_parameters_reach_the_generated_pptx(self):
        async def go():
            h = await self._full_flow(pdf=False)
            self.assertEqual(h.state, -1)
            self.assertEqual(len(h.bot.documents), 1)
            name, blob = h.bot.documents[0]
            self.assertTrue(name.endswith(".pptx"))
            prs = Presentation(io.BytesIO(blob))
            self.assertEqual(len(prs.slides), 10)                      # 3-variant = 10 ta
            design = list(THEMES)[6]; palette = list(PALETTES)[1]; font = FONTS[3]
            bg = prs.slides[1].background.fill.fore_color.rgb
            self.assertEqual(str(bg), PALETTES[palette][1].lstrip("#"))
            fonts = {r.font.name for sh in prs.slides[1].shapes if sh.has_text_frame for p in sh.text_frame.paragraphs for r in p.runs}
            self.assertIn(font, fonts)
            self.assertEqual(FAKE_STATE["success"], 1)
            self.assertEqual(FAKE_STATE["failure"], 0)
            self.assertTrue(FAKE_STATE["usage"])                      # sent_pptx xatosi tuzatilgan: yozuv saqlandi
            self.assertEqual(h.context.user_data, {})
            alive = h.bot.alive(h.chat_id)
            self.assertFalse([m for m in alive.values() if m["kind"] == "photo"], "Namuna xabari tozalangan bo'lishi kerak")
        run(go())

    def test_pdf_is_created_when_requested(self):
        async def go():
            h = await self._full_flow(pdf=True)
            names = [n for n, _ in h.bot.documents]
            self.assertEqual(len(names), 2)
            self.assertTrue(names[0].endswith(".pptx") and names[1].endswith(".pdf"))
            self.assertTrue(h.bot.documents[1][1].startswith(b"%PDF"))
        run(go())

    def test_double_tap_on_export_builds_only_once(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            for _ in range(5):
                await h.press(h.kb_data("ok"))
            self.assertEqual(h.state, pptx_gen.PX_CONFIRM)
            mid = h.wizard_msg()
            await asyncio.gather(h.press("pptx:pdf:no", message_id=mid), h.press("pptx:pdf:no", message_id=mid))
            self.assertEqual(len([n for n, _ in h.bot.documents if n.endswith(".pptx")]), 1)
            self.assertEqual(FAKE_STATE["success"], 1)
        run(go())

    def test_generation_failure_reports_and_releases(self):
        async def go():
            h = Harness()
            await self._to_count(h)
            for _ in range(5):
                await h.press(h.kb_data("ok"))
            with mock.patch.object(pptx_gen, "_generate_slides", side_effect=RuntimeError("AI ishlamadi")):
                await h.press("pptx:pdf:no")
            self.assertEqual(FAKE_STATE["failure"], 1)
            self.assertTrue(any("xatolik" in str(e) for e in h.bot.log if e[0] == "send_message"))
            self.assertEqual(h.state, -1)
        run(go())


if __name__ == "__main__":
    unittest.main()
