
"""PPTX and PDF export helpers. PDF uses LibreOffice when available, ReportLab otherwise."""
from io import BytesIO
import os, shutil, subprocess, tempfile, logging
from pptx import Presentation
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

log=logging.getLogger(__name__)

def pptx_to_pdf(pptx_buffer, topic="", slides=None, settings=None, images=None):
    data=pptx_buffer.getvalue() if hasattr(pptx_buffer,"getvalue") else pptx_buffer
    soffice=shutil.which("libreoffice") or shutil.which("soffice")
    if soffice:
        with tempfile.TemporaryDirectory(prefix="studentai_slide_") as td:
            src=os.path.join(td,"presentation.pptx")
            with open(src,"wb") as f: f.write(data)
            try:
                subprocess.run([soffice,"-env:UserInstallation=file://"+td+"/profile","--headless","--convert-to","pdf","--outdir",td,src],
                               capture_output=True,text=True,timeout=90,check=True)
                path=os.path.join(td,"presentation.pdf")
                with open(path,"rb") as f: return BytesIO(f.read())
            except Exception as e: log.warning("LibreOffice PDF export failed, using fallback: %s",e)
    return _reportlab_pdf(topic, slides or [], settings or {}, images or {})

def _reportlab_pdf(topic, slides, settings, images):
    from slide_themes import THEMES, PALETTES
    theme=THEMES.get(settings.get("design","modern_blue"),THEMES["modern_blue"])
    _,bg,accent,fg,_=theme
    if settings.get("palette") in PALETTES: _,bg,accent,fg=PALETTES[settings["palette"]]
    if settings.get("custom_bg"): bg=settings["custom_bg"]; accent=settings.get("custom_accent",accent); fg=settings.get("custom_text",fg)
    out=BytesIO(); c=canvas.Canvas(out,pagesize=(960,540))
    font_regular="Helvetica"; font_bold="Helvetica-Bold"
    try:
        candidates=["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
        bold_candidates=["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        regular=next(p for p in candidates if os.path.exists(p))
        bold=next((p for p in bold_candidates if os.path.exists(p)),regular)
        pdfmetrics.registerFont(TTFont("SlideUnicode",regular))
        pdfmetrics.registerFont(TTFont("SlideUnicodeBold",bold))
        font_regular="SlideUnicode"; font_bold="SlideUnicodeBold"
    except Exception: pass
    def color(v):
        try:return HexColor(v)
        except:return HexColor("#FFFFFF")
    pages=[{"heading":topic,"bullets":[settings.get("author","Student AI")] ,"cover":True}]+slides+[{"heading":"Xulosa","bullets":["E’tiboringiz uchun rahmat!"]}]
    for i,item in enumerate(pages):
        c.setFillColor(color(bg)); c.rect(0,0,960,540,fill=1,stroke=0)
        c.setStrokeColor(color(accent)); c.setLineWidth(2); c.rect(14,14,932,512,fill=0,stroke=1)
        c.setFillColor(color(accent)); c.rect(48,465,68,5,fill=1,stroke=0)
        c.setFillColor(color(fg)); c.setFont(font_bold,25 if i==0 else 20)
        c.drawString(50,420,str(item.get("heading",""))[:85])
        bullets=item.get("bullets",[]) or []
        y=365
        for b in bullets[:6]:
            c.setFont(font_regular,14); c.drawString(62,y,"• "+str(b)[:100]); y-=43
        img=images.get(i-1) if i>0 and i<=len(slides) else None
        if img:
            try:
                img.seek(0); c.drawImage(ImageReader(img),570,90,width=330,height=270,preserveAspectRatio=True,anchor="c",mask="auto")
            except Exception: pass
        c.setFont(font_regular,9); c.setFillColor(color(accent)); c.drawRightString(910,30,f"{i+1}/{len(pages)}")
        c.showPage()
    c.save(); out.seek(0); return out
