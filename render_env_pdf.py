"""Render environment variable'larini PDF'ga eksport qilish va PDF'dan import qilish.

/developer > RENDER > servis > Sozlamalar ekranidagi ikki tugma shu modulga tayanadi:

* ``build_env_pdf``  — barcha ENV (KEY + maxfiy qiymat) ni bitta PDF qiladi;
* ``parse_env_pdf``  — o'sha PDF'ni qaytib o'qib, ENV ro'yxatini tiklaydi.

PDF ikki qatlamli:

1. Inson o'qiydigan qism (KEY / qiymat) — ko'rish va saqlash uchun.
2. Mashina o'qiydigan qism — aynan bir xil ma'lumot JSON ko'rinishida
   (a) PDF ichiga biriktirilgan fayl (attachment) sifatida va
   (b) zaxira: sahifa oxirida siqilgan base64 matn sifatida.
   Import avval (a)ni, bo'lmasa (b)ni o'qiydi, shuning uchun uzun qiymatlar,
   ko'p qatorli kalitlar (masalan private key) va maxsus belgilar buzilmaydi.
   SHA-256 nazorat summasi PDF buzilgan/o'zgartirilgan bo'lsa xabar beradi.

Qiymatlar hech qachon logga yozilmaydi.
"""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import logging
import re
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

FORMAT_ID = "student-ai-env-pdf"
FORMAT_VERSION = 1
ATTACHMENT_NAME = "render_env_data.json"

_MARK = "ENVPDF"
_LINE_RE = re.compile(rf"{_MARK}:(\d{{5}}):([A-Za-z0-9_\-=]+)")
_CHUNK = 100

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Render hisobiga ulanish kalitlari: boshqa hisobga ko'chirishda eskisini
# yangisining ustiga yozib yuborish xavfli, shuning uchun alohida ajratiladi.
RENDER_CREDENTIAL_RE = re.compile(r"^RENDER_(API_KEY|OWNER_ID|SERVICE_ID|ACCOUNT_NAME)\d*$")

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class EnvPdfError(ValueError):
    """Foydalanuvchiga ko'rsatiladigan (o'zbekcha) PDF xatosi."""


# ---------------------------------------------------------------------------
# Umumiy yordamchilar
# ---------------------------------------------------------------------------

