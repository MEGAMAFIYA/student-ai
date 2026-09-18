import unittest

import github_dev


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeGitHub:
    """Kichik soxta repository: root -> {app, app2, handlers, bot.py}.

    `app` va `app2` nomlari ataylab o'xshash: 'app' o'chirilganda 'app2' ga tegilmasligi kerak.
    """

    def __init__(self, truncated=False):
        self.truncated = truncated
        self.calls = []
        self.trees = {
            "ROOT": [
                {"path": "app", "type": "tree", "mode": "040000", "sha": "T_APP"},
                {"path": "app2", "type": "tree", "mode": "040000", "sha": "T_APP2"},
                {"path": "handlers", "type": "tree", "mode": "040000", "sha": "T_HANDLERS"},
                {"path": "bot.py", "type": "blob", "mode": "100644", "sha": "B_BOT"},
            ],
            "T_APP": [
                {"path": "main.py", "type": "blob", "mode": "100644", "sha": "B1"},
                {"path": "ui", "type": "tree", "mode": "040000", "sha": "T_APP_UI"},
            ],
            "T_APP_UI": [
                {"path": "home.xml", "type": "blob", "mode": "100644", "sha": "B2"},
            ],
            "T_APP2": [{"path": "keep.py", "type": "blob", "mode": "100644", "sha": "B3"}],
            "T_HANDLERS": [{"path": "menu.py", "type": "blob", "mode": "100644", "sha": "B4"}],
        }
        # Rekursiv ko'rinish (papka o'z subtree'iga nisbatan yo'llar bilan)
        self.recursive = {
            "T_APP": [
                {"path": "main.py", "type": "blob", "mode": "100644", "sha": "B1"},
                {"path": "ui", "type": "tree", "mode": "040000", "sha": "T_APP_UI"},
                {"path": "ui/home.xml", "type": "blob", "mode": "100644", "sha": "B2"},
            ],
            "T_APP_UI": [{"path": "home.xml", "type": "blob", "mode": "100644", "sha": "B2"}],
        }

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/git/ref/heads/main"):
            return FakeResponse({"object": {"sha": "HEAD"}})
        if url.endswith("/git/commits/HEAD"):
            return FakeResponse({"tree": {"sha": "ROOT"}})
        if "/git/trees/" in url and method == "GET":
            sha = url.rsplit("/", 1)[-1]
            if (kwargs.get("params") or {}).get("recursive") == "1":
                return FakeResponse({"tree": self.recursive[sha], "truncated": self.truncated})
            return FakeResponse({"tree": self.trees[sha]})
        if url.endswith("/git/trees") and method == "POST":
            return FakeResponse({"sha": "NEW_TREE"})
        if url.endswith("/git/commits") and method == "POST":
            return FakeResponse({"sha": "NEW_COMMIT"})
        if "/git/refs/heads/main" in url and method == "PATCH":
            return FakeResponse({})
        raise AssertionError(f"kutilmagan so'rov: {method} {url}")

    def posted(self, suffix):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(suffix)]


class DeleteFolderTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self._orig_request = github_dev._request
        github_dev._request = self.gh.request

    def tearDown(self):
        github_dev._request = self._orig_request

    def test_only_files_inside_folder_are_deleted(self):
        result = github_dev.delete_directory("me/repo", "app", "main")

        (_, _, kw), = self.gh.posted("/git/trees")
        entries = kw["json"]["tree"]
        self.assertEqual(
            sorted(e["path"] for e in entries),
            ["app/main.py", "app/ui/home.xml"],
        )
        self.assertTrue(all(e["sha"] is None for e in entries))
        self.assertEqual(kw["json"]["base_tree"], "ROOT")
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["commit_sha"], "NEW_COMMIT")

    def test_similar_named_sibling_and_other_files_untouched(self):
        github_dev.delete_directory("me/repo", "app", "main")
        (_, _, kw), = self.gh.posted("/git/trees")
        paths = [e["path"] for e in kw["json"]["tree"]]
        self.assertFalse(any(p.startswith("app2") for p in paths))
        self.assertNotIn("bot.py", paths)
        self.assertFalse(any(p.startswith("handlers") for p in paths))

    def test_commit_uses_current_head_and_no_force_push(self):
        github_dev.delete_directory("me/repo", "app", "main")
        (_, _, commit_kw), = self.gh.posted("/git/commits")
        self.assertEqual(commit_kw["json"]["parents"], ["HEAD"])
        patch = [c for c in self.gh.calls if c[0] == "PATCH"]
        self.assertEqual(len(patch), 1)
        self.assertIs(patch[0][2]["json"]["force"], False)

    def test_nested_folder_path(self):
        github_dev.delete_directory("me/repo", "app/ui", "main")
        (_, _, kw), = self.gh.posted("/git/trees")
        self.assertEqual([e["path"] for e in kw["json"]["tree"]], ["app/ui/home.xml"])

    def test_count_does_not_modify_anything(self):
        self.assertEqual(github_dev.count_folder_files("me/repo", "app", "main"), 2)
        self.assertFalse([c for c in self.gh.calls if c[0] in ("POST", "PATCH", "DELETE")])

    def test_root_and_traversal_are_refused(self):
        for bad in ("", "/", ".", "../x", "app/../.."):
            with self.assertRaises(github_dev.GitHubDevError, msg=repr(bad)):
                github_dev.delete_directory("me/repo", bad, "main")
        self.assertEqual(self.gh.calls, [])

    def test_missing_folder_is_refused(self):
        with self.assertRaises(github_dev.GitHubDevError):
            github_dev.delete_directory("me/repo", "nope", "main")
        self.assertFalse(self.gh.posted("/git/trees"))

    def test_file_path_is_not_treated_as_folder(self):
        with self.assertRaises(github_dev.GitHubDevError):
            github_dev.delete_directory("me/repo", "bot.py", "main")
        self.assertFalse(self.gh.posted("/git/trees"))

    def test_truncated_listing_aborts_without_changes(self):
        self.gh.truncated = True
        with self.assertRaises(github_dev.GitHubDevError):
            github_dev.delete_directory("me/repo", "app", "main")
        self.assertFalse(self.gh.posted("/git/trees"))


class DeleteButtonKeyboardTests(unittest.TestCase):
    def test_trash_button_only_next_to_folders(self):
        try:
            from tests._stub_telegram import install_stubs
            install_stubs()
            from handlers import developer
        except Exception as exc:  # telegram to'liq o'rnatilmagan muhit
            self.skipTest(f"handlers.developer import qilinmadi: {exc}")

        items = [
            {"name": "webapp", "path": "webapp", "type": "dir", "size": 0},
            {"name": "bot.py", "path": "bot.py", "type": "file", "size": 10},
        ]
        rows = developer._github_path_keyboard("me/repo", "", items).inline_keyboard
        self.assertEqual([b.callback_data for b in rows[0]], ["dev:gh:item:0", "dev:gh:rmdir:0"])
        self.assertEqual(rows[0][1].text, "🗑")
        self.assertEqual([b.callback_data for b in rows[1]], ["dev:gh:item:1"])


if __name__ == "__main__":
    unittest.main()
