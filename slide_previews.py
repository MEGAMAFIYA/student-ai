"""Slayd yaratish ustasi (wizard) uchun VIZUAL NAMUNALAR (preview).

Muhim g'oya: preview rasmlari alohida "chizma" emas — ular HAQIQIY PPTX yaratuvchi
(`pptx_tools.build_presentation`) bilan yaratilgan namunaviy slayddan olinadi va Pillow
bilan 16:9 (1280x720) rasmga aylantiriladi. Shuning uchun preview doim yakuniy PPTX
parametrlari (dizayn, ramka, ranglar, shrift) bilan mos keladi.

Bu modul `telegram` kutubxonasiga bog'liq EMAS (testlash oson). Telegram qismi
`handlers/pptx_gen.py` da.

Tarkibi:
  * STEPS / items        — har bir tanlov bosqichi va uning variantlari ro'yxati;
  * make_cb / parse_cb   — callback_data (64 baytdan oshmaydi) yaratish/o'qish;
  * get_preview(...)     — JPEG baytlar (xotira + disk keshi, xato bo'lsa xavfsiz zaxira rasm);
  * prewarm()            — standart previewlarni oldindan yaratish.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import subprocess
import threading
from collections import OrderedDict
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from slide_themes import THEMES, PALETTES, FRAMES, FONTS

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Umumiy sozlamalar
# ----------------------------------------------------------------------------
PREVIEW_W, PREVIEW_H = 1280, 720          # 16:9
_SS = 2                                    # supersampling (silliq chiziqlar)
JPEG_QUALITY = 88
SAMPLE_FONT_TEXT = "Zamonaviy texnologiyalar va ta'lim rivoji"
COUNT_OPTIONS = [5, 7, 10, 12, 15, 20, 25, 30]
DEFAULT_SETTINGS = {"design": "modern_blue", "frame": "thin", "palette": "design_default",
                    "font": "Arial", "pdf": False}

_ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.getenv("SLIDE_PREVIEW_CACHE_DIR") or os.path.join(_ROOT, "data", "slide_preview_cache")
_MEM_LIMIT = int(os.getenv("SLIDE_PREVIEW_MEM_ITEMS", "160"))

_UI_REGULAR = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",)
_UI_BOLD = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",)


def _code_version() -> str:
    """Dizayn kodi o'zgarsa, eski kesh avtomatik yaroqsiz bo'ladi."""
    h = hashlib.sha1(b"slide-preview-v1")
    for name in ("slide_previews.py", "pptx_tools.py", "slide_themes.py", "slide_images.py"):
        try:
            with open(os.path.join(_ROOT, name), "rb") as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()[:12]


_VERSION = _code_version()

# ----------------------------------------------------------------------------
# Bosqichlar va variantlar
# ----------------------------------------------------------------------------
STEP_COUNT, STEP_DESIGN, STEP_FRAME, STEP_PALETTE, STEP_FONT = "cnt", "dsg", "frm", "pal", "fnt"
STEP_ORDER = [STEP_COUNT, STEP_DESIGN, STEP_FRAME, STEP_PALETTE, STEP_FONT]

STEP_TITLES = {
    STEP_COUNT: ("📑", "Slaydlar soni"),
    STEP_DESIGN: ("🎨", "Dizayn"),
    STEP_FRAME: ("🖼", "Ramka va bezak"),
    STEP_PALETTE: ("🌈", "Ranglar to‘plami"),
    STEP_FONT: ("🔤", "Shrift"),
}

DESIGN_KEYS = list(THEMES)
FRAME_KEYS = list(FRAMES)
PALETTE_KEYS = ["design_default"] + list(PALETTES) + ["custom"]
FONT_KEYS = list(FONTS)


def items(step: str) -> list[tuple[str, str]]:
    """[(kalit, ko'rinadigan nom), ...] — har bir bosqich uchun variantlar ro'yxati."""
    if step == STEP_COUNT:
        return [(str(n), f"{n} ta slayd") for n in COUNT_OPTIONS]
    if step == STEP_DESIGN:
        return [(k, THEMES[k][0]) for k in DESIGN_KEYS]
    if step == STEP_FRAME:
        return [(k, FRAMES[k]) for k in FRAME_KEYS]
    if step == STEP_PALETTE:
        out = []
        for k in PALETTE_KEYS:
            if k == "design_default":
                out.append((k, "✨ Dizaynning o‘z ranglari"))
            elif k == "custom":
                out.append((k, "🎨 O‘z ranglarim (HEX)"))
            else:
                out.append((k, PALETTES[k][0]))
        return out
    if step == STEP_FONT:
        return [(f"f{i}", name) for i, name in enumerate(FONTS)]
    raise KeyError(step)


def total(step: str) -> int:
    return len(items(step))


def describe(step: str, idx: int) -> str:
    """Variant haqida bir qator tushuntirish (caption uchun, o'zbekcha)."""
    key, label = items(step)[idx]
    if step == STEP_COUNT:
        n = int(key)
        return f"1 titul + {n - 2} mazmun + 1 yakuniy slayd (jami {n} ta)"
    if step == STEP_DESIGN:
        _, bg, accent, fg, _ = THEMES[key]
        return f"Fon {bg} · urg‘u {accent} · matn {fg}"
    if step == STEP_FRAME:
        return "Faqat ramka o‘zgaradi, qolgan dizayn bir xil"
    if step == STEP_PALETTE:
        if key == "design_default":
            return "Tanlangan dizaynning o‘z ranglari ishlatiladi"
        if key == "custom":
            return "Fon, urg‘u va matn ranglarini HEX kod bilan o‘zingiz kiritasiz"
        _, bg, accent, fg = PALETTES[key]
        return f"Fon {bg} · urg‘u {accent} · matn {fg}"
    if step == STEP_FONT:
        return "Bir xil matn turli shriftlarda"
    return ""


# ----------------------------------------------------------------------------
# callback_data: "px:<bosqich>:<amal>:<indeks>"  (64 baytdan ancha qisqa)
# amal: p=orqaga, n=keyingisi, ok=tasdiqlash
# ----------------------------------------------------------------------------
CB_PREFIX = "px"
ACTIONS = ("p", "n", "ok")


def make_cb(step: str, act: str, idx: int) -> str:
    data = f"{CB_PREFIX}:{step}:{act}:{int(idx)}"
    assert len(data.encode("utf-8")) <= 64, data
    return data


def parse_cb(data: str | None):
    """(step, act, idx) yoki None (noto'g'ri/eskirgan format)."""
    try:
        prefix, step, act, idx = str(data).split(":")
        if prefix != CB_PREFIX or step not in STEP_ORDER or act not in ACTIONS:
            return None
        idx = int(idx)
        if not 0 <= idx < total(step):
            return None
        return step, act, idx
    except Exception:
        return None


def nav_index(idx: int, act: str, n: int) -> int:
    """Keyingisi/Orqaga — aylana bo'ylab: oxirgidan birinchiga, birinchidan oxirgiga."""
    if act == "n":
        return (idx + 1) % n
    if act == "p":
        return (idx - 1) % n
    return idx % n


# ----------------------------------------------------------------------------
# Shriftlar
# ----------------------------------------------------------------------------
# So'ralgan shrift serverda bo'lmasa — shunga o'xshash ochiq shrift (xavfsiz zaxira).
_FONT_FALLBACKS = {
    "Arial": ["Arial", "Liberation Sans", "Arimo", "FreeSans"],
    "Aptos": ["Aptos", "Inter", "Carlito"],
    "Calibri": ["Calibri", "Carlito"],
    "Times New Roman": ["Times New Roman", "Liberation Serif", "Tinos", "FreeSerif"],
    "Georgia": ["Georgia", "Gelasio", "DejaVu Serif", "Caladea"],
    "Verdana": ["Verdana", "DejaVu Sans"],
    "DejaVu Sans": ["DejaVu Sans"],
    "Noto Sans": ["Noto Sans", "Open Sans", "FreeSans", "DejaVu Sans"],
}


@lru_cache(maxsize=64)
def _find_font(name: str, bold: bool):
    """(fayl_yo'li, haqiqiy_oila) — topilmasa (None, None)."""
    for fam in _FONT_FALLBACKS.get(name, [name]) + ["DejaVu Sans"]:
        try:
            p = subprocess.run(["fc-match", "-f", "%{family}|%{file}", f"{fam}:{'bold' if bold else 'regular'}"],
                               capture_output=True, text=True, timeout=3)
            out = (p.stdout or "").strip()
            if "|" not in out:
                continue
            families, path = out.rsplit("|", 1)
            if fam.lower() in [x.strip().lower() for x in families.split(",")] and os.path.exists(path):
                return path, fam
        except Exception:
            break
    for path in (_UI_BOLD if bold else _UI_REGULAR):
        if os.path.exists(path):
            return path, "DejaVu Sans"
    return None, None


def resolve_font(name: str, bold: bool = False):
    """(fayl, haqiqiy oila nomi, aniq_moslik) — preview chizish uchun."""
    path, fam = _find_font(str(name or "Arial"), bool(bold))
    exact = bool(fam) and fam.lower() == str(name).lower()
    return path, fam, exact


@lru_cache(maxsize=256)
def _pil_font(path, size):
    try:
        if path:
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def _ui_font(size, bold=False):
    return _pil_font((_UI_BOLD if bold else _UI_REGULAR)[0], int(size))


# ----------------------------------------------------------------------------
# PPTX -> rasm (Pillow)
# ----------------------------------------------------------------------------
def _hex(rgb) -> str:
    return f"#{rgb}" if not str(rgb).startswith("#") else str(rgb)


def _wrap(text, font, max_w):
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = (cur + " " + word).strip() if cur else word
            if font.getlength(trial) <= max_w or not cur:
                cur = trial
                # juda uzun so'zni belgilab bo'lamiz
                while font.getlength(cur) > max_w and len(cur) > 1:
                    cut = len(cur) - 1
                    while cut > 1 and font.getlength(cur[:cut]) > max_w:
                        cut -= 1
                    lines.append(cur[:cut]); cur = cur[cut:]
            else:
                lines.append(cur); cur = word
        lines.append(cur)
    return lines


def _star(cx, cy, rx, ry):
    pts = []
    for i in range(10):
        r = 1.0 if i % 2 == 0 else 0.382
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
    return pts


def render_slide(prs, slide_index: int, *, skip_text=False, skip_pictures=False,
                 size=(PREVIEW_W, PREVIEW_H)) -> Image.Image:
    """python-pptx slaydini (shakllar, matnlar, rasmlar) Pillow bilan chizadi."""
    from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
    from pptx.enum.dml import MSO_FILL
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    slide = prs.slides[slide_index]
    sw, sh_ = size[0] * _SS, size[1] * _SS
    k = sw / (prs.slide_width / 914400.0)          # piksel / dyuym
    px = lambda emu: float(emu) / 914400.0 * k     # noqa: E731

    try:
        bg = _hex(slide.background.fill.fore_color.rgb)
    except Exception:
        bg = "#FFFFFF"
    im = Image.new("RGB", (sw, sh_), bg)
    d = ImageDraw.Draw(im)

    for shape in slide.shapes:
        x, y, w, h = px(shape.left), px(shape.top), px(shape.width), px(shape.height)
        stype = shape.shape_type
        if stype == MSO_SHAPE_TYPE.PICTURE:
            if skip_pictures:
                continue
            try:
                pic = Image.open(BytesIO(shape.image.blob)).convert("RGB").resize((max(1, int(w)), max(1, int(h))), Image.LANCZOS)
                im.paste(pic, (int(x), int(y)))
            except Exception as e:
                logger.warning("Preview: rasm chizilmadi: %s", e)
            continue
        if stype == MSO_SHAPE_TYPE.TEXT_BOX:
            if not skip_text:
                _draw_textbox(d, shape, x, y, w, h, px, PP_ALIGN, MSO_ANCHOR)
            continue
        # avtoshakl
        try:
            fill = _hex(shape.fill.fore_color.rgb) if shape.fill.type == MSO_FILL.SOLID else None
        except Exception:
            fill = None
        line, lw = None, 0
        try:
            if shape.line.fill.type == MSO_FILL.SOLID:
                line = _hex(shape.line.color.rgb)
                lw = max(1, int(round(px(shape.line.width))))
        except Exception:
            pass
        kind = shape.auto_shape_type
        box = (x, y, x + w, y + h)
        kw = dict(fill=fill, outline=line, width=lw if line else 0)
        if kind == MSO_SHAPE.ROUNDED_RECTANGLE:
            d.rounded_rectangle(box, radius=min(w, h) * 0.16667, **kw)
        elif kind == MSO_SHAPE.OVAL:
            d.ellipse(box, **kw)
        elif kind == MSO_SHAPE.DIAMOND:
            d.polygon([(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)], fill=fill, outline=line, width=lw)
        elif kind == MSO_SHAPE.STAR_5_POINT:
            d.polygon(_star(x + w / 2, y + h / 2, w / 2, h / 2), fill=fill, outline=line, width=lw)
        elif kind == MSO_SHAPE.HEXAGON:
            dx = min(w, h) * 0.25
            d.polygon([(x, y + h / 2), (x + dx, y), (x + w - dx, y), (x + w, y + h / 2), (x + w - dx, y + h), (x + dx, y + h)],
                      fill=fill, outline=line, width=lw)
        elif kind == MSO_SHAPE.PARALLELOGRAM:
            dx = min(w, h) * 0.25
            d.polygon([(x + dx, y), (x + w, y), (x + w - dx, y + h), (x, y + h)], fill=fill, outline=line, width=lw)
        else:  # RECTANGLE va boshqalar
            d.rectangle(box, **kw)

    return im.resize(size, Image.LANCZOS)


def _draw_textbox(d, shape, x, y, w, h, px, PP_ALIGN, MSO_ANCHOR):
    tf = shape.text_frame
    ml, mr, mt, mb = (px(tf.margin_left), px(tf.margin_right), px(tf.margin_top), px(tf.margin_bottom))
    inner_w = max(1.0, w - ml - mr)
    blocks, total_h = [], 0.0
    for p in tf.paragraphs:
        if not p.runs:
            continue
        r = p.runs[0]
        pt = r.font.size.pt if r.font.size else 18
        size_px = pt / 72.0 * px(914400)                          # pt -> piksel (px(914400) = piksel/dyuym)
        path, _, _ = resolve_font(r.font.name or "Arial", bool(r.font.bold))
        font = _pil_font(path, max(6, int(round(size_px))))
        try:
            rgb = _hex(r.font.color.rgb)
        except Exception:
            rgb = "#000000"
        lines = _wrap(r.text, font, inner_w)
        lh = size_px * 1.2
        after = px(p.space_after) if p.space_after is not None else 0.0
        blocks.append((lines, font, rgb, p.alignment, lh, after, size_px))
        total_h += lh * len(lines) + after
    if not blocks:
        return
    anchor = tf.vertical_anchor
    if anchor == MSO_ANCHOR.MIDDLE:
        cy = y + mt + (h - mt - mb - total_h) / 2
    elif anchor == MSO_ANCHOR.BOTTOM:
        cy = y + h - mb - total_h
    else:
        cy = y + mt
    for lines, font, rgb, align, lh, after, size_px in blocks:
        for ln in lines:
            tw = font.getlength(ln)
            if align == PP_ALIGN.CENTER:
                tx = x + ml + (inner_w - tw) / 2
            elif align == PP_ALIGN.RIGHT:
                tx = x + ml + inner_w - tw
            else:
                tx = x + ml
            d.text((tx, cy + (lh - size_px) / 2 - size_px * 0.08), ln, font=font, fill=rgb)
            cy += lh
        cy += after


# ----------------------------------------------------------------------------
# Namunaviy slayd (haqiqiy quruvchi bilan)
# ----------------------------------------------------------------------------
def _sample_presentation(settings: dict):
    from pptx import Presentation
    from pptx_tools import build_presentation
    from slide_images import make_illustration

    design = settings.get("design", "modern_blue")
    accent = THEMES.get(design, THEMES["modern_blue"])[2]
    slides = [{
        "heading": "Zamonaviy texnologiyalar",
        "bullets": ["Raqamli ta’lim imkoniyatlari", "Sun’iy intellekt va zamonaviy vositalar",
                    "Amaliy ko‘nikmalarni rivojlantirish"],
        "layout": "split_right",
    }]
    img = make_illustration("Texnologiya", "Raqamli ta’lim", accent, 0)
    buf = build_presentation("Zamonaviy texnologiyalar va ta’lim rivoji", slides, "Student AI", dict(settings), {0: img})
    return Presentation(buf)


def _effective_colors(settings: dict):
    """build_presentation bilan bir xil qoida: (fon, urg'u, matn)."""
    _, bg, accent, fg, _ = THEMES.get(settings.get("design", "modern_blue"), THEMES["modern_blue"])
    pal = settings.get("palette", "")
    if pal in PALETTES:
        _, bg, accent, fg = PALETTES[pal]
    if settings.get("custom_bg") and settings.get("custom_accent") and settings.get("custom_text"):
        bg, accent, fg = settings["custom_bg"], settings["custom_accent"], settings["custom_text"]
    return bg, accent, fg


def slide_image(settings: dict) -> Image.Image:
    """Haqiqiy PPTX'ning kontent slaydi (2-slayd) — 1280x720."""
    return render_slide(_sample_presentation(settings), 1)


def _text_w(font, s):
    return font.getlength(s)


def _fit_text(d, text, xy, font, fill, max_w, anchor="la"):
    """Matn chegaradan chiqmasin: sig'masa shriftni kichraytiradi."""
    f = font
    size = getattr(f, "size", 20)
    path = getattr(f, "path", None)
    while f.getlength(text) > max_w and size > 8:
        size -= 1
        f = _pil_font(path, size)
    d.text(xy, text, font=f, fill=fill, anchor=anchor)


# --- slaydlar soni ----------------------------------------------------------
_LAYOUT_CYCLE = ["split_right", "split_left", "cards", "chart", "full_image", "two_col", "text_focus", "table"]
_ROLE_COLORS = {"titul": "#2563EB", "kirish": "#0D9488", "mazmun": "#64748B", "xulosa": "#EA580C", "yakuniy": "#16A34A"}


def _mini_slide(d, x, y, w, h, role, layout, number):
    col = _ROLE_COLORS[role]
    d.rounded_rectangle((x, y, x + w, y + h), radius=max(4, w * 0.04), fill="#FFFFFF", outline=col, width=max(2, int(w * 0.025)))
    pad = w * 0.09
    gray, light = "#CBD5E1", "#E2E8F0"
    lines = lambda x0, y0, x1, n, step: [d.rectangle((x0, y0 + i * step, x1 - (i % 2) * (x1 - x0) * .25, y0 + i * step + step * .45), fill=gray) for i in range(n)]  # noqa: E731
    if role in ("titul", "yakuniy"):
        d.rectangle((x + pad, y + h * 0.22, x + pad + w * 0.07, y + h * 0.78), fill=col)
        d.rectangle((x + pad * 2.2, y + h * 0.32, x + w - pad, y + h * 0.46), fill=col)
        lines(x + pad * 2.2, y + h * 0.56, x + w * 0.72, 2, h * 0.14)
    else:
        d.rectangle((x + pad, y + h * 0.12, x + w * 0.6, y + h * 0.22), fill=col)
        top, bot = y + h * 0.34, y + h * 0.88
        left, right, mid = x + pad, x + w - pad, x + w / 2
        if layout == "split_right":
            lines(left, top, mid - 3, 3, (bot - top) / 3); d.rectangle((mid + 3, top, right, bot), fill=light)
        elif layout == "split_left":
            d.rectangle((left, top, mid - 3, bot), fill=light); lines(mid + 3, top, right, 3, (bot - top) / 3)
        elif layout == "cards":
            cw = (right - left - 2 * 4) / 3
            for i in range(3):
                d.rounded_rectangle((left + i * (cw + 4), top, left + i * (cw + 4) + cw, bot), radius=3, fill=light, outline=gray)
        elif layout == "chart":
            lines(left, top, mid - 3, 3, (bot - top) / 3)
            for i, hh in enumerate((0.5, 0.9, 0.65, 1.0)):
                bw = (right - mid - 3) / 4
                d.rectangle((mid + 3 + i * bw + 1, bot - (bot - top) * hh, mid + 3 + (i + 1) * bw - 2, bot), fill=col)
        elif layout == "full_image":
            d.rectangle((left, top, x + w * 0.62, bot), fill=light); lines(x + w * 0.66, top, right, 3, (bot - top) / 3)
        elif layout == "two_col":
            lines(left, top, mid - 3, 3, (bot - top) / 3); lines(mid + 3, top, right, 3, (bot - top) / 3)
        elif layout == "table":
            rows = 4
            for r_ in range(rows):
                d.rectangle((left, top + r_ * (bot - top) / rows, right, top + (r_ + 1) * (bot - top) / rows - 2), fill=col if r_ == 0 else light)
        else:  # text_focus
            d.rounded_rectangle((left, top, right, bot), radius=3, fill=light); lines(left + 4, top + 4, right - 4, 3, (bot - top - 6) / 3)
    nf = _ui_font(max(9, int(h * 0.17)), True)
    d.text((x + w - pad * 0.6, y + h - pad * 0.45), str(number), font=nf, fill=col, anchor="rd")


def count_image(n: int) -> Image.Image:
    im = Image.new("RGB", (PREVIEW_W, PREVIEW_H), "#F1F5F9")
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, PREVIEW_W, 118), fill="#0F172A")
    d.text((48, 28), f"{n} ta slayd", font=_ui_font(46, True), fill="#FFFFFF")
    d.text((50, 84), f"1 titul + {n - 2} mazmun + 1 yakuniy  ·  taqdimot tuzilishi namunasi", font=_ui_font(21), fill="#CBD5E1")
    area_w, area_h, gap, top = 1184, 500, 14, 138
    best = None
    for cols in range(3, 11):
        rows = math.ceil(n / cols)
        cw = (area_w - (cols - 1) * gap) / cols
        ch = cw * 9 / 16
        if rows * ch + (rows - 1) * gap <= area_h and (best is None or cw > best[0]):
            best = (cw, ch, cols, rows)
    cw, ch, cols, rows = best or (120, 68, 10, math.ceil(n / 10))
    gw = cols * cw + (cols - 1) * gap
    gh = rows * ch + (rows - 1) * gap
    x0, y0 = (PREVIEW_W - gw) / 2, top + (area_h - gh) / 2
    for i in range(n):
        num = i + 1
        role = "titul" if num == 1 else "kirish" if num == 2 else "yakuniy" if num == n else "xulosa" if num == n - 1 else "mazmun"
        layout = _LAYOUT_CYCLE[(num - 3) % 8] if role in ("mazmun", "xulosa", "kirish") else "split_right"
        if role == "kirish":
            layout = "text_focus"
        r_, c_ = divmod(i, cols)
        _mini_slide(d, x0 + c_ * (cw + gap), y0 + r_ * (ch + gap), cw, ch, role, layout, num)
    # izoh
    lx = 48
    for role, label in (("titul", "Titul"), ("kirish", "Kirish"), ("mazmun", "Mazmun"), ("xulosa", "Xulosa"), ("yakuniy", "Yakuniy")):
        d.rounded_rectangle((lx, 672, lx + 22, 694), radius=5, fill=_ROLE_COLORS[role])
        d.text((lx + 32, 683), label, font=_ui_font(21), fill="#334155", anchor="lm")
        lx += 32 + int(_ui_font(21).getlength(label)) + 34
    return im


# --- dizayn / ramka -----------------------------------------------------------
def design_image(design: str) -> Image.Image:
    return slide_image({**DEFAULT_SETTINGS, "design": design})


def frame_image(design: str, frame: str, font: str) -> Image.Image:
    return slide_image({**DEFAULT_SETTINGS, "design": design, "frame": frame, "font": font})


# --- ranglar ------------------------------------------------------------------
def _compose_with_chips(slide_im: Image.Image, chips: list[tuple[str, str]], title: str = "") -> Image.Image:
    canvas = Image.new("RGB", (PREVIEW_W, PREVIEW_H), "#111827")
    sw, sh = 1024, 576
    canvas.paste(slide_im.resize((sw, sh), Image.LANCZOS), ((PREVIEW_W - sw) // 2, 12))
    d = ImageDraw.Draw(canvas)
    n = len(chips)
    cw = 320
    gap = 24
    x0 = (PREVIEW_W - (n * cw + (n - 1) * gap)) // 2
    for i, (label, hexv) in enumerate(chips):
        x = x0 + i * (cw + gap)
        d.rounded_rectangle((x, 606, x + cw, 704), radius=14, fill="#1F2937")
        d.rounded_rectangle((x + 14, 620, x + 84, 690), radius=12, fill=hexv, outline="#9CA3AF", width=2)
        d.text((x + 100, 640), label, font=_ui_font(20, True), fill="#F9FAFB", anchor="lm")
        d.text((x + 100, 673), hexv.upper(), font=_ui_font(22), fill="#D1D5DB", anchor="lm")
    return canvas


def palette_image(settings: dict, palette: str) -> Image.Image:
    if palette == "custom":
        return custom_colors_image()
    s = {**DEFAULT_SETTINGS, **{k: settings[k] for k in ("design", "frame", "font") if k in settings}, "palette": palette}
    s.pop("custom_bg", None)
    bg, accent, fg = _effective_colors(s)
    # build_presentation'da sarlavha va asosiy matn bir xil rangda (fg)
    return _compose_with_chips(slide_image(s), [("Fon", bg), ("Sarlavha va matn", fg), ("Urg‘u", accent)])


def custom_colors_image() -> Image.Image:
    im = Image.new("RGB", (PREVIEW_W, PREVIEW_H), "#111827")
    d = ImageDraw.Draw(im)
    d.text((PREVIEW_W // 2, 120), "O‘z ranglarim (HEX)", font=_ui_font(58, True), fill="#F9FAFB", anchor="mm")
    d.text((PREVIEW_W // 2, 195), "Tasdiqlagach, 3 ta HEX kodni yuborasiz:", font=_ui_font(28), fill="#CBD5E1", anchor="mm")
    cols = [("Fon", "#102A43"), ("Urg‘u", "#38BDF8"), ("Matn", "#FFFFFF")]
    for i, (label, hv) in enumerate(cols):
        x = 150 + i * 370
        d.rounded_rectangle((x, 270, x + 330, 470), radius=26, fill=hv, outline="#9CA3AF", width=3)
        d.text((x + 165, 520), label, font=_ui_font(32, True), fill="#F9FAFB", anchor="mm")
        d.text((x + 165, 565), hv, font=_ui_font(28), fill="#D1D5DB", anchor="mm")
    d.text((PREVIEW_W // 2, 650), "Masalan:  #102A43 #38BDF8 #FFFFFF", font=_ui_font(30, True), fill="#FBBF24", anchor="mm")
    return im


# --- shriftlar ----------------------------------------------------------------
def font_image(settings: dict, font: str) -> Image.Image:
    s = {**DEFAULT_SETTINGS, **{k: v for k, v in settings.items() if k in ("design", "frame", "palette", "custom_bg", "custom_accent", "custom_text")}, "font": font}
    prs = _sample_presentation(s)
    im = render_slide(prs, 1, skip_text=True, skip_pictures=True)    # faqat fon + ramka + bezak
    bg, accent, fg = _effective_colors(s)
    d = ImageDraw.Draw(im)
    left, right = 104, 1000
    path, fam, exact = resolve_font(font, False)
    bpath, _, _ = resolve_font(font, True)
    d.text((left, 92), f"Shrift: {font}", font=_ui_font(30, True), fill=accent)
    y = 168
    big = _pil_font(path, 60)
    for ln in _wrap(SAMPLE_FONT_TEXT, big, right - left):
        d.text((left, y), ln, font=big, fill=fg)
        y += 80
    y += 18
    bold = _pil_font(bpath, 40)
    for ln in _wrap(SAMPLE_FONT_TEXT, bold, right - left):
        d.text((left, y), ln, font=bold, fill=fg)
        y += 56
    y += 14
    small = _pil_font(path, 30)
    _fit_text(d, "Aa Bb Cc Dd  O‘o G‘g  Sh sh  Ch ch  0123456789", (left, y), small, fg, right - left)
    if not exact:
        _fit_text(d, f"Namuna o‘xshash shrift ({fam}) bilan ko‘rsatildi; PPTX’da «{font}» yoziladi",
                  (left, 596), _ui_font(20), accent, right - left)
    return im


def final_image(settings: dict) -> Image.Image:
    return slide_image(settings)


# ----------------------------------------------------------------------------
# Kesh (xotira LRU + disk) va get_preview
# ----------------------------------------------------------------------------
_mem: "OrderedDict[str, bytes]" = OrderedDict()
_mem_lock = threading.Lock()
_gen_lock = threading.Lock()          # og'ir chizish bir vaqtda bittadan (CPU ortiqcha yuklanmasin)


def _to_jpeg(im: Image.Image) -> bytes:
    b = BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return b.getvalue()


def _custom_part(s: dict):
    return (s.get("custom_bg"), s.get("custom_accent"), s.get("custom_text"))


def cache_key(step: str, idx: int | None, settings: dict | None = None, *, final=False) -> str:
    s = settings or {}
    if final:
        parts = ("final", s.get("design"), s.get("frame"), s.get("palette"), s.get("font"), _custom_part(s))
    else:
        key = items(step)[idx][0]
        if step == STEP_COUNT:
            parts = (step, key)
        elif step == STEP_DESIGN:
            parts = (step, key)
        elif step == STEP_FRAME:
            parts = (step, key, s.get("design"), s.get("font"))
        elif step == STEP_PALETTE:
            parts = (step, key, s.get("design"), s.get("frame"), s.get("font"))
        else:
            parts = (step, key, s.get("design"), s.get("frame"), s.get("palette"), _custom_part(s))
    raw = _VERSION + "|" + repr(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _mem_get(k):
    with _mem_lock:
        v = _mem.get(k)
        if v is not None:
            _mem.move_to_end(k)
        return v


def _mem_put(k, v):
    with _mem_lock:
        _mem[k] = v
        _mem.move_to_end(k)
        while len(_mem) > _MEM_LIMIT:
            _mem.popitem(last=False)


def _disk_path(k):
    return os.path.join(CACHE_DIR, k + ".jpg")


def _render(step: str, idx: int | None, settings: dict, final: bool) -> Image.Image:
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    if final:
        return final_image(s)
    key = items(step)[idx][0]
    if step == STEP_COUNT:
        return count_image(int(key))
    if step == STEP_DESIGN:
        return design_image(key)
    if step == STEP_FRAME:
        return frame_image(s["design"], key, s["font"])
    if step == STEP_PALETTE:
        return palette_image(s, key)
    return font_image(s, FONTS[int(key[1:])])


def fallback_image(label: str = "") -> bytes:
    """Chizish xatosi bo'lsa ham bot to'xtamasligi uchun oddiy zaxira rasm (kesh qilinmaydi)."""
    im = Image.new("RGB", (PREVIEW_W, PREVIEW_H), "#E5E7EB")
    d = ImageDraw.Draw(im)
    d.text((PREVIEW_W // 2, 300), str(label)[:60] or "Namuna", font=_ui_font(54, True), fill="#111827", anchor="mm")
    d.text((PREVIEW_W // 2, 390), "Namunani yuklab bo‘lmadi — tanlashni davom ettirishingiz mumkin", font=_ui_font(26), fill="#374151", anchor="mm")
    return _to_jpeg(im)


def get_preview(step: str, idx: int, settings: dict | None = None, *, final: bool = False) -> bytes:
    """JPEG baytlar. Hech qachon istisno ko'tarmaydi (xato bo'lsa zaxira rasm qaytaradi)."""
    try:
        k = cache_key(step, idx, settings, final=final)
        data = _mem_get(k)
        if data:
            return data
        path = _disk_path(k)
        try:
            with open(path, "rb") as f:
                data = f.read()
            if data[:2] == b"\xff\xd8":
                _mem_put(k, data)
                return data
        except OSError:
            pass
        with _gen_lock:
            data = _mem_get(k)
            if data:
                return data
            data = _to_jpeg(_render(step, idx, settings or {}, final))
        _mem_put(k, data)
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = path + f".{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError as e:
            logger.debug("Preview disk keshiga yozilmadi: %s", e)
        return data
    except Exception:
        logger.exception("Preview yaratishda xatolik (step=%s idx=%s)", step, idx)
        try:
            label = "Yakuniy namuna" if final else items(step)[idx][1]
        except Exception:
            label = "Namuna"
        return fallback_image(label)


def prewarm(settings: dict | None = None) -> int:
    """Standart previewlarni oldindan yaratadi (fon oqimida chaqiriladi). Yaratilgan soni qaytadi."""
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    made = 0
    for step in STEP_ORDER:
        for idx in range(total(step)):
            try:
                get_preview(step, idx, s)
                made += 1
            except Exception:                      # get_preview o'zi ham istisno ko'tarmaydi
                logger.exception("Prewarm xatosi")
    logger.info("🖼 Slayd previewlari tayyor: %d ta", made)
    return made
