"""
BFL / Stability klientlari, kalit tekshiruvi, kurs ishi rasmlari va PDF joylashuvi.

Tarmoq YO'Q: image_clients._CLIENT_FACTORY orqali soxta HTTP klient ishlatiladi
(rasmiy hujjatdagi javob shakllari bo'yicha). Bu "haqiqiy BFL/Stability bilan
sinov" EMAS — faqat bizning mantiq (endpoint, sarlavha, polling, xato tasnifi,
xavfsizlik, kesh, fallback, PDF) to'g'riligini tekshiradi.
"""
import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._stub_telegram import install_stubs
install_stubs()

import config  # noqa: E402
import image_clients as ic  # noqa: E402
import course_images as ci  # noqa: E402
from PIL import Image  # noqa: E402

KEY = "sk-SECRET-KEY-123456"


def png_bytes(color="red", size=(64, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class FakeResp:
    def __init__(self, status=200, js=None, content=b"", headers=None, text=""):
        self.status_code, self._js, self.content = status, js, content
        self.headers = headers or {}
        self.text = text or (json.dumps(js) if js is not None else "")

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js


class FakeClient:
    """routes: [(method, substring, handler_or_resp)] — birinchi mos kelgani ishlaydi;
    handler list bo'lsa ketma-ket javoblar beriladi."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _do(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        for m, sub, resp in self.routes:
            if m == method and sub in url:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp(kw) if callable(resp) else resp
        return FakeResp(404, {"detail": "no route"})

    async def get(self, url, **kw):
        return await self._do("get", url, **kw)

    async def post(self, url, **kw):
        return await self._do("post", url, **kw)


def run(coro):
    return asyncio.run(coro)


class ClientTestBase(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(ic, "POLL_INTERVAL_SEC", 0.0),
            patch.object(ic, "RETRY_BASE_DELAY_SEC", 0.0),
            patch.object(ic, "POLL_TIMEOUT_SEC", 2.0),
        ]
        for p in self._patches:
            p.start()
        ic.clear_cache()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        ic._CLIENT_FACTORY = None

    def use(self, routes) -> FakeClient:
        fc = FakeClient(routes)
        ic._CLIENT_FACTORY = lambda: fc
        return fc


class BflTests(ClientTestBase):
    def routes_ok(self, pending=1):
        img = png_bytes()
        polls = [FakeResp(200, {"status": "Pending"}) for _ in range(pending)] + [
            FakeResp(200, {"status": "Ready", "result": {"sample": "https://delivery-eu1.bfl.ai/x.jpeg"}, "cost": 3.5})]
        return [
            ("post", "/v1/flux-2-pro", FakeResp(200, {"id": "t1", "polling_url": "https://api.eu.bfl.ai/v1/get_result?id=t1"})),
            ("get", "get_result", polls),
            ("get", "delivery-eu1.bfl.ai", FakeResp(200, content=img, headers={"content-type": "image/jpeg"})),
        ], img

    def test_success_polling_and_headers(self):
        routes, img = self.routes_ok(pending=2)
        fc = self.use(routes)
        res = run(ic.generate_image("bfl", KEY, "flux-2-pro", "A scientific illustration of classroom", "16:9"))
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(res.data, img)
        self.assertEqual(res.cost_credits, 3.5)
        submit = fc.calls[0]
        self.assertEqual(submit["url"], "https://api.bfl.ai/v1/flux-2-pro")
        self.assertEqual(submit["headers"]["x-key"], KEY)
        self.assertEqual(submit["json"]["width"], 1344)
        self.assertEqual(submit["json"]["height"], 768)
        # 3 ta polling (2 pending + 1 ready) + 1 yuklash; yuklashda KALIT yuborilmaydi
        download = fc.calls[-1]
        self.assertNotIn("headers", download)

    def test_cache_prevents_duplicate_api_calls(self):
        routes, _ = self.routes_ok(pending=0)
        fc = self.use(routes)
        run(ic.generate_image("bfl", KEY, "flux-2-pro", "same prompt here for cache", "16:9"))
        n = len(fc.calls)
        res = run(ic.generate_image("bfl", KEY, "flux-2-pro", "  Same Prompt Here For Cache ", "16:9"))
        self.assertTrue(res.ok)
        self.assertEqual(len(fc.calls), n, "takroriy prompt API'ga qayta yuborilmasligi kerak")

    def test_untrusted_polling_url_never_receives_key(self):
        fc = self.use([("post", "/v1/flux-2-pro", FakeResp(200, {"id": "x", "polling_url": "https://evil.example.com/steal"}))])
        res = run(ic.generate_image("bfl", KEY, "flux-2-pro", "prompt long enough to pass", "16:9"))
        self.assertEqual(res.status, "error")
        self.assertEqual(len(fc.calls), 1)

    def test_http_error_classification(self):
        cases = {401: "invalid", 403: "invalid", 402: "no_balance", 404: "model_not_found", 422: "bad_request"}
        for code, expected in cases.items():
            ic.clear_cache()
            self.use([("post", "/v1/flux-2-pro", FakeResp(code, {"detail": f"err {KEY}"}))])
            res = run(ic.generate_image("bfl", KEY, "flux-2-pro", f"prompt number {code} long", "16:9"))
            self.assertEqual(res.status, expected, code)
            self.assertNotIn(KEY, res.detail, "API kalit xato matniga chiqib ketmasligi kerak")

    def test_rate_limit_retries_then_succeeds(self):
        routes, _ = self.routes_ok(pending=0)
        routes[0] = ("post", "/v1/flux-2-pro", [FakeResp(429, {"detail": "busy"}),
                                                 FakeResp(200, {"id": "t", "polling_url": "https://api.bfl.ai/v1/get_result?id=t"})])
        fc = self.use(routes)
        res = run(ic.generate_image("bfl", KEY, "flux-2-pro", "retry prompt long enough", "16:9"))
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(sum(1 for c in fc.calls if c["method"] == "post"), 2)

    def test_persistent_429_is_quota(self):
        self.use([("post", "/v1/flux-2-pro", FakeResp(429, {"detail": "busy"}))])
        res = run(ic.generate_image("bfl", KEY, "flux-2-pro", "quota prompt long enough", "16:9"))
        self.assertEqual(res.status, "quota")

    def test_moderated_and_failed_statuses(self):
        for st, expected in (("Content Moderated", "moderated"), ("Request Moderated", "moderated"),
                             ("Error", "error"), ("Failed", "error"), ("Task not found", "error")):
            ic.clear_cache()
            self.use([
                ("post", "/v1/flux-dev", FakeResp(200, {"id": "t", "polling_url": "https://api.bfl.ai/v1/get_result?id=t"})),
                ("get", "get_result", FakeResp(200, {"status": st})),
            ])
            res = run(ic.generate_image("bfl", KEY, "flux-dev", f"status prompt {st} long", "16:9"))
            self.assertEqual(res.status, expected, st)

    def test_polling_timeout(self):
        self.use([
            ("post", "/v1/flux-dev", FakeResp(200, {"id": "t", "polling_url": "https://api.bfl.ai/v1/get_result?id=t"})),
            ("get", "get_result", FakeResp(200, {"status": "Pending"})),
        ])
        with patch.object(ic, "POLL_TIMEOUT_SEC", 0.05):
            res = run(ic.generate_image("bfl", KEY, "flux-dev", "timeout prompt long enough", "16:9"))
        self.assertEqual(res.status, "timeout")

    def test_network_exception_becomes_result_not_raise(self):
        class Boom(FakeClient):
            async def post(self, url, **kw):
                raise RuntimeError(f"connection failed for {KEY}")
        fc = Boom([])
        ic._CLIENT_FACTORY = lambda: fc
        res = run(ic.generate_image("bfl", KEY, "flux-dev", "exception prompt long enough", "16:9"))
        self.assertEqual(res.status, "error")
        self.assertNotIn(KEY, res.detail)

    def test_rejects_bad_model_name(self):
        res = run(ic.generate_image("bfl", KEY, "../../etc/passwd", "path injection prompt", "16:9"))
        self.assertEqual(res.status, "invalid")


class StabilityTests(ClientTestBase):
    def test_v2beta_core_request_shape(self):
        img = png_bytes("blue")
        fc = self.use([("post", "/v2beta/stable-image/generate/core",
                        FakeResp(200, content=img, headers={"content-type": "image/png", "finish-reason": "SUCCESS"}))])
        res = run(ic.generate_image("stability", KEY, "core", "stability core prompt long", "16:9"))
        self.assertTrue(res.ok, res.detail)
        call = fc.calls[0]
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertEqual(call["headers"]["Accept"], "image/*")
        self.assertEqual(call["data"]["aspect_ratio"], "16:9")
        self.assertIn("none", call["files"], "multipart/form-data majburlanishi kerak")

    def test_sd3_passes_model_and_ultra_path(self):
        fc = self.use([("post", "/generate/sd3", FakeResp(200, content=png_bytes(), headers={"content-type": "image/png"})),
                       ("post", "/generate/ultra", FakeResp(200, content=png_bytes(), headers={"content-type": "image/png"}))])
        run(ic.generate_image("stability", KEY, "sd3.5-large-turbo", "sd3 prompt long enough", "3:2"))
        self.assertEqual(fc.calls[0]["data"]["model"], "sd3.5-large-turbo")
        run(ic.generate_image("stability", KEY, "ultra", "ultra prompt long enough", "1:1"))
        self.assertTrue(fc.calls[1]["url"].endswith("/v2beta/stable-image/generate/ultra"))

    def test_sdxl_v1_json_request(self):
        fc = self.use([("post", "/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
                        FakeResp(200, content=png_bytes(), headers={"content-type": "image/png"}))])
        res = run(ic.generate_image("stability", KEY, "Stable Diffusion XL", "sdxl prompt long enough", "16:9"))
        self.assertTrue(res.ok, res.detail)
        body = fc.calls[0]["json"]
        self.assertEqual((body["width"], body["height"]), (1344, 768))
        self.assertEqual(body["text_prompts"][0]["text"], "sdxl prompt long enough")

    def test_errors(self):
        for code, expected in {401: "invalid", 402: "no_balance", 403: "moderated", 400: "bad_request",
                               413: "bad_request", 429: "quota", 500: "error"}.items():
            ic.clear_cache()
            self.use([("post", "/generate/core", FakeResp(code, {"errors": ["x"]}))])
            res = run(ic.generate_image("stability", KEY, "core", f"error prompt {code} long", "16:9"))
            self.assertEqual(res.status, expected, code)

    def test_v1_403_is_invalid_key_not_moderation(self):
        self.use([("post", "/text-to-image", FakeResp(403, {"message": "denied"}))])
        res = run(ic.generate_image("stability", KEY, "stable-diffusion-xl-1024-v1-0", "v1 forbidden prompt long", "16:9"))
        self.assertEqual(res.status, "invalid")

    def test_content_filtered_header(self):
        self.use([("post", "/generate/core", FakeResp(200, content=png_bytes(), headers={"finish-reason": "CONTENT_FILTERED"}))])
        res = run(ic.generate_image("stability", KEY, "core", "filtered prompt long enough", "16:9"))
        self.assertEqual(res.status, "moderated")

    def test_unknown_model(self):
        res = run(ic.generate_image("stability", KEY, "dalle-9", "unknown model prompt long", "16:9"))
        self.assertEqual(res.status, "model_not_found")


class KeyCheckTests(ClientTestBase):
    def test_bfl_ok_reports_balance_and_probes_model_without_generating(self):
        fc = self.use([("get", "/v1/credits", FakeResp(200, {"credits": 120.5})),
                       ("post", "/v1/flux-2-pro", FakeResp(422, {"detail": [{"msg": "field required"}]}))])
        st, det = run(ic.test_image_key("bfl", KEY, "flux-2-pro"))
        self.assertEqual(st, "ok")
        self.assertIn("120.5", det)
        probe = [c for c in fc.calls if c["method"] == "post"][0]
        self.assertEqual(probe["json"], {}, "tekshiruv rasm yaratmasligi (bo'sh tana) kerak")

    def test_bfl_invalid_key_zero_balance_missing_model(self):
        self.use([("get", "/v1/credits", FakeResp(401, {"detail": "bad"}))])
        self.assertEqual(run(ic.test_image_key("bfl", KEY, "flux-2-pro"))[0], "invalid")
        self.use([("get", "/v1/credits", FakeResp(200, {"credits": 0}))])
        self.assertEqual(run(ic.test_image_key("bfl", KEY, "flux-2-pro"))[0], "no_balance")
        self.use([("get", "/v1/credits", FakeResp(200, {"credits": 5})), ("post", "/v1/nope-model", FakeResp(404, {}))])
        self.assertEqual(run(ic.test_image_key("bfl", KEY, "nope-model"))[0], "model_not_found")

    def test_stability_checks(self):
        self.use([("get", "/v1/user/balance", FakeResp(200, {"credits": 42})),
                  ("post", "/generate/core", FakeResp(400, {"errors": ["prompt: required"]}))])
        st, det = run(ic.test_image_key("stability", KEY, "core"))
        self.assertEqual(st, "ok")
        self.assertIn("42", det)
        self.use([("get", "/v1/user/balance", FakeResp(401, {"name": "unauthorized"}))])
        self.assertEqual(run(ic.test_image_key("stability", KEY, "core"))[0], "invalid")
        self.use([("get", "/v1/user/balance", FakeResp(200, {"credits": 3})),
                  ("get", "/v1/engines/list", FakeResp(200, [{"id": "stable-diffusion-v1-6"}]))])
        self.assertEqual(run(ic.test_image_key("stability", KEY, "sdxl"))[0], "model_not_found")
        self.use([("get", "/v1/user/balance", FakeResp(200, {"credits": 3})),
                  ("get", "/v1/engines/list", FakeResp(200, [{"id": "stable-diffusion-xl-1024-v1-0"}]))])
        self.assertEqual(run(ic.test_image_key("stability", KEY, "sdxl"))[0], "ok")
        self.use([("get", "/v1/user/balance", FakeResp(200, {"credits": 3})),
                  ("post", "/generate/sd3", FakeResp(400, {"errors": ["model: invalid value"]}))])
        self.assertEqual(run(ic.test_image_key("stability", KEY, "sd3.5-nonexistent"))[0], "model_not_found")
        self.use([("get", "/v1/user/balance", FakeResp(200, {"credits": 0}))])
        self.assertEqual(run(ic.test_image_key("stability", KEY, "core"))[0], "no_balance")


class ModelAndConfigTests(unittest.TestCase):
    def test_normalize_model(self):
        self.assertEqual(ic.normalize_model("stability", "Stable Diffusion XL"), "stable-diffusion-xl-1024-v1-0")
        self.assertEqual(ic.normalize_model("stability", " SD3.5-Large "), "sd3.5-large")
        self.assertEqual(ic.normalize_model("bfl", "FLUX.2 pro"), "flux.2-pro")
        self.assertEqual(ic.normalize_model("bfl", "a/b"), "")
        self.assertEqual(ic.normalize_model("bfl", ""), "")

    def test_schnell_not_in_official_catalog(self):
        self.assertNotIn("flux-schnell", [m["id"] for m in ic.models_for("bfl")])
        self.assertIn("flux-dev", [m["id"] for m in ic.models_for("bfl")])

    def test_providers_registered_without_polluting_text_chain(self):
        for p in ("bfl", "stability"):
            self.assertIn(p, config.ALL_KEY_PROVIDERS)
            self.assertIn(p, config.KEY_POOLS)
            self.assertNotIn(p, config.SUPPORTED_PROVIDERS)
            self.assertIn(p, config.PROVIDER_LABELS)

    def test_add_replace_change_model_delete_key(self):
        with patch.object(config, "_save_runtime_overrides") as saved:
            config.KEY_POOLS["bfl"].clear()
            idx = config.add_key("bfl", KEY, "flux-2-pro")
            idx2 = config.add_key("bfl", "second-key-abcdef", "flux-dev")
            self.assertEqual((idx, idx2), (1, 2))
            self.assertTrue(config.update_key_field("bfl", 1, "model", "flux-2-flex"))
            self.assertTrue(config.update_key_field("bfl", 1, "key", "new-key-0123456"))
            self.assertEqual(config.KEY_POOLS["bfl"][0], {"key": "new-key-0123456", "model": "flux-2-flex"})
            self.assertTrue(config.delete_key("bfl", 1))
            self.assertEqual(len(config.KEY_POOLS["bfl"]), 1)
            self.assertGreaterEqual(saved.call_count, 5)
            config.KEY_POOLS["bfl"].clear()

    def test_course_image_settings_sanitize(self):
        base = {"enabled": True, "provider": "auto", "model": "", "count": 3}
        s = config.sanitize_course_image_settings({"count": 99, "provider": "evil", "enabled": 0}, base)
        self.assertEqual(s, {"enabled": False, "provider": "auto", "model": "", "count": 5})
        s = config.sanitize_course_image_settings({"count": -4, "provider": "bfl", "model": "flux-dev"}, base)
        self.assertEqual((s["count"], s["provider"], s["model"]), (1, "bfl", "flux-dev"))
        self.assertEqual(config.sanitize_course_image_settings({}, base), base)

    def test_dev_keys_are_masked_in_views(self):
        try:
            from handlers import developer
        except Exception:  # telegram.ext stubda yo'q — handler import qilib bo'lmaydi
            self.skipTest("handlers.developer stub muhitida import qilinmaydi")
        self.assertNotIn(KEY, developer._mask_key(KEY))


class CandidateKeyTests(unittest.TestCase):
    def setUp(self):
        self._saved = {p: list(v) for p, v in config.KEY_POOLS.items()}
        for p in config.IMAGE_PROVIDERS:
            config.KEY_POOLS[p] = []

    def tearDown(self):
        for p, v in self._saved.items():
            config.KEY_POOLS[p] = v

    def settings(self, **kw):
        base = {"enabled": True, "provider": "auto", "model": "", "count": 3}
        base.update(kw)
        return base

    def test_fallback_only_to_provider_with_real_key(self):
        config.KEY_POOLS["bfl"] = [{"key": "bfl-key-123456", "model": "flux-2-pro"}]
        config.KEY_POOLS["stability"] = [{"key": "   ", "model": "core"}]  # bo'sh kalit — "haqiqiy" emas
        cands = ci.candidate_keys(self.settings(provider="stability"), None)
        self.assertEqual([c["provider"] for c in cands], ["bfl"])
        self.assertEqual(ci.available_providers(None), {"bfl": 1})

    def test_chosen_provider_first_with_its_model_override(self):
        config.KEY_POOLS["bfl"] = [{"key": "bfl-key-123456", "model": "flux-2-pro"}]
        config.KEY_POOLS["stability"] = [{"key": "stab-key-123456", "model": "core"}]
        cands = ci.candidate_keys(self.settings(provider="stability", model="ultra"), None)
        self.assertEqual([(c["provider"], c["model"]) for c in cands], [("stability", "ultra"), ("bfl", "flux-2-pro")])

    def test_user_keys_before_bot_keys(self):
        config.KEY_POOLS["bfl"] = [{"key": "bot-key-123456", "model": "flux-2-pro"}]
        with patch("user_ai_keys.get_image_pools", return_value={"bfl": [{"key": "user-key-123456", "model": "flux-dev"}]}):
            cands = ci.candidate_keys(self.settings(), 7)
        self.assertEqual([(c["source"], c["model"]) for c in cands], [("user", "flux-dev"), ("bot", "flux-2-pro")])

    def test_no_keys_no_candidates(self):
        self.assertEqual(ci.candidate_keys(self.settings(), None), [])


SECTION_TEXT = ("Talabalarning 72 foizi sun'iy intellektdan foydalanadi, 18 foizi esa foydalanmaydi. " * 3
                + "Matn yetarlicha uzun bo'lishi uchun qo'shimcha so'zlar yoziladi. " * 4)


def make_sections():
    def sub(h):
        return {"heading": h, "content": "\n\n".join([SECTION_TEXT] * 4)}
    bobs = []
    for b in (1, 2, 3):
        subs = [sub(f"{b}.{j}. Bo'lim {b}.{j} nomi") for j in (1, 2, 3)]
        content = "\n\n".join(f"{s['heading']}\n{s['content']}" for s in subs)
        bobs.append({"title": f"{b}-BOB. NOM", "subsections": subs, "content": content})
    return {"kirish": SECTION_TEXT, "bobs": bobs, "xulosa": SECTION_TEXT, "adabiyotlar": "1. Manba\n2. Manba"}


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.sections = make_sections()
        _, self.index, self.texts = ci.collect_subsections(self.sections)

    def validate(self, figs, count=5, ai=True):
        return ci.validate_plan({"figures": figs}, self.index, self.texts, count, ai)

    def test_chart_requires_numbers_present_in_section_text(self):
        good = {"section": "1.1", "kind": "chart", "caption": "Foydalanish ulushi",
                "labels": ["Foydalanadi", "Foydalanmaydi"], "values": [72, 18], "unit": "%"}
        bad = {"section": "1.2", "kind": "chart", "caption": "To'qima", "labels": ["A", "B"], "values": [55, 45]}
        specs = self.validate([good, bad])
        self.assertEqual([s.section_id for s in specs], ["1.1"])
        self.assertEqual(specs[0].data["values"], [72.0, 18.0])

    def test_illustration_dropped_without_ai_and_duplicates_removed(self):
        ill = {"section": "1.1", "kind": "illustration", "caption": "1-rasm. Sinf xonasi",
               "prompt": "Students using tablets in a modern classroom with a teacher"}
        self.assertEqual(self.validate([ill], ai=False), [])
        dup = dict(ill, section="2.1")
        specs = self.validate([ill, dup])
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].caption, "Sinf xonasi", "AI qo'ygan 'N-rasm.' prefiksi olib tashlanishi kerak")

    def test_count_limit_one_per_section_and_bad_items_ignored(self):
        flow = lambda sid: {"section": sid, "kind": "flowchart", "caption": "Jarayon", "nodes": ["A", "B", "C"]}
        specs = self.validate([flow("1.1"), flow("1.1"), flow("9.9"), "junk", flow("2.1"), flow("3.1")], count=2)
        self.assertEqual([s.section_id for s in specs], ["1.1", "2.1"])

    def test_flowchart_edges_default_to_chain_and_invalid_removed(self):
        specs = self.validate([{"section": "1.1", "kind": "flowchart", "caption": "x", "nodes": ["A", "B", "C"],
                                "edges": [[0, 9], [1, 1]]}])
        self.assertEqual(specs[0].data["edges"], [[0, 1], [1, 2]])

    def test_extract_json_handles_code_fences(self):
        self.assertEqual(ci._extract_json('```json\n{"figures": []}\n```'), {"figures": []})
        self.assertEqual(ci._extract_json('Mana: {"figures": []} tamom'), {"figures": []})
        self.assertIsNone(ci._extract_json("not json"))

    def test_short_sections_are_not_candidates(self):
        s = make_sections()
        s["bobs"][0]["subsections"][0]["content"] = "qisqa"
        subs, index, _ = ci.collect_subsections(s)
        self.assertNotIn("1.1", index)


class RenderTests(unittest.TestCase):
    def check_png(self, path):
        with Image.open(path) as im:
            self.assertGreater(im.size[0], 800)
            self.assertEqual(im.mode in ("RGB", "RGBA"), True)

    def test_all_diagram_types_render(self):
        d = tempfile.mkdtemp(prefix="cwimg_test_")
        self.addCleanup(shutil.rmtree, d, True)
        specs = [
            ci.FigureSpec("flowchart", "c", 0, 0, "1.1", data={"nodes": ["Kirish", "Qayta ishlash", "Model", "Natija"], "edges": [[0, 1], [1, 2], [2, 3]]}),
            ci.FigureSpec("flowchart", "c", 0, 0, "1.1", data={"nodes": [f"Bosqich {i}" for i in range(8)], "edges": [[i, i + 1] for i in range(7)] + [[7, 0]]}),
            ci.FigureSpec("architecture", "c", 0, 0, "1.1", data={"layers": [{"name": "Foydalanuvchi", "items": ["Talaba", "O'qituvchi"]}, {"name": "Xizmat", "items": ["API", "Baza", "AI"]}]}),
            ci.FigureSpec("comparison", "c", 0, 0, "1.1", data={"columns": [{"name": "An'anaviy", "items": ["A", "B"]}, {"name": "Raqamli", "items": ["C", "D", "E"]}]}),
            ci.FigureSpec("chart", "c", 0, 0, "1.1", data={"labels": ["A", "B"], "values": [72.0, 18.5], "unit": "%", "title": "Ulush"}),
        ]
        for i, s in enumerate(specs):
            p = os.path.join(d, f"{i}.png")
            ci.render_diagram(s, p)
            self.check_png(p)


class BuildFiguresTests(unittest.TestCase):
    def setUp(self):
        self._saved = {p: list(v) for p, v in config.KEY_POOLS.items()}
        config.KEY_POOLS["bfl"] = [{"key": "bfl-key-123456", "model": "flux-2-pro"}]
        config.KEY_POOLS["stability"] = [{"key": "stab-key-123456", "model": "core"}]
        self.workdir = ci.make_workdir()
        self.addCleanup(ci.cleanup, self.workdir)
        self.sections = make_sections()
        self.settings = {"enabled": True, "provider": "bfl", "model": "", "count": 3}

    def tearDown(self):
        for p, v in self._saved.items():
            config.KEY_POOLS[p] = v

    @staticmethod
    def planner(figs):
        async def ask(prompt, system, tag):
            return json.dumps({"figures": figs})
        return ask

    FIGS = [
        {"section": "1.1", "kind": "illustration", "caption": "Sinfda sun'iy intellekt", "prompt": "Students using tablets in a modern classroom"},
        {"section": "2.1", "kind": "flowchart", "caption": "Ishlash sxemasi", "nodes": ["Ma'lumot", "Model", "Natija"]},
        {"section": "3.1", "kind": "chart", "caption": "Foydalanish ulushi", "labels": ["Ha", "Yo'q"], "values": [72, 18], "unit": "%"},
    ]

    def test_fallback_to_other_provider_and_dedup_bad_keys(self):
        calls = []

        async def fake_gen(provider, key, model, prompt, aspect="16:9", use_cache=True):
            calls.append(provider)
            if provider == "bfl":
                return ic.ImageResult("no_balance", "balans tugagan", provider=provider, model=model)
            return ic.ImageResult("ok", "", png_bytes("green", (400, 225)), "image/png", None, provider, model)

        with patch.object(ic, "generate_image", fake_gen):
            figs = run(ci.build_figures("Mavzu", self.sections, self.settings, None, self.planner(self.FIGS), self.workdir))
        self.assertEqual(len(figs), 3)
        self.assertEqual(calls, ["bfl", "stability"])
        self.assertTrue(any(f["source"].startswith("ai:stability") for f in figs))
        self.assertTrue(all(os.path.exists(f["path"]) for f in figs))
        self.assertEqual([(f["bob"], f["sub"]) for f in figs], [(0, 0), (1, 0), (2, 0)])

    def test_no_fallback_without_real_key_and_diagrams_still_made(self):
        config.KEY_POOLS["stability"] = []

        async def fail_gen(*a, **k):
            return ic.ImageResult("quota", "limit", provider="bfl", model="m")

        with patch.object(ic, "generate_image", fail_gen):
            figs = run(ci.build_figures("Mavzu", self.sections, self.settings, None, self.planner(self.FIGS), self.workdir))
        self.assertEqual(sorted(f["kind"] for f in figs), ["chart", "flowchart"])

    def test_planner_failure_or_garbage_returns_empty_not_exception(self):
        async def broken(prompt, system, tag):
            raise RuntimeError("AI down")

        async def none(prompt, system, tag):
            return None

        self.assertEqual(run(ci.build_figures("M", self.sections, self.settings, None, broken, self.workdir)), [])
        self.assertEqual(run(ci.build_figures("M", self.sections, self.settings, None, none, self.workdir)), [])

    def test_ai_returning_non_image_bytes_is_skipped(self):
        async def junk(provider, key, model, prompt, aspect="16:9", use_cache=True):
            return ic.ImageResult("ok", "", b"not an image at all" * 50, "image/png", None, provider, model)

        with patch.object(ic, "generate_image", junk):
            figs = run(ci.build_figures("M", self.sections, self.settings, None, self.planner(self.FIGS[:1]), self.workdir))
        self.assertEqual(figs, [])

    def test_cleanup_removes_workdir_and_stale_dirs(self):
        d = ci.make_workdir()
        with open(os.path.join(d, "a.png"), "wb") as fh:
            fh.write(b"x")
        ci.cleanup(d)
        self.assertFalse(os.path.exists(d))
        old = tempfile.mkdtemp(prefix=ci.WORKDIR_PREFIX)
        os.utime(old, (1, 1))
        self.assertGreaterEqual(ci.cleanup_stale(), 1)
        self.assertFalse(os.path.exists(old))
        ci.cleanup("/tmp")  # cwimg_ prefiksi yo'q — hech narsa o'chirilmaydi
        self.assertTrue(os.path.exists("/tmp"))


class PdfPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pdf_tools
        cls.pdf_tools = pdf_tools
        fonts = tempfile.mkdtemp(prefix="fonts_test_")
        cls._fonts = fonts
        src = "/usr/share/fonts/truetype/dejavu"
        names = ["DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf"]
        have_local = all(os.path.exists(os.path.join(pdf_tools._FONTS_DIR, n)) for n in names)
        if not have_local:
            if not all(os.path.exists(os.path.join(src, n)) for n in names):
                raise unittest.SkipTest("DejaVu Serif shriftlari mavjud emas (offline)")
            for n in names:
                shutil.copy(os.path.join(src, n), fonts)
            pdf_tools._FONTS_DIR = fonts
        pdf_tools._fonts_ready = False

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._fonts, ignore_errors=True)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cwimg_pdf_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sections = make_sections()

    def img(self, name, size):
        p = os.path.join(self.tmp, name)
        Image.new("RGB", size, "#3366cc").save(p)
        return p

    def extract(self, buf):
        from pypdf import PdfReader
        buf.seek(0)
        r = PdfReader(buf)
        return r, "\n".join(pg.extract_text() or "" for pg in r.pages)

    def test_figures_placed_in_chapter_with_numbered_centered_captions(self):
        self.sections["figures"] = [
            {"path": self.img("a.png", (2000, 1100)), "caption": "Birinchi izoh", "bob": 0, "sub": 1},
            {"path": self.img("b.png", (600, 3000)), "caption": "Juda baland rasm", "bob": 2, "sub": 0},  # baland — ramkaga moslanadi
        ]
        r, text = self.extract(self.pdf_tools.build_course_work_pdf("Mavzu", self.sections))
        self.assertIn("1-rasm. Birinchi izoh", text)
        self.assertIn("2-rasm. Juda baland rasm", text)
        self.assertLess(text.index("1-rasm."), text.index("2-rasm."))
        # 1-rasm II... emas, I-bob ichida: 1.2 sarlavhasidan keyin, 2-bob sarlavhasidan oldin
        pos = text.index("1-rasm.")
        self.assertGreater(text.rfind("1.2.", 0, pos), text.index("KIRISH", 800))  # mundarijadan keyingi 1.2 sarlavhasi
        self.assertIn("2-BOB.", text[pos:])  # keyingi bob sarlavhasi rasmdan keyin
        # Mundarija mavjud va rasmlar tufayli buzilmagan
        self.assertIn("MUNDARIJA", text)
        for pg in r.pages:  # hech bir rasm sahifadan chiqib ketmagan (mediabox ichida)
            for im in pg.images:
                self.assertTrue(im.data)

    def test_pdf_without_figures_unchanged_structure(self):
        r1, t1 = self.extract(self.pdf_tools.build_course_work_pdf("Mavzu", self.sections))
        self.sections["figures"] = []
        r2, t2 = self.extract(self.pdf_tools.build_course_work_pdf("Mavzu", self.sections))
        self.assertEqual(len(r1.pages), len(r2.pages))
        self.assertNotIn("-rasm.", t1)

    def test_broken_or_missing_image_is_skipped_pdf_still_built(self):
        bad = os.path.join(self.tmp, "bad.png")
        with open(bad, "wb") as fh:
            fh.write(b"garbage")
        self.sections["figures"] = [
            {"path": bad, "caption": "Buzilgan", "bob": 0, "sub": 0},
            {"path": "/no/such/file.png", "caption": "Yo'q", "bob": 1, "sub": 0},
            {"path": self.img("ok.png", (800, 450)), "caption": "Yaxshi", "bob": 1, "sub": 1},
        ]
        _, text = self.extract(self.pdf_tools.build_course_work_pdf("Mavzu", self.sections))
        self.assertNotIn("Buzilgan", text)
        self.assertIn("1-rasm. Yaxshi", text, "buzilganlar o'tkazib yuborilib, raqamlash ketma-ket qolishi kerak")

    def test_unmatched_anchor_falls_back_to_end_of_same_chapter(self):
        self.sections["figures"] = [{"path": self.img("c.png", (800, 450)), "caption": "Oxirida", "bob": 0, "sub": 99}]
        _, text = self.extract(self.pdf_tools.build_course_work_pdf("Mavzu", self.sections))
        self.assertIn("1-rasm. Oxirida", text)
        pos = text.index("1-rasm.")
        self.assertGreater(text.rfind("1.3.", 0, pos), text.index("KIRISH", 800))
        self.assertIn("2-BOB.", text[pos:])


if __name__ == "__main__":
    unittest.main()
