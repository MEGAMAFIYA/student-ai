"""Render REST API integration used by /developer > RENDER.

The module deliberately keeps all Render credentials in environment variables
and exposes only the API operations needed by the Telegram admin panel.
"""

from __future__ import annotations

import html
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

import config

logger = logging.getLogger(__name__)

BASE_URL = "https://api.render.com/v1"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
_RETRYABLE = {429, 500, 502, 503, 504}


class RenderAPIError(RuntimeError):
    """A Render API request failed in a user-actionable way."""

    def __init__(self, status_code: int, message: str, response: Any = None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


def _require_key() -> str:
    key = config.RENDER_API_KEY
    if not key:
        raise RenderAPIError(401, "RENDER_API_KEY sozlanmagan.")
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_require_key()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Student-AI-Render-Panel/1.0",
    }


def _unwrap_items(payload: Any, singular_keys: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Normalize Render's list responses across API response envelope variants."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("items", "results", "services", "deploys", "logs", "owners", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        else:
            items = []
    else:
        items = []

    out: list[dict[str, Any]] = []
    singular = set(singular_keys)
    for item in items:
        if not isinstance(item, dict):
            continue
        nested = next((item[k] for k in singular if isinstance(item.get(k), dict)), None)
        out.append(nested if nested is not None else item)
    return out


def _pagination_cursor(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    cursor = payload.get("cursor")
    if cursor is None:
        cursor = payload.get("nextCursor")
    return str(cursor) if cursor else None


async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> Any:
    url = f"{BASE_URL}{path}"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.request(method, url, headers=_headers(), params=params, json=json)
            if response.status_code in _RETRYABLE and attempt < 2:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else (1.0 * (2**attempt))
                await _sleep(delay)
                continue
            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                detail = body.get("message") if isinstance(body, dict) else body
                detail = detail or body.get("error") if isinstance(body, dict) else detail
                raise RenderAPIError(response.status_code, str(detail or response.reason_phrase), body)
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                return {"raw": response.text}
        except RenderAPIError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt < 2:
                await _sleep(1.0 * (2**attempt))
                continue
            raise RenderAPIError(503, f"Render API ga ulanishda xato: {exc}") from exc
        except Exception as exc:
            last_error = exc
            break
    raise RenderAPIError(500, f"Render API so'rov xatosi: {last_error}")


async def _sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(min(seconds, 5.0))


async def list_workspaces(limit: int = 100) -> list[dict[str, Any]]:
    payload = await _request("GET", "/owners", params={"limit": min(max(limit, 1), 100)})
    return _unwrap_items(payload, ("owner", "workspace"))


async def list_services(owner_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": min(max(limit, 1), 100), "includePreviews": "false"}
    if owner_id:
        params["ownerId"] = owner_id
    else:
        # API key may have access to several workspaces; fetching all is more
        # useful than guessing one workspace.
        pass
    payload = await _request("GET", "/services", params=params)
    return _unwrap_items(payload, ("service",))


async def get_service(service_id: str) -> dict[str, Any]:
    return dict(await _request("GET", f"/services/{service_id}"))


async def trigger_deploy(service_id: str, *, clear_cache: bool = False, commit_id: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {"clearCache": "clear" if clear_cache else "do_not_clear"}
    if commit_id:
        body["commitId"] = commit_id
    return dict(await _request("POST", f"/services/{service_id}/deploys", json=body))


async def restart_service(service_id: str) -> dict[str, Any]:
    return dict(await _request("POST", f"/services/{service_id}/restart"))


async def suspend_service(service_id: str) -> dict[str, Any]:
    return dict(await _request("POST", f"/services/{service_id}/suspend"))


async def resume_service(service_id: str) -> dict[str, Any]:
    return dict(await _request("POST", f"/services/{service_id}/resume"))


async def list_deploys(service_id: str, limit: int = 30) -> list[dict[str, Any]]:
    payload = await _request("GET", f"/services/{service_id}/deploys", params={"limit": min(max(limit, 1), 100)})
    return _unwrap_items(payload, ("deploy",))


async def get_deploy(service_id: str, deploy_id: str) -> dict[str, Any]:
    return dict(await _request("GET", f"/services/{service_id}/deploys/{deploy_id}"))


async def cancel_deploy(service_id: str, deploy_id: str) -> dict[str, Any]:
    return dict(await _request("POST", f"/services/{service_id}/deploys/{deploy_id}/cancel"))


async def list_env_vars(service_id: str, limit: int = 100) -> list[dict[str, Any]]:
    payload = await _request("GET", f"/services/{service_id}/env-vars", params={"limit": min(max(limit, 1), 100)})
    return _unwrap_items(payload, ("envVar", "env_var"))


async def upsert_env_var(service_id: str, key: str, value: str) -> dict[str, Any]:
    if not key or any(ch in key for ch in "/?#"):
        raise ValueError("Noto'g'ri environment variable nomi.")
    return dict(await _request("PUT", f"/services/{service_id}/env-vars/{key}", json={"value": value}))


async def delete_env_var(service_id: str, key: str) -> dict[str, Any]:
    if not key or any(ch in key for ch in "/?#"):
        raise ValueError("Noto'g'ri environment variable nomi.")
    return dict(await _request("DELETE", f"/services/{service_id}/env-vars/{key}"))


async def update_service(service_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"autoDeploy", "repo", "branch", "image", "name", "buildFilter", "rootDir"}
    body = {k: v for k, v in changes.items() if k in allowed}
    if not body:
        raise ValueError("Yangilanadigan Render service maydoni berilmagan.")
    return dict(await _request("PATCH", f"/services/{service_id}", json=body))


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _log_level_match(log: dict[str, Any], levels: list[str] | None) -> bool:
    if levels is None:
        return True
    level = str(log.get("level") or "info").lower()
    return level in {x.lower() for x in levels}


async def list_logs_for_service(
    *,
    service_id: str,
    owner_id: str,
    levels: list[str] | None = None,
    limit: int = 100,
    hours: int = 1,
    start_time: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch paginated logs for one service.

    Render's logs endpoint is workspace-scoped and requires at least one
    resource ID. Pagination for logs uses the returned nextStartTime and
    nextEndTime timestamps, so we follow that cursor until exhausted or until
    the requested maximum is reached.
    """
    if not owner_id:
        raise RenderAPIError(400, "Render workspace ID (ownerId) aniqlanmadi.")
    now = datetime.now(timezone.utc)
    end = now
    if start_time is not None:
        start = start_time.astimezone(timezone.utc) if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
        if start > end:
            start = end
    else:
        start = now - timedelta(hours=max(1, hours))
    wanted = max(1, min(limit, 5000))
    collected: list[dict[str, Any]] = []

    while len(collected) < wanted:
        params: dict[str, Any] = {
            "ownerId": owner_id,
            "resource": service_id,
            "startTime": _iso_z(start),
            "endTime": _iso_z(end),
            "direction": "backward",
            "limit": min(100, wanted - len(collected)),
        }
        if levels:
            params["level"] = levels
        payload = await _request("GET", "/logs", params=params)
        page = _unwrap_items(payload, ("log",))
        if not page:
            break
        collected.extend(page)
        if not isinstance(payload, dict) or not payload.get("hasMore"):
            break
        next_start = payload.get("nextStartTime")
        next_end = payload.get("nextEndTime")
        if not next_start or not next_end:
            break
        new_start, new_end = _parse_dt(next_start), _parse_dt(next_end)
        if not new_start or not new_end or new_end >= end:
            break
        start, end = new_start, new_end

    return collected[:wanted]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        raw = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _log_message(log: dict[str, Any]) -> str:
    msg = log.get("message") or log.get("text") or log.get("msg") or ""
    if isinstance(msg, (dict, list)):
        return str(msg)
    return str(msg).replace("\x00", "")


def _font_path() -> str | None:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def create_logs_pdf(service: dict[str, Any], logs: list[dict[str, Any]]) -> str:
    """Create a temporary, readable PDF containing the fetched Render logs."""
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font_name = "Helvetica"
    font_path = _font_path()
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("RenderDejaVu", font_path))
            font_name = "RenderDejaVu"
        except Exception:
            logger.warning("PDF Unicode font yuklanmadi", exc_info=True)

    fd, path = tempfile.mkstemp(prefix="render_logs_", suffix=".pdf")
    os.close(fd)
    title = str(service.get("name") or service.get("slug") or service.get("id") or "Render service")
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "RenderTitle", parent=styles["Title"], fontName=font_name, fontSize=15,
        leading=19, alignment=TA_LEFT, spaceAfter=7 * mm,
    )
    meta_style = ParagraphStyle(
        "RenderMeta", parent=styles["Normal"], fontName=font_name, fontSize=8,
        leading=11, spaceAfter=4 * mm,
    )
    log_style = ParagraphStyle(
        "RenderLog", parent=styles["Normal"], fontName=font_name, fontSize=7.5,
        leading=10, spaceAfter=3 * mm,
    )

    doc = SimpleDocTemplate(
        path, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"Render logs — {title}", author="Student AI",
    )
    story = [
        Paragraph(f"Render loglari — {html.escape(title)}", title_style),
        Paragraph(
            f"Service ID: {html.escape(str(service.get('id', '—')))}<br/>"
            f"Jami log: {len(logs)}<br/>"
            f"Yaratilgan: {html.escape(datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z'))}",
            meta_style,
        )
    ]
    for index, log in enumerate(logs, 1):
        timestamp = str(log.get("timestamp") or log.get("time") or log.get("createdAt") or "—")
        level = str(log.get("level") or "info").upper()
        log_type = str(log.get("type") or "app")
        instance = str(log.get("instance") or log.get("instanceId") or "—")
        message = _log_message(log)
        block = (
            f"<b>#{index} {html.escape(timestamp)} | {html.escape(level)} | {html.escape(log_type)}</b><br/>"
            f"Instance: {html.escape(instance)}<br/>"
            f"{html.escape(message).replace(chr(10), '<br/>')}"
        )
        story.append(Paragraph(block, log_style))
    if not logs:
        story.append(Paragraph("Tanlangan vaqt oralig'ida log topilmadi.", log_style))
    doc.build(story)
    return path


def remove_temp_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Vaqtinchalik PDF o'chirilmadi: %s", path, exc_info=True)


def human_error(exc: Exception) -> str:
    if isinstance(exc, RenderAPIError):
        if exc.status_code == 401:
            return "API Key noto'g'ri yoki RENDER_API_KEY sozlanmagan."
        if exc.status_code == 403:
            return "Render API bu amal uchun ruxsat bermadi (403)."
        if exc.status_code == 404:
            return "Render'da servis yoki resource topilmadi (404)."
        if exc.status_code == 429:
            return "Render API rate limitga urildi (429). Biroz kutib qayta urinib ko'ring."
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
