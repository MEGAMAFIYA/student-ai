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
import io
import logging
import mimetypes
import posixpath
import zipfile
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



_MAX_ZIP_BYTES = 20 * 1024 * 1024
_MAX_ZIP_FILES = 1000
_MAX_ZIP_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_MAX_ZIP_FILE_BYTES = 20 * 1024 * 1024


def _normalize_zip_path(name: str) -> str:
    """Normalize and validate a ZIP member path before sending it to GitHub."""
    name = (name or "").replace("\\", "/")
    name = name.lstrip("/")
    if not name or name.endswith("/"):
        return ""
    parts = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise GitHubDevError(f"ZIP ichida xavfli yo'l aniqlandi: {name}")
        parts.append(part)
    normalized = posixpath.join(*parts) if parts else ""
    if not normalized:
        return ""
    first = normalized.split("/", 1)[0].lower()
    if first == ".git" or normalized.lower().startswith(".git/"):
        raise GitHubDevError("ZIP ichidagi .git katalogi yuklanmaydi.")
    return normalized


def _is_protected_zip_path(path: str) -> bool:
    """Return True for local-secret files that should never be copied to GitHub."""
    basename = posixpath.basename(path).lower()
    if basename == ".env" or (basename.startswith(".env.") and basename != ".env.example"):
        return True
    if basename == "cookies.txt" or basename.endswith(".cookies.txt"):
        return True
    return False


def _zip_project_root(paths: list[str]) -> str:
    """Strip one artificial archive root (e.g. project-main/) when all files share it."""
    if not paths:
        return ""
    first_parts = {p.split("/", 1)[0] for p in paths}
    if len(first_parts) != 1:
        return ""
    root = next(iter(first_parts))
    # Only strip a directory when every member is actually below it.
    if all("/" in p for p in paths):
        return root
    return ""


