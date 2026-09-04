"""
GitHub Developer manager for /developer.

Provides admin-only GitHub repository browsing and file management:
- list accessible repositories
- browse repository directories
- view text files
- edit existing files
- delete files
- create new files (including nested paths)

Uses the existing GITHUB_TOKEN/GITHUB_REPO/GITHUB_BRANCH settings but does
NOT restrict browsing to GITHUB_REPO: repository listing comes directly from
GitHub and respects the token's actual permissions.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
_TIMEOUT = 30.0
_MAX_REPOS = 500
_MAX_DIR_ITEMS = 200
_MAX_TEXT_BYTES = 900_000
_MAX_VIEW_CHARS = 15_000


class GitHubDevError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.GITHUB_TOKEN)


def _headers() -> dict[str, str]:
    if not configured():
        raise GitHubDevError("GITHUB_TOKEN sozlanmagan.")
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Student-AI-Developer",
    }


def _raise_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        data = response.json()
        message = data.get("message") or response.text
    except Exception:
        message = response.text
    if response.status_code in (401, 403):
        raise GitHubDevError(
            f"GitHub ruxsat xatosi ({response.status_code}): {message}. "
            "Tokenning repository Access/Contents huquqlarini tekshiring."
        )
    if response.status_code == 404:
        raise GitHubDevError("Repository yoki fayl topilmadi, yoki token unga kira olmaydi.")
    if response.status_code == 409:
        raise GitHubDevError("GitHub conflict berdi. Branch yoki fayl ayni paytda o'zgargan bo'lishi mumkin.")
    raise GitHubDevError(f"GitHub API xatosi ({response.status_code}): {message}")


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = client.request(method, url, headers=_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise GitHubDevError(f"GitHub bilan ulanishda xato: {exc}") from exc
    _raise_response(response)
    return response


def _repo_parts(full_name: str) -> tuple[str, str]:
    parts = full_name.strip("/").split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise GitHubDevError("Repository nomi noto'g'ri.")
    return parts[0], parts[1]


def _contents_url(repo: str, path: str = "") -> str:
    owner, name = _repo_parts(repo)
    path = path.strip("/")
    return f"{_API}/repos/{owner}/{name}/contents/{path}"


def list_repositories() -> list[dict[str, Any]]:
    """List repositories accessible by the token, across pagination."""
    if not configured():
        raise GitHubDevError("GITHUB_TOKEN sozlanmagan.")
    result: list[dict[str, Any]] = []
    for page in range(1, 6):
        response = _request(
            "GET",
            f"{_API}/user/repos",
            params={
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member",
                "sort": "full_name",
                "direction": "asc",
                "per_page": 100,
                "page": page,
            },
        )
        batch = response.json()
        if not isinstance(batch, list):
            break
        result.extend(batch)
        if len(batch) < 100 or len(result) >= _MAX_REPOS:
            break
    return [
        {
            "full_name": r.get("full_name", ""),
            "name": r.get("name", ""),
            "private": bool(r.get("private")),
            "default_branch": r.get("default_branch") or "main",
            "description": r.get("description") or "",
            "size": r.get("size") or 0,
        }
        for r in result[:_MAX_REPOS]
        if r.get("full_name")
    ]


def list_directory(repo: str, path: str = "", branch: str | None = None) -> list[dict[str, Any]]:
    """Return a sorted directory listing. Folders first, then files."""
    params = {"ref": branch or "main"}
    # Empty/invalid branch is fixed by the caller with repository default branch.
    response = _request("GET", _contents_url(repo, path), params=params)
    data = response.json()
    if not isinstance(data, list):
        raise GitHubDevError("Bu yo'l papka emas.")
    items = []
    for item in data[:_MAX_DIR_ITEMS]:
        items.append({
            "name": item.get("name", ""),
            "path": item.get("path", ""),
            "type": item.get("type", ""),  # file / dir / symlink / submodule
            "size": int(item.get("size") or 0),
            "sha": item.get("sha"),
        })
    return sorted(items, key=lambda x: (x["type"] != "dir", x["name"].lower()))


def get_repository(repo: str) -> dict[str, Any]:
    owner, name = _repo_parts(repo)
    response = _request("GET", f"{_API}/repos/{owner}/{name}")
    return response.json()


def read_file(repo: str, path: str, branch: str | None = None) -> dict[str, Any]:
    response = _request(
        "GET",
        _contents_url(repo, path),
        params={"ref": branch or get_repository(repo).get("default_branch") or "main"},
    )
    data = response.json()
    if not isinstance(data, dict) or data.get("type") != "file":
        raise GitHubDevError("Tanlangan yo'l fayl emas.")
    encoded = data.get("content", "").replace("\n", "")
    try:
        raw = base64.b64decode(encoded, validate=False)
    except Exception as exc:
        raise GitHubDevError("Fayl mazmunini o'qib bo'lmadi.") from exc
    if len(raw) > _MAX_TEXT_BYTES:
        raise GitHubDevError(
            f"Fayl juda katta ({len(raw):,} bayt). Bu interfeysda {_MAX_TEXT_BYTES:,} baytgacha "
            "matnli fayllar tahrirlanadi."
        )
    # UTF-8 first; if binary, refuse editing/viewing as text.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitHubDevError("Bu binary fayl. Uni matn muharriri orqali tahrirlash xavfsiz emas.") from exc
    return {
        "repo": repo,
        "path": data.get("path") or path,
        "sha": data.get("sha"),
        "size": len(raw),
        "text": text,
        "html_url": data.get("html_url"),
        "download_url": data.get("download_url"),
    }


def write_file(
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str | None = None,
    sha: str | None = None,
) -> dict[str, Any]:
    if not path or path.endswith("/"):
        raise GitHubDevError("Fayl yo'li noto'g'ri.")
    raw = content.encode("utf-8")
    if len(raw) > _MAX_TEXT_BYTES:
        raise GitHubDevError(f"Fayl hajmi juda katta: {len(raw):,} bayt.")
    default_branch = get_repository(repo).get("default_branch") or "main"
    branch = branch or default_branch
    if sha is None:
        try:
            sha = read_file(repo, path, branch)["sha"]
        except GitHubDevError as exc:
            if "topilmadi" not in str(exc).lower():
                # For a new file, only a genuine 404 is acceptable. The public
                # error text is intentionally broad, so use a direct existence
                # GET to distinguish it.
                response = _request(
                    "GET", _contents_url(repo, path), params={"ref": branch}
                )
                _ = response  # _request would raise on 404.
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(raw).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    response = _request("PUT", _contents_url(repo, path), json=body)
    return response.json()


def create_file(repo: str, path: str, content: str, branch: str | None = None) -> dict[str, Any]:
    # Avoid accidentally overwriting an existing file from the "new file" flow.
    branch = branch or (get_repository(repo).get("default_branch") or "main")
    try:
        existing = read_file(repo, path, branch)
    except GitHubDevError as exc:
        if "topilmadi" not in str(exc).lower():
            # GitHub's 404 is normalized to "topilmadi"; permission errors are
            # never silently converted into a create operation.
            pass
        existing = None
    if existing:
        raise GitHubDevError("Bu nomdagi fayl allaqachon mavjud. Uni 'Tahrirlash' orqali o'zgartiring.")
    return write_file(repo, path, content, f"Create {path}", branch=branch, sha=None)


def delete_file(repo: str, path: str, branch: str | None = None, sha: str | None = None) -> dict[str, Any]:
    branch = branch or (get_repository(repo).get("default_branch") or "main")
    if not sha:
        sha = read_file(repo, path, branch)["sha"]
    body = {
        "message": f"Delete {path}",
        "sha": sha,
        "branch": branch,
    }
    response = _request("DELETE", _contents_url(repo, path), json=body)
    return response.json()


def display_text(text: str, limit: int = _MAX_VIEW_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n… [qisqartirildi: jami {len(text):,} belgi]"


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def item_icon(item_type: str) -> str:
    return {"dir": "📁", "file": "📄", "symlink": "🔗", "submodule": "📦"}.get(item_type, "❓")
