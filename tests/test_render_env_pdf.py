import asyncio
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import render_env_pdf as env_pdf  # noqa: E402

try:  # httpx yo'q muhitda (offline sandbox) API testlari o'tkazib yuboriladi
    import render_api  # noqa: E402
except Exception:  # pragma: no cover
    render_api = None

PEM = "-----BEGIN KEY-----\n" + "\n".join("MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" for _ in range(40)) + "\n-----END KEY-----\n"

ITEMS = [
    {"key": "BOT_TOKEN", "value": "123456:ABC-def_GHI"},
    {"key": "TELEGRAM_TOKEN", "value": "7777777777:AAHsecretSECRETsecretSECRETsecret123"},
    {"key": "MY_ODD_NAME", "value": "8888888888:AAGodd-name_ODDname_ODDname_ODD12345"},
    {"key": "TELEGRAM_BOT_TOKEN", "value": None},
    {"key": "DATABASE_URL", "value": "postgresql://u:p%40ss@h:5432/db?a=1&b=<x>&c=\"'"},
    {"key": "PEM", "value": PEM},
    {"key": "UZ", "value": "o‘zbek ʻtil — «test» 😀"},
    {"key": "EMPTY", "value": ""},
    {"key": "SPACES", "value": "  lead and trail  "},
    {"key": "LONG", "value": "A1b2" * 3000},
    {"key": "RENDER_API_KEY2", "value": "rnd_secret"},
    {"key": "GENERATED", "value": None},
    {"key": "BAD-KEY", "value": "x"},
]
SERVICE = {"id": "srv-abc123", "name": "student-ai"}
BOT_TOKEN_KEYS = ["BOT_TOKEN", "TELEGRAM_TOKEN", "MY_ODD_NAME"]
EXPECTED = {i["key"]: i["value"] for i in ITEMS if i["value"] is not None and i["key"] not in BOT_TOKEN_KEYS}


def _strip_attachments(pdf: bytes) -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(pdf)).pages:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class EnvPdfRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf, cls.info = env_pdf.build_env_pdf(SERVICE, ITEMS)

    def _vars(self, data: bytes) -> dict[str, str]:
        return {v["key"]: v["value"] for v in env_pdf.parse_env_pdf(data)["vars"]}

    def test_bot_token_is_never_in_pdf(self):
        from pypdf import PdfReader
        self.assertEqual(sorted(self.info["excluded"]), sorted(BOT_TOKEN_KEYS))
        vars_ = self._vars(self.pdf)
        for k in BOT_TOKEN_KEYS:
            self.assertNotIn(k, vars_)
        secrets = ("123456:ABC-def_GHI", "AAHsecretSECRET", "AAGodd-name")
        reader = PdfReader(io.BytesIO(self.pdf))
        visible = "".join(p.extract_text() or "" for p in reader.pages)
        blob = self.pdf + b"".join(b for v in reader.attachments.values() for b in v)
        for sec in secrets:
            self.assertNotIn(sec, visible)
            self.assertNotIn(sec.encode(), blob)
        # tushunarli izoh bor, "missing" ro'yxatida ham token nomi yo'q
        self.assertIn("bot tokeni", visible)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", self.info["missing"])

    def test_is_bot_token(self):
        self.assertTrue(env_pdf.is_bot_token("telegram_token"))
        self.assertTrue(env_pdf.is_bot_token("X", "123456789:" + "A" * 35))
        for k in ("GITHUB_TOKEN", "UPSTASH_REDIS_REST_TOKEN", "KINO_STREAM_TOKEN_SECRET", "TG_SESSION", "DATABASE_URL"):
            self.assertFalse(env_pdf.is_bot_token(k, "x"))

    def test_info_reports_count_and_missing(self):
        self.assertEqual(self.info["count"], len(EXPECTED))
        self.assertEqual(self.info["missing"], ["GENERATED"])

    def test_round_trip_via_attachment_is_exact(self):
        self.assertEqual(self._vars(self.pdf), EXPECTED)

    def test_round_trip_via_text_backup_is_exact(self):
        # PDF qayta saqlanib biriktirma yo'qolsa ham ma'lumot tiklanadi.
        self.assertEqual(self._vars(_strip_attachments(self.pdf)), EXPECTED)

    def test_split_valid_flags_bad_names_and_render_credentials(self):
        payload = env_pdf.parse_env_pdf(self.pdf)
        valid, invalid = env_pdf.split_valid(payload["vars"])
        self.assertEqual(invalid, ["BAD-KEY"])
        self.assertEqual(env_pdf.render_credential_keys(valid), ["RENDER_API_KEY2"])
        self.assertNotIn("BAD-KEY", dict(valid))

    def test_garbage_and_foreign_pdfs_are_rejected_with_user_message(self):
        with self.assertRaises(env_pdf.EnvPdfError):
            env_pdf.parse_env_pdf(b"not a pdf")
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(50, 800, "hello")
        c.save()
        with self.assertRaises(env_pdf.EnvPdfError):
            env_pdf.parse_env_pdf(buf.getvalue())

    def test_tampered_payload_fails_checksum(self):
        payload = env_pdf.parse_env_pdf(self.pdf)
        payload["vars"][0]["value"] = "changed"
        with self.assertRaises(env_pdf.EnvPdfError):
            env_pdf._validate_payload(payload)

    def test_safe_filename(self):
        name = env_pdf.safe_filename({"name": "My Service/../x"})
        self.assertTrue(name.startswith("env_My_Service_") and name.endswith(".pdf"))
        self.assertNotIn("/", name)


@unittest.skipIf(render_api is None, "httpx o'rnatilmagan")
class ListEnvVarsAllTests(unittest.TestCase):
    def test_follows_cursor_until_short_page_and_dedupes(self):
        def page(start, n):
            return [{"envVar": {"key": f"K{i}", "value": str(i)}, "cursor": f"c{i}"} for i in range(start, start + n)]

        pages = [page(0, 100), page(100, 100), page(200, 7)]
        seen_cursors = []

        async def fake_request(method, path, *, api_key, params=None, json=None):
            seen_cursors.append((params or {}).get("cursor"))
            return pages.pop(0)

        async def run():
            with patch.object(render_api, "_request", side_effect=fake_request):
                return await render_api.list_env_vars_all("rnd_test", "srv-1")

        items = asyncio.run(run())
        self.assertEqual(len(items), 207)
        self.assertEqual(items[-1]["key"], "K206")
        self.assertEqual(seen_cursors, [None, "c99", "c199"])


if __name__ == "__main__":
    unittest.main()
