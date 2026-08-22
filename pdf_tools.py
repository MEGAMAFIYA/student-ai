"""
PDF va rasm bilan bog'liq umumiy funksiyalar.
"""

import os
import re
import threading
from io import BytesIO

from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.units import cm, mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ============================================================
# UNICODE SHRIFTLAR (DejaVu Serif) — loyiha ichida bundle qilingan,
# shuning uchun Render kabi istalgan serverda ham ishlaydi.
# reportlab'ning standart Times-Roman shrifti o'zbekcha tutuq belgisi
# (ʻ) va boshqa maxsus belgilarni chizolmasligi (■ bo'lib chiqishi)
# sababli, bu muammoni butunlay bartaraf etadi.
# ============================================================

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_REGULAR = "DejaVuSerif"
FONT_BOLD = "DejaVuSerif-Bold"
FONT_ITALIC = "DejaVuSerif-Italic"

# Agar fonts/ papkasida shrift fayllari bo'lmasa (masalan, git orqali
# ko'chirilmagan bo'lsa), bot birinchi marta ishga tushganda ularni
# ushbu bepul ochiq CDN'dan avtomatik yuklab oladi — qo'lda fayl
# ko'chirish shart emas.
_FONT_SOURCES = {
    "DejaVuSerif.ttf": [
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSerif.ttf",
        "https://raw.githubusercontent.com/senotrusov/dejavu-fonts-ttf/master/ttf/DejaVuSerif.ttf",
    ],
    "DejaVuSerif-Bold.ttf": [
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSerif-Bold.ttf",
        "https://raw.githubusercontent.com/senotrusov/dejavu-fonts-ttf/master/ttf/DejaVuSerif-Bold.ttf",
    ],
    "DejaVuSerif-Italic.ttf": [
        "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSerif-Italic.ttf",
        "https://raw.githubusercontent.com/senotrusov/dejavu-fonts-ttf/master/ttf/DejaVuSerif-Italic.ttf",
    ],
}

_fonts_ready = False
# Endi PDF funksiyalari asyncio.to_thread() orqali chaqirilgani sababli, bir
# nechta HAQIQIY OS-oqim (thread) birinchi PDF so'ralganda BIR VAQTDA shu
# yerga kelishi mumkin. Lock'siz holda ikkalasi ham bir xil fayllarni bir
# vaqtda yozib, buzilgan (yarim yuklangan) shrift faylini qoldirib ketishi
# mumkin edi. Double-checked locking: lock faqat BIRINCHI marta (kamdan-kam)
# ishlatiladi, keyin _fonts_ready=True bo'lgach hech qanday qo'shimcha
# xarajatsiz darhol qaytadi — bu boshqa foydalanuvchilarni bloklamaydi.
_fonts_lock = threading.Lock()


def _download_missing_fonts():
    os.makedirs(_FONTS_DIR, exist_ok=True)
    import urllib.request

    for filename, urls in _FONT_SOURCES.items():
        path = os.path.join(_FONTS_DIR, filename)
        if os.path.exists(path) and os.path.getsize(path) > 50_000:
            continue
        last_error = None
        for url in urls:
            try:
                urllib.request.urlretrieve(url, path)
                if os.path.getsize(path) > 50_000:
                    last_error = None
                    break
            except Exception as e:
                last_error = e
        if last_error:
            raise RuntimeError(
                f"Shrift fayli topilmadi va yuklab bo'lmadi: {filename} ({last_error}). "
                "fonts/ papkasiga DejaVuSerif shriftlarini qo'lda joylashtiring."
            )


def _ensure_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    with _fonts_lock:
        if _fonts_ready:  # boshqa oqim shu orada allaqachon tayyorlab bo'lgan bo'lishi mumkin
            return
        _download_missing_fonts()
        pdfmetrics.registerFont(TTFont(FONT_REGULAR, os.path.join(_FONTS_DIR, "DejaVuSerif.ttf")))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, os.path.join(_FONTS_DIR, "DejaVuSerif-Bold.ttf")))
        pdfmetrics.registerFont(TTFont(FONT_ITALIC, os.path.join(_FONTS_DIR, "DejaVuSerif-Italic.ttf")))
        _fonts_ready = True


# ============================================================
# ODDIY PDF (Tarjima, PDF tahrirlash, Qo'llanma uchun)
# ============================================================

