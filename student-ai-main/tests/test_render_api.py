import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import render_api


class RenderApiPureTests(unittest.TestCase):
    def test_unwrap_services_envelope(self):
        payload = {"items": [{"service": {"id": "srv-1", "name": "Student"}}]}
        self.assertEqual(render_api._unwrap_items(payload, ("service",))[0]["id"], "srv-1")

    def test_unwrap_plain_list(self):
        payload = [{"id": "srv-1", "name": "Student"}]
        self.assertEqual(render_api._unwrap_items(payload)[0]["name"], "Student")

    def test_pagination_cursor_supports_common_variants(self):
        self.assertEqual(render_api._pagination_cursor({"cursor": "abc"}), "abc")
        self.assertEqual(render_api._pagination_cursor({"nextCursor": "def"}), "def")
        self.assertIsNone(render_api._pagination_cursor({}))

    def test_level_matching(self):
        self.assertTrue(render_api._log_level_match({"level": "warning"}, ["warning", "notice"]))
        self.assertFalse(render_api._log_level_match({"level": "info"}, ["warning", "notice"]))
        self.assertTrue(render_api._log_level_match({"level": "info"}, None))

    def test_parse_dt_handles_z_and_naive_values(self):
        self.assertIsNotNone(render_api._parse_dt("2026-09-03T20:00:00Z"))
        self.assertIsNotNone(render_api._parse_dt("2026-09-04T00:00:00"))
        self.assertIsNone(render_api._parse_dt("not-a-date"))

    def test_pdf_contains_log_data(self):
        service = {"id": "srv-test", "name": "Student AI"}
        logs = [{
            "timestamp": "2026-09-03T20:00:00Z",
            "level": "error",
            "type": "app",
            "instance": "inst-1",
            "message": "Test Render xatosi",
        }]
        path = render_api.create_logs_pdf(service, logs)
        try:
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 500)
        finally:
            render_api.remove_temp_file(path)
            self.assertFalse(os.path.exists(path))


class RenderApiAsyncTests(unittest.TestCase):
    def test_list_logs_paginates_with_timestamp_cursor(self):
        pages = [
            {
                "logs": [{"timestamp": "2026-09-03T20:00:00Z", "level": "error", "message": "one"}],
                "hasMore": True,
                "nextStartTime": "2026-09-03T19:00:00Z",
                "nextEndTime": "2026-09-03T19:30:00Z",
            },
            {
                "logs": [{"timestamp": "2026-09-03T19:20:00Z", "level": "warning", "message": "two"}],
                "hasMore": False,
            },
        ]

        async def fake_request(*args, **kwargs):
            return pages.pop(0)

        async def run():
            with patch.object(render_api, "_request", side_effect=fake_request):
                return await render_api.list_logs_for_service(
                    service_id="srv-test", owner_id="tea-test", levels=None, limit=100, hours=2
                )

        logs = asyncio.run(run())
        self.assertEqual([x["message"] for x in logs], ["one", "two"])
        self.assertEqual(len(pages), 0)


if __name__ == "__main__":
    unittest.main()
