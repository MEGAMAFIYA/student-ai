import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

import webapp_security as ws

BOT_TOKEN = "123456:FAKE-TEST-TOKEN-not-real"


def _build_valid_init_data(user_id=42, username="testuser", auth_date=None, bot_token=BOT_TOKEN, extra=None):
    """Rasmiy Telegram algoritmiga muvofiq TO'G'RI imzolangan initData
    quradi — bu FAQAT test uchun (production kodida hech qachon bunday
    'imzolash' qilinmaydi, faqat Telegram kliyenti tomonidan yaratiladi)."""
    auth_date = auth_date if auth_date is not None else int(time.time())
    data = {
        "auth_date": str(auth_date),
        "query_id": "AAFoobar123",
        "user": json.dumps({"id": user_id, "first_name": "Test", "username": username}, separators=(",", ":")),
    }
    if extra:
        data.update(extra)
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    data["hash"] = computed_hash
    return urlencode(data)


class VerifyInitDataTests(unittest.TestCase):
    def test_valid_init_data_returns_user(self):
        init_data = _build_valid_init_data(user_id=555, username="davron")
        user = ws.verify_telegram_init_data(init_data, BOT_TOKEN)
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], 555)
        self.assertEqual(user["username"], "davron")

    def test_valid_init_data_exposes_query_id_for_inline_flow(self):
        # query_id — inline rejimda ochilgan Mini App uchun kerak
        # (answer_web_app_query chaqirish uchun), _build_valid_init_data
        # buni har doim qo'shadi.
        init_data = _build_valid_init_data(user_id=555)
        user = ws.verify_telegram_init_data(init_data, BOT_TOKEN)
        self.assertEqual(user["_query_id"], "AAFoobar123")

    def test_tampered_data_is_rejected(self):
        init_data = _build_valid_init_data(user_id=555)
        # Foydalanuvchi ID'sini imzolanmagan holda o'zgartirishga urinamiz.
        tampered = init_data.replace("%22id%22%3A+555", "%22id%22%3A+999").replace("id%22%3A555", "id%22%3A999")
        # Har ehtimolga qarshi, agar almashtirish ishlamagan bo'lsa ham hash mos kelmasligi kifoya.
        user = ws.verify_telegram_init_data(init_data[:-5] + "aaaaa", BOT_TOKEN)
        self.assertIsNone(user)

    def test_wrong_bot_token_is_rejected(self):
        init_data = _build_valid_init_data(user_id=555)
        user = ws.verify_telegram_init_data(init_data, "999999:OTHER-BOT-TOKEN")
        self.assertIsNone(user)

    def test_missing_hash_is_rejected(self):
        user = ws.verify_telegram_init_data("auth_date=123&user=%7B%7D", BOT_TOKEN)
        self.assertIsNone(user)

    def test_empty_input_is_rejected(self):
        self.assertIsNone(ws.verify_telegram_init_data("", BOT_TOKEN))
        self.assertIsNone(ws.verify_telegram_init_data(None, BOT_TOKEN))
        self.assertIsNone(ws.verify_telegram_init_data("abc", ""))

    def test_expired_auth_date_is_rejected(self):
        old_date = int(time.time()) - ws.MAX_INIT_DATA_AGE_SECONDS - 100
        init_data = _build_valid_init_data(user_id=555, auth_date=old_date)
        user = ws.verify_telegram_init_data(init_data, BOT_TOKEN)
        self.assertIsNone(user)

    def test_fresh_auth_date_is_accepted(self):
        init_data = _build_valid_init_data(user_id=555, auth_date=int(time.time()) - 10)
        user = ws.verify_telegram_init_data(init_data, BOT_TOKEN)
        self.assertIsNotNone(user)

    def test_missing_user_field_is_rejected(self):
        auth_date = str(int(time.time()))
        data = {"auth_date": auth_date, "query_id": "abc"}
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        data["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        user = ws.verify_telegram_init_data(urlencode(data), BOT_TOKEN)
        self.assertIsNone(user)


class RequestTokenTests(unittest.TestCase):
    def setUp(self):
        ws._REQUESTS.clear()

    def test_create_and_consume(self):
        rid = ws.create_request(chat_id=-100123, user_id=42)
        chat_id = ws.consume_request(rid, verified_user_id=42)
        self.assertEqual(chat_id, -100123)

    def test_cannot_consume_twice(self):
        rid = ws.create_request(chat_id=-100123, user_id=42)
        first = ws.consume_request(rid, verified_user_id=42)
        second = ws.consume_request(rid, verified_user_id=42)
        self.assertEqual(first, -100123)
        self.assertIsNone(second)

    def test_wrong_user_cannot_consume_someone_elses_request(self):
        """Bu — 'group A foydalanuvchisi B ning natijasini o'g'irlashi
        mumkin emas' talabining asosiy himoyasi."""
        rid = ws.create_request(chat_id=-100123, user_id=42)
        stolen = ws.consume_request(rid, verified_user_id=999)
        self.assertIsNone(stolen)
        # Asl foydalanuvchi hali ham muvaffaqiyatli ishlata olishi kerak
        # (noto'g'ri urinish tokenni "ishlatilgan" deb belgilamasligi kerak).
        legit = ws.consume_request(rid, verified_user_id=42)
        self.assertEqual(legit, -100123)

    def test_unknown_rid_returns_none(self):
        self.assertIsNone(ws.consume_request("doesnotexist", verified_user_id=42))

    def test_expired_request_is_rejected(self):
        rid = ws.create_request(chat_id=-100123, user_id=42)
        ws._REQUESTS[rid]["created_at"] = time.time() - ws.REQUEST_TTL_SECONDS - 10
        self.assertIsNone(ws.consume_request(rid, verified_user_id=42))

    def test_different_requests_get_different_ids(self):
        rid1 = ws.create_request(chat_id=1, user_id=1)
        rid2 = ws.create_request(chat_id=2, user_id=2)
        self.assertNotEqual(rid1, rid2)

    def test_max_requests_evicts_oldest(self):
        old_max = ws.MAX_REQUESTS
        ws.MAX_REQUESTS = 5
        try:
            ids = [ws.create_request(chat_id=i, user_id=i) for i in range(10)]
            self.assertLessEqual(len(ws._REQUESTS), 5)
            self.assertIsNone(ws.consume_request(ids[0], verified_user_id=0))
            self.assertEqual(ws.consume_request(ids[-1], verified_user_id=9), 9)
        finally:
            ws.MAX_REQUESTS = old_max


class InlineRequestTokenTests(unittest.TestCase):
    """🔍 /rasim inline rejimda (chat_id'siz, faqat user_id bilan) — bular
    do'st bilan shaxsiy chatda '@Bot /rasim' orqali ochilgan Mini App
    uchun ishlatiladi (webapp_security.consume_inline_request)."""

    def setUp(self):
        ws._INLINE_REQUESTS.clear()

    def test_rid_has_inline_prefix(self):
        rid = ws.create_inline_request(user_id=42)
        self.assertTrue(rid.startswith("in_"))

    def test_create_and_consume(self):
        rid = ws.create_inline_request(user_id=42)
        self.assertTrue(ws.consume_inline_request(rid, verified_user_id=42))

    def test_cannot_consume_twice(self):
        rid = ws.create_inline_request(user_id=42)
        self.assertTrue(ws.consume_inline_request(rid, verified_user_id=42))
        self.assertFalse(ws.consume_inline_request(rid, verified_user_id=42))

    def test_wrong_user_cannot_consume_someone_elses_request(self):
        rid = ws.create_inline_request(user_id=42)
        self.assertFalse(ws.consume_inline_request(rid, verified_user_id=999))
        # Asl foydalanuvchi hali ham muvaffaqiyatli ishlata olishi kerak.
        self.assertTrue(ws.consume_inline_request(rid, verified_user_id=42))

    def test_unknown_rid_returns_false(self):
        self.assertFalse(ws.consume_inline_request("in_doesnotexist", verified_user_id=42))

    def test_expired_request_is_rejected(self):
        rid = ws.create_inline_request(user_id=42)
        ws._INLINE_REQUESTS[rid]["created_at"] = time.time() - ws.INLINE_REQUEST_TTL_SECONDS - 10
        self.assertFalse(ws.consume_inline_request(rid, verified_user_id=42))

    def test_max_inline_requests_evicts_oldest(self):
        old_max = ws.MAX_INLINE_REQUESTS
        ws.MAX_INLINE_REQUESTS = 5
        try:
            ids = [ws.create_inline_request(user_id=i) for i in range(10)]
            self.assertLessEqual(len(ws._INLINE_REQUESTS), 5)
            self.assertFalse(ws.consume_inline_request(ids[0], verified_user_id=0))
            self.assertTrue(ws.consume_inline_request(ids[-1], verified_user_id=9))
        finally:
            ws.MAX_INLINE_REQUESTS = old_max


if __name__ == "__main__":
    unittest.main()