def make_pdf(title: str, content: str, lowercase: bool = False) -> BytesIO:
    """Matnni chiroyli formatlangan A4 PDF ga aylantiradi. '#' bilan boshlangan qatorlar sarlavha bo'ladi."""
    _ensure_fonts()
    if lowercase:
        content = content.lower()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleStyle", parent=styles["Title"], fontName=FONT_BOLD, fontSize=20,
        alignment=TA_CENTER, spaceAfter=18, textColor="#1a5490",
    )
    body_style = ParagraphStyle(
        "BodyStyle", parent=styles["Normal"], fontName=FONT_REGULAR, fontSize=12,
        leading=19, alignment=TA_LEFT,
    )
    h2_style = ParagraphStyle(
        "H2Style", parent=styles["Heading2"], fontName=FONT_BOLD, fontSize=14,
        spaceBefore=12, spaceAfter=8, textColor="#2a6fb0",
    )

    story = [Paragraph(_escape(title), title_style), Spacer(1, 0.8 * cm)]

    for block in content.split("\n\n"):
        block = block.strip()
        if not block:
            continue

        block_safe = _escape(block)

        if block_safe.startswith("#"):
            story.append(Paragraph(block_safe.lstrip("# ").strip(), h2_style))
        else:
            story.append(Paragraph(block_safe.replace("\n", "<br/>"), body_style))
            story.append(Spacer(1, 0.35 * cm))

    doc.build(story)
    buffer.seek(0)
    return buffer


# ============================================================
# KURS ISHI PDF — titul, avtomatik mundarija (TOC), 3 bob, xulosa,
# adabiyotlar ro'yxati. Rasmiy uslubiy qo'llanma talablariga mos:
# Times New Roman uslubi, chap 30mm/o'ng 10mm/tepa-past 20mm,
# har bob yangi sahifadan, sahifa raqami pastki o'ngda.
# ============================================================

_HEADING_RE = re.compile(r"^\d+\.\d+\.?\s+\S")


class _CourseWorkDoc(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            text = flowable.getPlainText()
            style_name = getattr(flowable.style, "name", "")
            if style_name == "CWChapterTOC":
                self.notify("TOCEntry", (0, text, self.page))
            elif style_name == "CWSectionTOC":
                self.notify("TOCEntry", (1, text, self.page))


def _footer(canvas, doc):
    if doc.page == 1:
        return
    canvas.saveState()
    canvas.setFont(FONT_REGULAR, 10)
    canvas.drawRightString(A4[0] - 1 * cm, 1.2 * cm, str(doc.page))
    canvas.restoreState()


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in (text or "").split("\n\n") if p.strip()]


def _parse_section_blocks(text: str):
    """Bob matnini o'qib, '1.1. Sarlavha' ko'rinishidagi qatorlarni kichik sarlavha
    sifatida, qolganini oddiy abzas sifatida ajratadi."""
    blocks = []
    for para in _split_paragraphs(text):
        lines = para.split("\n")
        first_line = lines[0].strip()
        if _HEADING_RE.match(first_line) and len(first_line.split()) <= 14:
            blocks.append((_escape(first_line), True))
            rest = "\n".join(lines[1:]).strip()
            if rest:
                blocks.append((_escape(rest).replace("\n", "<br/>"), False))
        else:
            blocks.append((_escape(para).replace("\n", "<br/>"), False))
    return blocks


