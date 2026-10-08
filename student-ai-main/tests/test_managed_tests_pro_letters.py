import asyncio
import sys
import types
import unittest

from tests._stub_telegram import install_stubs

install_stubs()

# handlers/managed_tests.py qo'shimcha telegram klasslarini import qiladi.
import telegram  # noqa: E402

for _name in ("InlineQueryResultArticle", "InputTextMessageContent"):
    if not hasattr(telegram, _name):
        setattr(telegram, _name, type(_name, (), {"__init__": lambda self, *a, **kw: None}))

# Ildizdagi `managed_tests` (DB) o'rniga sof xotiradagi soxta modul.
_fake_db = types.ModuleType("managed_tests")
_QUESTIONS = [(1, "ت", "تَمْ", ["tam", "tim", "tom", "tum"], 0)]
_fake_db.get_questions = lambda: list(_QUESTIONS)
sys.modules["managed_tests"] = _fake_db

from handlers import managed_tests as mt  # noqa: E402


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edits = []
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append((text, reply_markup))


class FakeUpdate:
    def __init__(self, data):
        self.callback_query = FakeQuery(data)


class Ctx:
    def __init__(self):
        self.user_data = {}


def press(ctx, data):
    upd = FakeUpdate(data)
    asyncio.run(mt.callback(upd, ctx))
    return upd.callback_query


def flat(markup):
    return [b for row in markup.inline_keyboard for b in row]


class ProLettersTest(unittest.TestCase):
    def test_keyboard_layout(self):
        kb = mt.pro_keyboard()
        self.assertTrue(all(len(row) <= 8 for row in kb.inline_keyboard))
        letters = [b.text for b in flat(kb) if b.callback_data.startswith("mt:k:")]
        self.assertEqual("".join(letters), "qwertyuiopasdfghjklzxvbnm")
        datas = [b.callback_data for b in flat(kb)]
        self.assertIn("mt:sub", datas)
        self.assertIn("mt:bk", datas)

    def _start(self, answer="tam"):
        _QUESTIONS[:] = [(1, "ت", "تَمْ", [answer, "x1", "x2", "x3"], 0)]
        ctx = Ctx()
        q = press(ctx, "mt:start:0:1:0")
        return ctx, q

    def _extras(self, q):
        return [b.text for b in flat(q.edits[-1][1]) if b.callback_data.startswith("mt:x:")]

    def test_start_shows_question_and_letters(self):
        ctx, q = self._start()
        text, markup = q.edits[-1]
        self.assertIn("تَمْ", text)
        self.assertEqual(ctx.user_data["mt_buf"], "")
        # "tam" javobida ch/sh/ng/c/o‘/' yo'q — qo'shimcha tugmalar chiqmaydi
        self.assertFalse([b for b in flat(markup) if b.callback_data.startswith("mt:x:")])
        self.assertIn("mt:sub", [b.callback_data for b in flat(markup)])
        self.assertFalse(ctx.user_data["mt_wait_text"])

    def test_correct_answer(self):
        ctx, _ = self._start()
        for ch in "tam":
            q = press(ctx, f"mt:k:{ch}")
        self.assertIn("<b>tam</b>", q.edits[-1][0])  # jonli ko'rinish
        q = press(ctx, "mt:sub")
        self.assertIn("To\u2018g\u2018ri javob!", q.edits[-1][0])
        self.assertEqual((ctx.user_data["mt_ok"], ctx.user_data["mt_bad"]), (1, 0))
        # Yuborilgandan keyin harflar va qayta yuborish e'tiborga olinmaydi
        q = press(ctx, "mt:k:x")
        self.assertEqual(q.edits, [])
        q = press(ctx, "mt:sub")
        self.assertEqual(q.edits, [])
        self.assertEqual(ctx.user_data["mt_ok"], 1)

    def test_wrong_answer_and_backspace(self):
        ctx, _ = self._start()
        for ch in "tomz":
            press(ctx, f"mt:k:{ch}")
        q = press(ctx, "mt:bk")
        self.assertIn("<b>tom</b>", q.edits[-1][0])
        q = press(ctx, "mt:sub")
        self.assertIn("Xato javob!", q.edits[-1][0])
        self.assertIn("<b>tam</b>", q.edits[-1][0])  # to'g'ri javob ko'rsatiladi
        self.assertEqual((ctx.user_data["mt_ok"], ctx.user_data["mt_bad"]), (0, 1))

    def test_empty_submit_is_ignored(self):
        ctx, _ = self._start()
        q = press(ctx, "mt:sub")  # bo'sh
        self.assertEqual(q.edits, [])
        self.assertEqual(ctx.user_data.get("mt_bad", 0), 0)
        self.assertIn("Avval harflarni tanlang", q.answers[-1])

    def test_next_resets_buffer(self):
        ctx, _ = self._start()
        for ch in "tam":
            press(ctx, f"mt:k:{ch}")
        press(ctx, "mt:sub")
        press(ctx, "mt:result")
        self.assertNotIn("mt_buf", ctx.user_data)

    def test_extras_only_when_in_answer(self):
        _, q = self._start("chol")
        self.assertEqual(self._extras(q), ["ch", "c"])
        _, q = self._start("shox")
        self.assertEqual(self._extras(q), ["sh"])
        _, q = self._start("tong")
        self.assertEqual(self._extras(q), ["ng"])
        _, q = self._start("o\u2018rik")   # o‘
        self.assertEqual(self._extras(q), ["o\u2018", "\u2018"])
        _, q = self._start("g'oz")
        self.assertEqual(self._extras(q), ["\u2018"])
        _, q = self._start("chang o'g'il")
        self.assertEqual(self._extras(q), ["ch", "ng", "c", "o\u2018", "\u2018"])

    def test_extras_row_fits_telegram_limit(self):
        _, q = self._start("chang o'g'il sh")
        self.assertTrue(all(len(r) <= 8 for r in q.edits[-1][1].inline_keyboard))

    def test_type_word_with_extras(self):
        ctx, q = self._start("Cho\u02bbl")   # apostrof boshqa ko'rinishda saqlangan (ʻ)
        idx = {b.text: b.callback_data for b in flat(q.edits[-1][1])}
        press(ctx, idx["ch"])
        press(ctx, idx["o\u2018"])
        q = press(ctx, "mt:k:l")
        self.assertIn("<b>cho\u2018l</b>", q.edits[-1][0])
        q = press(ctx, "mt:sub")
        self.assertIn("To\u2018g\u2018ri javob!", q.edits[-1][0])

    def test_backspace_removes_whole_extra_button(self):
        ctx, q = self._start("chol")
        idx = {b.text: b.callback_data for b in flat(q.edits[-1][1])}
        press(ctx, idx["ch"])
        press(ctx, "mt:k:o")
        q = press(ctx, "mt:bk")
        self.assertIn("<b>ch</b>", q.edits[-1][0])
        q = press(ctx, "mt:bk")          # 'ch' bitta bosish — butunlay o'chadi
        self.assertIn("…", q.edits[-1][0])

    def test_extra_not_in_answer_is_rejected(self):
        ctx, _ = self._start("tam")
        q = press(ctx, "mt:x:0")         # 'ch' bu savolda ko'rsatilmagan
        self.assertEqual(q.edits, [])
        self.assertEqual(ctx.user_data["mt_buf"], "")


if __name__ == "__main__":
    unittest.main()