def upload_zip_project(
    repo: str,
    zip_bytes: bytes,
    target_path: str = "",
    branch: str | None = None,
) -> dict[str, Any]:
    """Merge a ZIP project into a repository without deleting omitted files.

    Existing paths in the ZIP are replaced; new paths are added. Files that
    are not present in the ZIP are left untouched. The whole merge is published
    as one Git commit using the Git Trees API, which is safer and much faster
    than creating one commit per file.
    """
    logger.info(
        "GitHub ZIP upload start: repo=%s target_path=%r branch=%r zip_bytes=%d",
        repo, target_path, branch, len(zip_bytes),
    )

    if len(zip_bytes) > _MAX_ZIP_BYTES:
        raise GitHubDevError(f"ZIP hajmi juda katta: {len(zip_bytes):,} bayt. Maksimum {_MAX_ZIP_BYTES:,} bayt.")

    target_path = (target_path or "").replace("\\", "/").strip("/")
    if target_path == ".":
        target_path = ""
    if ".." in target_path.split("/"):
        raise GitHubDevError("Yuklash papkasi noto'g'ri.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except (zipfile.BadZipFile, OSError) as exc:
        raise GitHubDevError("Yuborilgan fayl haqiqiy ZIP arxiv emas.") from exc

    logger.info("GitHub ZIP upload stage=zip_opened: repo=%s", repo)
    members: dict[str, bytes] = {}
    skipped_protected: list[str] = []
    total_uncompressed = 0
    try:
        infos = zf.infolist()
        if len(infos) > _MAX_ZIP_FILES:
            raise GitHubDevError(f"ZIP ichida juda ko'p fayl bor: {len(infos)} ta. Maksimum {_MAX_ZIP_FILES} ta.")
        normalized_names: list[str] = []
        for info in infos:
            name = _normalize_zip_path(info.filename)
            if not name:
                continue
            if _is_protected_zip_path(name):
                skipped_protected.append(name)
                logger.warning(
                    "GitHub ZIP upload stage=protected_file_skipped: repo=%s path=%s",
                    repo, name,
                )
                continue
            # Do not follow links/special filesystem entries from archives.
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise GitHubDevError(f"ZIP ichidagi symbolic link yuklanmaydi: {info.filename}")
            if name in members:
                raise GitHubDevError(f"ZIP ichida bir xil fayl ikki marta bor: {name}")
            if info.file_size > _MAX_ZIP_FILE_BYTES:
                raise GitHubDevError(f"Fayl juda katta: {name} ({info.file_size:,} bayt).")
            total_uncompressed += info.file_size
            if total_uncompressed > _MAX_ZIP_UNCOMPRESSED_BYTES:
                raise GitHubDevError("ZIP ochilgandagi umumiy hajm juda katta.")
            normalized_names.append(name)
            with zf.open(info, "r") as fh:
                data = fh.read(_MAX_ZIP_FILE_BYTES + 1)
            if len(data) > _MAX_ZIP_FILE_BYTES:
                raise GitHubDevError(f"Fayl juda katta: {name}")
            members[name] = data
    finally:
        zf.close()

    if not members:
        raise GitHubDevError("ZIP ichida yuklanadigan fayl topilmadi.")

    logger.info(
        "GitHub ZIP upload stage=zip_validated: repo=%s members=%d protected_skipped=%d uncompressed_bytes=%d",
        repo, len(members), len(skipped_protected), total_uncompressed,
    )

    # ZIP exports commonly contain one artificial top-level project folder.
    # Removing it avoids creating e.g. repo/student-ai-main/... unintentionally.
    common_root = _zip_project_root(list(members))
    if common_root:
        prefix = common_root + "/"
        members = {name[len(prefix):]: data for name, data in members.items()}
        members = {name: data for name, data in members.items() if name}

    final_files: dict[str, bytes] = {}
    for relative_path, data in members.items():
        final_path = "/".join(x for x in (target_path, relative_path) if x)
        final_path = _normalize_zip_path(final_path)
        if not final_path:
            continue
        if final_path in final_files:
            raise GitHubDevError(f"ZIP yo'llari to'qnashdi: {final_path}")
        final_files[final_path] = data

    if not final_files:
        raise GitHubDevError("ZIP ichida yuklanadigan fayl topilmadi.")

    logger.info(
        "GitHub ZIP upload stage=paths_prepared: repo=%s files=%d target_path=%r common_root=%r bytes=%d",
        repo, len(final_files), target_path, common_root, sum(len(v) for v in final_files.values()),
    )

    repo_info = get_repository(repo)
    branch = branch or repo_info.get("default_branch") or "main"
    owner, name = _repo_parts(repo)

    # Read the current branch tip and base tree. `base_tree` makes this an
    # additive/overwrite merge: every repository path not mentioned by the ZIP
    # remains in the resulting tree exactly as it was.
    ref = _request("GET", f"{_API}/repos/{owner}/{name}/git/ref/heads/{branch}").json()
    old_commit_sha = ((ref.get("object") or {}).get("sha"))
    if not old_commit_sha:
        raise GitHubDevError("Branch HEAD aniqlanmadi.")
    commit = _request("GET", f"{_API}/repos/{owner}/{name}/git/commits/{old_commit_sha}").json()
    base_tree = ((commit.get("tree") or {}).get("sha"))
    if not base_tree:
        raise GitHubDevError("Repository tree aniqlanmadi.")

    logger.info(
        "GitHub ZIP upload stage=base_tree_ready: repo=%s branch=%s head=%s base_tree=%s",
        repo, branch, old_commit_sha, base_tree,
    )

    tree_entries = []
    for index, (path, data) in enumerate(sorted(final_files.items()), start=1):
        logger.info(
            "GitHub ZIP upload stage=blob_create: repo=%s branch=%s file=%d/%d path=%s bytes=%d",
            repo, branch, index, len(final_files), path, len(data),
        )
        try:
            blob = _request(
                "POST",
                f"{_API}/repos/{owner}/{name}/git/blobs",
                json={
                    "content": base64.b64encode(data).decode("ascii"),
                    "encoding": "base64",
                },
            ).json()
        except Exception as exc:
            raise GitHubDevError(
                f"GitHub blob yaratishda xato: {path}: {exc}"
            ) from exc
        blob_sha = blob.get("sha")
        if not blob_sha:
            raise GitHubDevError(f"GitHub blob yaratilmadi: {path}")
        tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})

    logger.info(
        "GitHub ZIP upload stage=blobs_created: repo=%s branch=%s blobs=%d",
        repo, branch, len(tree_entries),
    )

    logger.info(
        "GitHub ZIP upload stage=tree_create: repo=%s branch=%s entries=%d",
        repo, branch, len(tree_entries),
    )
    try:
        new_tree = _request(
            "POST",
            f"{_API}/repos/{owner}/{name}/git/trees",
            json={"base_tree": base_tree, "tree": tree_entries},
        ).json()
    except Exception as exc:
        raise GitHubDevError(f"GitHub tree yaratishda xato: {exc}") from exc
    new_tree_sha = new_tree.get("sha")
    if not new_tree_sha:
        raise GitHubDevError("GitHub yangi tree yaratmadi.")

    logger.info(
        "GitHub ZIP upload stage=tree_created: repo=%s branch=%s tree=%s",
        repo, branch, new_tree_sha,
    )

    commit_message = f"Update project from ZIP ({len(final_files)} files)"
    logger.info(
        "GitHub ZIP upload stage=commit_create: repo=%s branch=%s tree=%s parent=%s",
        repo, branch, new_tree_sha, old_commit_sha,
    )
    try:
        new_commit = _request(
            "POST",
            f"{_API}/repos/{owner}/{name}/git/commits",
            json={"message": commit_message, "tree": new_tree_sha, "parents": [old_commit_sha]},
        ).json()
    except Exception as exc:
        raise GitHubDevError(f"GitHub commit yaratishda xato: {exc}") from exc
    new_commit_sha = new_commit.get("sha")
    if not new_commit_sha:
        raise GitHubDevError("GitHub commit yaratmadi.")

    logger.info(
        "GitHub ZIP upload stage=commit_created: repo=%s branch=%s commit=%s files=%d",
        repo, branch, new_commit_sha, len(final_files),
    )

    # No force push: if somebody changed the branch while the ZIP was being
    # prepared, GitHub rejects the ref update instead of overwriting their work.
    logger.info(
        "GitHub ZIP upload stage=ref_update: repo=%s branch=%s new_commit=%s",
        repo, branch, new_commit_sha,
    )
    try:
        _request(
            "PATCH",
            f"{_API}/repos/{owner}/{name}/git/refs/heads/{branch}",
            json={"sha": new_commit_sha, "force": False},
        )
    except GitHubDevError as exc:
        raise GitHubDevError(
            "ZIP commit tayyorlandi, lekin branch boshqa commitga o'zgarib ketgani uchun "
            "avtomatik qo'llanmadi. Hech narsa ustidan majburan yozilmadi. Qayta urinib ko'ring."
        ) from exc

    logger.info(
        "GitHub ZIP upload stage=completed: repo=%s branch=%s commit=%s files=%d bytes=%d",
        repo, branch, new_commit_sha, len(final_files), sum(len(v) for v in final_files.values()),
    )

    return {
        "repo": repo,
        "branch": branch,
        "commit_sha": new_commit_sha,
        "files": sorted(final_files),
        "file_count": len(final_files),
        "bytes": sum(len(v) for v in final_files.values()),
        "target_path": target_path,
        "common_root_removed": common_root,
        "skipped_protected": sorted(skipped_protected),
    }

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