def build_course_work_pdf(topic: str, sections: dict, meta: dict | None = None) -> BytesIO:
    """
    sections: {"kirish": str, "bobs": [{"title": str, "content": str}, ...],
               "xulosa": str, "adabiyotlar": str}
    meta: {"muassasa": str, "kafedra": str, "bajaruvchi": str, "guruh": str,
           "yonalish": str, "rahbar": str, "shahar": str} — barchasi ixtiyoriy
    """
    meta = meta or {}
    _ensure_fonts()
    buffer = BytesIO()
    doc = _CourseWorkDoc(
        buffer, pagesize=A4,
        leftMargin=3 * cm, rightMargin=1 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )

    title_style = ParagraphStyle(
        "CWTitle", fontName=FONT_BOLD, fontSize=14,
        alignment=TA_CENTER, leading=18, spaceAfter=6,
    )
    title_small = ParagraphStyle(
        "CWTitleSmall", fontName=FONT_REGULAR, fontSize=13,
        alignment=TA_CENTER, leading=17, spaceAfter=6,
    )
    meta_style = ParagraphStyle(
        "CWMeta", fontName=FONT_REGULAR, fontSize=13,
        alignment=TA_LEFT, leading=20, spaceAfter=10,
    )
    body = ParagraphStyle(
        "CWBody", fontName=FONT_REGULAR, fontSize=12,
        leading=18, alignment=TA_JUSTIFY, firstLineIndent=10 * mm, spaceAfter=6,
    )
    ref_style = ParagraphStyle(
        "CWRef", fontName=FONT_REGULAR, fontSize=11,
        leading=15, alignment=TA_JUSTIFY, spaceAfter=6,
    )
    chapter_toc = ParagraphStyle(
        "CWChapterTOC", fontName=FONT_BOLD, fontSize=15,
        alignment=TA_CENTER, spaceBefore=0, spaceAfter=16,
    )
    section_toc = ParagraphStyle(
        "CWSectionTOC", fontName=FONT_BOLD, fontSize=12,
        spaceBefore=12, spaceAfter=8,
    )

    story = []

    # ===== 1-bet: TITUL =====
    story.append(Spacer(1, 2 * cm))
    story.append(Paragraph(_escape(meta.get("muassasa", "_" * 42 + " UNIVERSITETI")), title_style))
    story.append(Paragraph(_escape(meta.get("kafedra", "_" * 38 + " kafedrasi")), title_style))
    story.append(Spacer(1, 2.5 * cm))
    story.append(Paragraph("KURS ISHI", title_style))
    story.append(Paragraph(f"«{_escape(topic)}»", title_small))
    story.append(Paragraph("mavzusida", title_small))
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph(f"Bajaruvchi: {meta.get('bajaruvchi', '_' * 32)}", meta_style))
    story.append(Paragraph(f"Guruh: {meta.get('guruh', '_' * 18)}", meta_style))
    story.append(Paragraph(f"Ta'lim yo'nalishi: {meta.get('yonalish', '_' * 28)}", meta_style))
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(f"Kurs ishi rahbari: {meta.get('rahbar', '_' * 24)}", meta_style))
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph(f"{meta.get('shahar', '_' * 12)}, 20{meta.get('yil', '__')} yil", title_small))
    story.append(PageBreak())

    # ===== 2-bet: MUNDARIJA =====
    story.append(Paragraph("MUNDARIJA", chapter_toc))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(name="TOCHeading1", fontName=FONT_BOLD, fontSize=12, leftIndent=0, firstLineIndent=0, spaceBefore=8, leading=15),
        ParagraphStyle(name="TOCHeading2", fontName=FONT_REGULAR, fontSize=11, leftIndent=10, firstLineIndent=0, spaceBefore=4, leading=13),
    ]
    story.append(toc)
    story.append(PageBreak())

    # ===== KIRISH =====
    story.append(Paragraph("KIRISH", chapter_toc))
    for para in _split_paragraphs(sections.get("kirish", "")):
        story.append(Paragraph(_escape(para).replace("\n", "<br/>"), body))
    story.append(PageBreak())

    # ===== BOBLAR =====
    for bob in sections.get("bobs", []):
        story.append(Paragraph(_escape(bob["title"]), chapter_toc))
        for text, is_heading in _parse_section_blocks(bob.get("content", "")):
            story.append(Paragraph(text, section_toc if is_heading else body))
        story.append(PageBreak())

    # ===== XULOSA =====
    story.append(Paragraph("XULOSA", chapter_toc))
    for para in _split_paragraphs(sections.get("xulosa", "")):
        story.append(Paragraph(_escape(para).replace("\n", "<br/>"), body))
    story.append(PageBreak())

    # ===== ADABIYOTLAR ROʻYXATI =====
    story.append(Paragraph("FOYDALANILGAN ADABIYOTLAR RO'YXATI", chapter_toc))
    for line in (sections.get("adabiyotlar", "") or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        story.append(Paragraph(_escape(line), ref_style))

    doc.multiBuild(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer


# ============================================================
# UMUMIY YORDAMCHI FUNKSIYALAR
# ============================================================

def count_pdf_pages(buffer: BytesIO) -> int:
    buffer.seek(0)
    n = len(PdfReader(buffer).pages)
    buffer.seek(0)
    return n


def extract_pdf_text(file_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(file_bytes))
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p for p in parts if p).strip()


def images_to_pdf(images_bytes: list[bytes]) -> BytesIO:
    """Bir nechta rasm baytlarini ketma-ket PDF sahifalariga joylaydi."""
    pil_images = [Image.open(BytesIO(b)).convert("RGB") for b in images_bytes]

    buffer = BytesIO()
    first, rest = pil_images[0], pil_images[1:]
    first.save(buffer, format="PDF", save_all=True, append_images=rest)
    buffer.seek(0)
    return buffer
