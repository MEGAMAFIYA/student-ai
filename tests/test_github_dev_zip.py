import io
import unittest
import zipfile
from unittest.mock import patch

import github_dev


class GitHubZipUploadTests(unittest.TestCase):
    def make_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("student-ai-main/README.md", "# test")
            zf.writestr("student-ai-main/handlers/example.py", "print('ok')\n")
        return buf.getvalue()

    def test_upload_zip_creates_single_merge_commit(self):
        calls = []

        class Response:
            def __init__(self, payload):
                self.payload = payload
            def json(self):
                return self.payload
            @property
            def is_success(self):
                return True

        def request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "GET" and "/git/ref/heads/" in url:
                return Response({"object": {"sha": "oldcommit"}})
            if method == "GET" and "/git/commits/" in url:
                return Response({"tree": {"sha": "basetree"}})
            if method == "POST" and url.endswith("/git/blobs"):
                return Response({"sha": "blobsha"})
            if method == "POST" and url.endswith("/git/trees"):
                return Response({"sha": "newtree"})
            if method == "POST" and url.endswith("/git/commits"):
                return Response({"sha": "newcommit"})
            if method == "PATCH" and "/git/refs/heads/" in url:
                return Response({})
            raise AssertionError(f"Unexpected request: {method} {url}")

        repo_info = {"default_branch": "main"}

        with patch.object(github_dev, "get_repository", return_value=repo_info),              patch.object(github_dev, "_request", side_effect=request):
            result = github_dev.upload_zip_project(
                "owner/repo", self.make_zip(), branch="main"
            )

        self.assertEqual(result["files"], ["README.md", "handlers/example.py"])
        self.assertEqual(result["common_root_removed"], "student-ai-main")
        self.assertEqual(result["commit_sha"], "newcommit")
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(
            [c[0] for c in calls],
            ["GET", "GET", "POST", "POST", "POST", "POST", "PATCH"],
        )

    def test_protected_secret_files_are_skipped(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("project/.env", "TOKEN=secret")
            zf.writestr("project/.env.example", "TOKEN=")
            zf.writestr("project/main.py", "print('ok')\n")

        calls = []

        class Response:
            def __init__(self, payload):
                self.payload = payload
            def json(self):
                return self.payload
            @property
            def is_success(self):
                return True

        def request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "GET" and "/git/ref/heads/" in url:
                return Response({"object": {"sha": "old"}})
            if method == "GET" and "/git/commits/" in url:
                return Response({"tree": {"sha": "tree"}})
            if method == "POST" and url.endswith("/git/blobs"):
                return Response({"sha": "blob"})
            if method == "POST" and url.endswith("/git/trees"):
                return Response({"sha": "newtree"})
            if method == "POST" and url.endswith("/git/commits"):
                return Response({"sha": "newcommit"})
            if method == "PATCH":
                return Response({})
            raise AssertionError(url)

        with patch.object(github_dev, "get_repository", return_value={"default_branch": "main"}),              patch.object(github_dev, "_request", side_effect=request):
            result = github_dev.upload_zip_project("owner/repo", buf.getvalue(), branch="main")

        self.assertEqual(result["files"], [".env.example", "main.py"])
        self.assertEqual(result["skipped_protected"], ["project/.env"])

    def test_dangerous_zip_path_is_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../outside.txt", "bad")

        with self.assertRaises(github_dev.GitHubDevError):
            github_dev.upload_zip_project("owner/repo", buf.getvalue(), branch="main")


if __name__ == "__main__":
    unittest.main()