def normalize_env_vars(items: Iterable[dict[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
    """Render javobini [{"key","value"}] ga keltiradi.

    Qiymati yo'q (Render qaytarmagan) o'zgaruvchilar ikkinchi ro'yxatda
    (``missing``) nomi bilan qaytadi — ularni tiklab bo'lmaydi.
    """
    out: dict[str, str] = {}
    missing: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or item.get("name") or item.get("envVarKey")
        if not key:
            continue
        key = str(key)
        value = item.get("value")
        if value is None:
            missing.append(key)
            continue
        out[key] = str(value)
    return [{"key": k, "value": v} for k, v in out.items()], missing


def _checksum(variables: list[dict[str, str]]) -> str:
    canon = json.dumps(variables, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _payload(service: dict[str, Any], variables: list[dict[str, str]], missing: list[str]) -> dict[str, Any]:
    return {
        "format": FORMAT_ID,
        "version": FORMAT_VERSION,
        "service_id": str(service.get("id") or ""),
        "service_name": str(service.get("name") or service.get("slug") or ""),
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(variables),
        "missing": missing,
        "sha256": _checksum(variables),
        "vars": variables,
    }


def safe_filename(service: dict[str, Any]) -> str:
    raw = str(service.get("name") or service.get("slug") or service.get("id") or "service")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-") or "service"
    return f"env_{slug[:40]}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"


# ---------------------------------------------------------------------------
# PDF yaratish
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/dejavu/DejaVuSansMono.ttf"),
)


def _register_fonts() -> tuple[str, str, str, bool]:
    """(oddiy, qalin, mono, unicode_ok). Unicode shrift topilmasa Helvetica/Courier."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold, mono in _FONT_CANDIDATES:
        if all(Path(p).exists() for p in (regular, bold, mono)):
            try:
                pdfmetrics.registerFont(TTFont("EnvPdfSans", regular))
                pdfmetrics.registerFont(TTFont("EnvPdfSansBold", bold))
                pdfmetrics.registerFont(TTFont("EnvPdfMono", mono))
                return "EnvPdfSans", "EnvPdfSansBold", "EnvPdfMono", True
            except Exception:
                logger.warning("ENV PDF: Unicode shrift yuklanmadi", exc_info=True)
    return "Helvetica", "Helvetica-Bold", "Courier", False


def _display(text: str, unicode_ok: bool) -> str:
    """Ko'rinadigan qism uchun matnni xavfsiz qiladi (mashina qismiga tegmaydi)."""
    text = _CTRL_RE.sub("�" if unicode_ok else "?", text)
    if not unicode_ok:
        text = text.encode("latin-1", "replace").decode("latin-1")
    return text


def _encode_machine_block(payload: dict[str, Any]) -> list[str]:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    blob = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")
    return [
        f"{_MARK}:{i // _CHUNK:05d}:{blob[i:i + _CHUNK]}"
        for i in range(0, len(blob), _CHUNK)
    ]


def build_env_pdf(service: dict[str, Any], env_items: list[dict[str, Any]]) -> tuple[bytes, dict[str, Any]]:
    """ENV'larni PDF baytlariga aylantiradi.

    Qaytaradi: (pdf_bytes, {"count": int, "missing": [...]})
    """
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, Preformatted, SimpleDocTemplate, Spacer

    variables, missing = normalize_env_vars(env_items)
    payload = _payload(service, variables, missing)
    sans, bold, mono, uni = _register_fonts()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("EnvT", parent=styles["Title"], fontName=bold, fontSize=16, leading=20, alignment=0, spaceAfter=3 * mm)
    meta_style = ParagraphStyle("EnvM", parent=styles["Normal"], fontName=sans, fontSize=8.5, leading=12, spaceAfter=2 * mm)
    warn_style = ParagraphStyle("EnvW", parent=meta_style, textColor="#B00020", fontName=bold)
    key_style = ParagraphStyle("EnvK", parent=styles["Normal"], fontName=bold, fontSize=9.5, leading=12, spaceBefore=3 * mm)
    val_style = ParagraphStyle(
        "EnvV", parent=styles["Normal"], fontName=mono, fontSize=8, leading=10.5,
        leftIndent=4 * mm, wordWrap="CJK", backColor="#F3F3F3", borderPadding=2,
    )
    h2_style = ParagraphStyle("EnvH2", parent=styles["Heading3"], fontName=bold, fontSize=10, spaceBefore=6 * mm)

    title = _display(payload["service_name"] or payload["service_id"] or "Render service", uni)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Render ENV — {title}", author="Student AI",
    )
    story: list[Any] = [
        Paragraph(f"Render ENV — {html.escape(title)}", title_style),
        Paragraph(
            f"Service ID: {html.escape(payload['service_id'] or '—')}<br/>"
            f"Jami o'zgaruvchilar: <b>{payload['count']}</b><br/>"
            f"Yaratilgan: {html.escape(payload['exported_at'])}",
            meta_style,
        ),
        Paragraph("MAXFIY! Bu faylda barcha kalitlar ochiq ko'rinishda. Begonaga bermang, ishlatib bo'lgach o'chiring.", warn_style),
        Paragraph("Bu PDF'ni o'zgartirmang: botdagi «ENV PDF yuklash» aynan shu faylni o'qiydi.", meta_style),
        Spacer(1, 2 * mm),
    ]
    for entry in variables:
        value = _display(entry["value"], uni)
        shown = html.escape(value).replace("\r\n", "\n").replace("\n", "<br/>") if value else "<i>(bo'sh qiymat)</i>"
        story.append(KeepTogether([
            Paragraph(html.escape(_display(entry["key"], uni)), key_style),
            Paragraph(shown, val_style),
        ]))
    if missing:
        names = ", ".join(html.escape(_display(k, uni)) for k in missing)
        story.append(Paragraph(f"Qiymati Render tomonidan berilmagan (tiklab bo'lmaydi): {names}", h2_style))

    story.append(Paragraph("Mashina o'qiydigan zaxira (o'zgartirmang)", h2_style))
    story.append(Preformatted("\n".join(_encode_machine_block(payload)), ParagraphStyle(
        "EnvB", fontName="Courier", fontSize=6, leading=7.2,
    )))
    doc.build(story)

    # Asosiy mashina qatlami: JSON'ni PDF ichiga biriktirib qo'yamiz.
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    writer.add_attachment(ATTACHMENT_NAME, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), {"count": payload["count"], "missing": missing}


# ---------------------------------------------------------------------------
# PDF o'qish
# ---------------------------------------------------------------------------

def _validate_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("format") != FORMAT_ID:
        raise EnvPdfError("Bu PDF botning «ENV'larni PDF qilish» tugmasi yaratgan fayl emas.")
    if int(data.get("version") or 0) > FORMAT_VERSION:
        raise EnvPdfError("PDF yangiroq versiyada yaratilgan; botni yangilang.")
    variables = data.get("vars")
    if not isinstance(variables, list) or not all(
        isinstance(v, dict) and isinstance(v.get("key"), str) and isinstance(v.get("value"), str) for v in variables
    ):
        raise EnvPdfError("PDF ichidagi ma'lumot buzilgan.")
    if data.get("sha256") != _checksum(variables):
        raise EnvPdfError("PDF buzilgan yoki o'zgartirilgan (nazorat summasi mos kelmadi). Asl faylni yuboring.")
    return data


def _from_attachment(reader: Any) -> dict[str, Any] | None:
    try:
        files = reader.attachments
    except Exception:
        return None
    blobs = files.get(ATTACHMENT_NAME) if hasattr(files, "get") else None
    if not blobs:
        return None
    try:
        return json.loads(bytes(blobs[0]).decode("utf-8"))
    except Exception:
        return None


def _from_text(reader: Any) -> dict[str, Any] | None:
    lines: dict[int, str] = {}
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        for m in _LINE_RE.finditer(text):
            lines.setdefault(int(m.group(1)), m.group(2))
    if not lines:
        return None
    if sorted(lines) != list(range(len(lines))):
        raise EnvPdfError("PDF'ning zaxira qismi to'liq emas (sahifalar kesilgan bo'lishi mumkin).")
    blob = "".join(lines[i] for i in range(len(lines)))
    try:
        return json.loads(zlib.decompress(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))).decode("utf-8"))
    except Exception as exc:
        raise EnvPdfError("PDF'ning zaxira qismini o'qib bo'lmadi.") from exc


