import io
import unittest
import zipfile

import github_dev


class GitHubZipValidationTests(unittest.TestCase):
    def make_zip(self, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def test_protected_files_are_skipped(self):
        data = self.make_zip({
            "student-ai-main/.env": "SECRET=do-not-upload",
            "student-ai-main/.env.production": "SECRET=do-not-upload",
            "student-ai-main/cookies.txt": "session=secret",
            "student-ai-main/app.py": "print('ok')",
        })
        # Capture the normalized member set without touching GitHub by checking
        # the protection predicate directly.
        self.assertTrue(github_dev._is_protected_zip_path("student-ai-main/.env"))
        self.assertTrue(github_dev._is_protected_zip_path("student-ai-main/.env.production"))
        self.assertTrue(github_dev._is_protected_zip_path("student-ai-main/cookies.txt"))
        self.assertFalse(github_dev._is_protected_zip_path("student-ai-main/app.py"))
        self.assertGreater(len(data), 0)

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(github_dev.GitHubDevError):
            github_dev._normalize_zip_path("../secret.txt")

    def test_git_directory_is_rejected(self):
        with self.assertRaises(github_dev.GitHubDevError):
            github_dev._normalize_zip_path(".git/config")


if __name__ == "__main__":
    unittest.main()
