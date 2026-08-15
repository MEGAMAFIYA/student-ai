"""
PDF va rasm bilan bog'liq umumiy funksiyalar.
"""

from io import BytesIO

from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def make_pdf(title: str, content: str, lowercase: bool = False) -> BytesIO:
    """Matnni chiroyli formatlangan A4 PDF ga aylantiradi. '#' bilan boshlangan qatorlar sarlavha bo'ladi."""
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
        "TitleStyle", parent=styles["Title"], fontSize=20,
        alignment=TA_CENTER, spaceAfter=18, textColor="#1a5490",
    )
    body_style = ParagraphStyle(
        "BodyStyle", parent=styles["Normal"], fontSize=12,
        leading=19, alignment=TA_LEFT,
    )
    h2_style = ParagraphStyle(
        "H2Style", parent=styles["Heading2"], fontSize=14,
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