def parse_env_pdf(data: bytes) -> dict[str, Any]:
    """PDF baytlaridan ENV payload'ini tiklaydi (nazorat summasi tekshiriladi)."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise EnvPdfError("PDF parol bilan himoyalangan.")
    except EnvPdfError:
        raise
    except Exception as exc:
        raise EnvPdfError("PDF faylni ochib bo'lmadi.") from exc

    payload = _from_attachment(reader)
    if payload is not None:
        try:
            return _validate_payload(payload)
        except EnvPdfError:
            # Biriktirma buzilgan bo'lsa, zaxira matn qatlamiga o'tamiz.
            payload = None
    payload = _from_text(reader)
    if payload is None:
        raise EnvPdfError("PDF ichida ENV ma'lumoti topilmadi. Botdan olingan asl PDF'ni yuboring.")
    return _validate_payload(payload)


def split_valid(variables: list[dict[str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    """(to'g'ri (key, value) juftliklari, noto'g'ri nomli kalitlar)."""
    valid: dict[str, str] = {}
    invalid: list[str] = []
    for item in variables:
        key = item["key"]
        if KEY_RE.match(key):
            valid[key] = item["value"]
        else:
            invalid.append(key)
    return list(valid.items()), invalid


def render_credential_keys(entries: Iterable[tuple[str, str]]) -> list[str]:
    return [k for k, _ in entries if RENDER_CREDENTIAL_RE.match(k)]
