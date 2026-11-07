
"""Professional PowerPoint builder: 20 distinct visual systems, frames, palettes and layouts."""
from io import BytesIO
import os, shutil, subprocess, tempfile, logging
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.dml import MSO_THEME_COLOR
from slide_themes import THEMES, PALETTES, FRAMES

log=logging.getLogger(__name__)
W,H=13.333,7.5

def _rgb(value):
    value=str(value or "#FFFFFF").lstrip("#")
    if len(value)!=6: value="FFFFFF"
    return RGBColor(*(int(value[i:i+2],16) for i in (0,2,4)))

def _font_available(name):
    try:
        p=subprocess.run(["fc-match","-f","%{family}",name],capture_output=True,text=True,timeout=2)
        found=(p.stdout or "").lower()
        if name.lower() in found: return name
    except Exception: pass
    return "DejaVu Sans"

def _shape(slide, typ, x,y,w,h, fill, line=None, radius=False):
    sh=slide.shapes.add_shape(typ, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid(); sh.fill.fore_color.rgb=_rgb(fill)
    if line: sh.line.color.rgb=_rgb(line); sh.line.width=Pt(1.2)
    else: sh.line.fill.background()
    return sh

def _text(slide, text, x,y,w,h, size, color, font, bold=False, align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.MIDDLE):
    box=slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf=box.text_frame; tf.clear(); tf.word_wrap=True
    tf.margin_left=Inches(.04); tf.margin_right=Inches(.04)
    tf.margin_top=Inches(.02); tf.margin_bottom=Inches(.02); tf.vertical_anchor=valign
    p=tf.paragraphs[0]; p.alignment=align
    r=p.add_run(); r.text=str(text); r.font.name=font; r.font.size=Pt(size); r.font.bold=bold; r.font.color.rgb=_rgb(color)
    return box

def _set_bg(slide, color):
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb=_rgb(color)

def _frame(slide, kind, accent, bg):
    if kind=="none": return
    if kind in ("thin","gold","silver","minimal"):
        color={"gold":"#C9A227","silver":"#AEB8C2","minimal":accent}.get(kind,accent)
        m=.18 if kind=="minimal" else .12
        _shape(slide,MSO_SHAPE.RECTANGLE,m,m,W-2*m,H-2*m,bg,color)
        return
    if kind=="double":
        _shape(slide,MSO_SHAPE.RECTANGLE,.12,.12,W-.24,H-.24,bg,accent)
        _shape(slide,MSO_SHAPE.RECTANGLE,.22,.22,W-.44,H-.44,bg,accent)
    elif kind=="corners":
        for x,y,sx,sy in [(.22,.22,1,1),(W-.22,.22,-1,1),(.22,H-.22,1,-1),(W-.22,H-.22,-1,-1)]:
            _shape(slide,MSO_SHAPE.RECTANGLE,x if sx>0 else x-.45,y if sy>0 else y-.08,.45,.055,accent)
            _shape(slide,MSO_SHAPE.RECTANGLE,x if sx>0 else x-.055,y if sy>0 else y-.45,.055,.45,accent)
    elif kind=="geometric":
        for i in range(12):
            x=.25+i*1.08
            _shape(slide,MSO_SHAPE.DIAMOND,x,.16,.22,.22,accent)
            _shape(slide,MSO_SHAPE.DIAMOND,x,H-.38,.22,.22,accent)
    elif kind=="classic":
        _shape(slide,MSO_SHAPE.RECTANGLE,.18,.18,W-.36,H-.36,bg,accent)
        _shape(slide,MSO_SHAPE.RECTANGLE,.28,.28,W-.56,H-.56,bg,accent)
        for x,y in [(.35,.35),(W-.65,.35),(.35,H-.65),(W-.65,H-.65)]:
            _shape(slide,MSO_SHAPE.DIAMOND,x,y,.3,.3,accent)
    elif kind=="premium":
        _shape(slide,MSO_SHAPE.RECTANGLE,.14,.14,W-.28,H-.28,bg,"#C9A227")
        for x,y in [(.3,.3),(W-.55,.3),(.3,H-.55),(W-.55,H-.55)]:
            _shape(slide,MSO_SHAPE.STAR_5_POINT,x,y,.25,.25,"#C9A227")

def _add_image(slide, img, x,y,w,h):
    if not img: return
    try:
        if hasattr(img,"seek"): img.seek(0)
        slide.shapes.add_picture(img, Inches(x), Inches(y), width=Inches(w), height=Inches(h))
    except Exception as e: log.warning("Slide image skipped: %s",e)

def _bullets(slide, items, x,y,w,h, font, fg, size=18):
    box=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
    tf=box.text_frame; tf.clear(); tf.word_wrap=True
    tf.margin_left=Inches(.08); tf.margin_right=Inches(.04)
    for i,item in enumerate(items[:6]):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
        p.space_after=Pt(12); p.level=0
        r=p.add_run(); r.text=("•  " if i%2==0 else "—  ")+str(item)
        r.font.name=font; r.font.size=Pt(size); r.font.color.rgb=_rgb(fg)
    return box

def _chart(slide, chart, x,y,w,h, accent, fg):
    vals=chart if isinstance(chart,list) else (chart or {}).get("values",[])
    labels=(chart or {}).get("labels",[]) if isinstance(chart,dict) else []
    vals=[float(v) for v in vals[:6] if isinstance(v,(int,float))]
    if not vals: vals=[4,7,5,9]
    mx=max(vals) or 1; n=len(vals); gap=.16; bw=(w-gap*(n+1))/n
    for i,v in enumerate(vals):
        bh=max(.12,(h-.55)*v/mx); xx=x+gap+i*(bw+gap)
        _shape(slide,MSO_SHAPE.ROUNDED_RECTANGLE,xx,y+h-bh,bw,bh,accent if i%2==0 else "#73A6C8")
        _text(slide,labels[i] if i<len(labels) else str(i+1),xx,y+h-.42,bw,.28,9,fg,"DejaVu Sans",align=PP_ALIGN.CENTER)
        _text(slide,f"{v:g}",xx,y+h-bh-.28,bw,.22,9,fg,"DejaVu Sans",align=PP_ALIGN.CENTER)

def _table(slide, rows, x,y,w,h, accent, fg, bg, font):
    rows=rows[:5] if isinstance(rows,list) else []
    if not rows: rows=[["Ko‘rsatkich","Qiymat"],["A","—"],["B","—"]]
    cols=max(1,min(4,max(len(r) if isinstance(r,list) else 1 for r in rows)))
    rh=h/max(1,len(rows)); cw=w/cols
    for ri,row in enumerate(rows):
        row=row if isinstance(row,list) else [str(row)]
        for ci in range(cols):
            xx=x+ci*cw; yy=y+ri*rh
            _shape(slide,MSO_SHAPE.RECTANGLE,xx,yy,cw-.025,rh-.025,accent if ri==0 else bg)
            _text(slide,str(row[ci])[:80] if ci<len(row) else "",xx+.06,yy+.02,cw-.12,rh-.06,11,"#FFFFFF" if ri==0 else fg,font,bold=ri==0)

def _slide_base(prs, bg, frame, accent, style="modern"):
    s=prs.slides.add_slide(prs.slide_layouts[6]); _set_bg(s,bg); _frame(s,frame,accent,bg)
    # Distinctive motif per design family, repeated consistently across the deck.
    if style=="tech":
        for i in range(7):
            x=10.9+(i%3)*.55; y=2.0+(i//3)*.65
            _shape(s,MSO_SHAPE.OVAL,x,y,.10,.10,accent)
            if i<6: _shape(s,MSO_SHAPE.RECTANGLE,10.98,2.10+((i%3)*.16),.42,.025,accent)
    elif style=="creative":
        _shape(s,MSO_SHAPE.OVAL,11.3,.25,1.1,1.1,accent)
        _shape(s,MSO_SHAPE.OVAL,10.8,.55,.35,.35,"#E9A3C1")
    elif style=="nature":
        for i in range(5):
            _shape(s,MSO_SHAPE.OVAL,11.25,2.0+i*.48,.25,.55,accent)
    elif style=="medical":
        _shape(s,MSO_SHAPE.RECTANGLE,11.25,2.2,.18,.85,accent)
        _shape(s,MSO_SHAPE.RECTANGLE,10.92,2.53,.85,.18,accent)
    elif style=="finance":
        for i in range(5):
            _shape(s,MSO_SHAPE.RECTANGLE,10.9+i*.35,6.35,.18,.25+i*.12,accent)
    elif style=="education":
        _shape(s,MSO_SHAPE.PARALLELOGRAM,10.9,.25,1.1,.35,accent)
        _shape(s,MSO_SHAPE.PARALLELOGRAM,11.7,.68,.75,.25,accent)
    elif style=="science":
        _shape(s,MSO_SHAPE.OVAL,10.9,2.1,1.0,.35,bg,accent)
        _shape(s,MSO_SHAPE.OVAL,11.2,1.8,.35,1.0,bg,accent)
        _shape(s,MSO_SHAPE.OVAL,11.32,2.22,.12,.12,accent)
    elif style=="news":
        _shape(s,MSO_SHAPE.RECTANGLE,.55,.12,12.2,.035,accent)
        _shape(s,MSO_SHAPE.RECTANGLE,.55,H-.16,12.2,.035,accent)
    elif style=="info":
        for i in range(4):
            _shape(s,MSO_SHAPE.HEXAGON,11.1+(i%2)*.48,2.0+(i//2)*.5,.35,.35,accent)
    elif style=="gold":
        _shape(s,MSO_SHAPE.STAR_5_POINT,11.2,.35,.55,.55,accent)
    elif style=="glass":
        _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,10.85,1.9,1.0,1.0,"#FFFFFF",accent)
    elif style=="elegant":
        _shape(s,MSO_SHAPE.OVAL,11.15,.45,.75,.75,bg,accent)
    elif style=="corporate":
        _shape(s,MSO_SHAPE.RECTANGLE,12.45,.2,.35,6.9,accent)
    elif style=="minimal":
        _shape(s,MSO_SHAPE.RECTANGLE,.6,6.7,2.1,.025,accent)
    elif style=="dark":
        _shape(s,MSO_SHAPE.OVAL,11.15,.35,.65,.65,accent)
    elif style=="gradient":
        for i in range(5):
            _shape(s,MSO_SHAPE.RECTANGLE,10.9+i*.22,.2,.12,1.0,accent)
    elif style=="academic":
        _shape(s,MSO_SHAPE.RECTANGLE,.55,.2,12.2,.035,accent)
    return s

def build_presentation(topic, slides, author_note="", settings=None, images=None):
    settings=settings or {}
    design=settings.get("design","modern_blue")
    theme=THEMES.get(design,THEMES["modern_blue"])
    name, bg, accent, fg, style=theme
    palette=settings.get("palette","")
    if palette in PALETTES:
        _, bg, accent, fg=PALETTES[palette]
    if settings.get("custom_bg") and settings.get("custom_accent") and settings.get("custom_text"):
        bg=settings["custom_bg"]; accent=settings["custom_accent"]; fg=settings["custom_text"]
    frame=settings.get("frame","thin")
    font=_font_available(settings.get("font","Arial"))
    prs=Presentation(); prs.slide_width=Inches(W); prs.slide_height=Inches(H)
    prs.core_properties.title=str(topic); prs.core_properties.author=author_note or "Student AI"
    total=len(slides)+2

    # Cover is intentionally distinct for every theme family.
    s=_slide_base(prs,bg,frame,accent,style)
    if style in ("dark","tech","luxury","gold","gradient"):
        _shape(s,MSO_SHAPE.RECTANGLE,0,0,4.2,H,accent)
        _shape(s,MSO_SHAPE.OVAL,9.4,.7,2.5,2.5,accent)
        _text(s,"STUDENT AI  /  TAQDIMOT",4.65,.75,7.5,.35,11,fg,font,True)
        _text(s,topic,4.65,2.0,7.9,2.0,30,fg,font,True)
        _text(s,author_note or "Professional taqdimot",4.7,4.25,7.5,.6,16,fg,font)
    elif style in ("news","academic"):
        _shape(s,MSO_SHAPE.RECTANGLE,.55,.65,12.2,.05,accent)
        _text(s,"MAXSUS TAQDIMOT  •  STUDENT AI",.75,.9,11,.35,11,accent,font,True)
        _text(s,topic,.75,1.8,11.5,2.5,36,fg,font,True)
        _shape(s,MSO_SHAPE.RECTANGLE,.75,5.55,11.6,.025,accent)
        _text(s,author_note or "Tahliliy taqdimot",.8,5.8,10,.5,15,fg,font)
    else:
        _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,.65,.65,12.0,6.1,"#FFFFFF")
        _shape(s,MSO_SHAPE.RECTANGLE,.65,.65,.18,6.1,accent)
        cover_fg="#243447" if style=="modern" else fg
        _text(s,topic,1.25,2.0,10.7,1.8,36,cover_fg,font,True)
        _text(s,author_note or "Taqdimot",1.3,4.1,9,.5,17,accent,font)
        _text(s,"STUDENT AI  •  01",1.3,6.0,6,.3,10,cover_fg,font,True)

    for idx,item in enumerate(slides, start=2):
        item=item if isinstance(item,dict) else {}
        heading=str(item.get("heading") or f"{idx-1}-slayd")
        bullets=item.get("bullets") or []
        if isinstance(bullets,str): bullets=[bullets]
        bullets=[str(v) for v in bullets if str(v).strip()][:6] or ["Asosiy ma’lumotlar"]
        img=(images or {}).get(idx-2)
        layout=item.get("layout") or ["split_right","split_left","cards","chart","full_image","two_col","text_focus","table"][((idx-2)%8)]
        s=_slide_base(prs,bg,frame,accent,style)
        _text(s,f"{idx-1:02d}  /  {total-2:02d}",.65,.35,1.1,.3,9,accent,font,True)
        _text(s,heading,.8,.72,11.8,.9,25,fg,font,True)
        _shape(s,MSO_SHAPE.RECTANGLE,.8,1.62,1.0,.055,accent)
        if layout=="split_left":
            _add_image(s,img,.8,2.0,5.4,4.5); _bullets(s,bullets,6.6,2.0,5.7,4.7,font,fg,17)
        elif layout=="full_image":
            _add_image(s,img,.8,1.95,7.0,4.8)
            _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,8.1,2.15,4.2,4.2,"#FFFFFF")
            _bullets(s,bullets,8.35,2.45,3.7,3.6,font,fg,15)
        elif layout=="cards":
            n=min(3,len(bullets)); cw=3.75
            for j in range(n):
                x=.8+j*4.0
                _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,x,2.15,cw,3.65,"#FFFFFF")
                _shape(s,MSO_SHAPE.OVAL,x+.25,2.45,.55,.55,accent)
                _text(s,f"0{j+1}",x+.27,2.53,.5,.25,12,"#FFFFFF",font,True,PP_ALIGN.CENTER)
                _text(s,bullets[j],x+.25,3.25,cw-.5,2.1,16,fg,font,True)
        elif layout=="chart":
            _bullets(s,bullets[:3],.85,2.0,5.1,4.6,font,fg,16)
            _chart(s,item.get("chart"),6.3,2.3,5.8,3.7,accent,fg)
        elif layout=="table":
            _table(s,item.get("table") or [],.9,2.0,11.5,4.5,accent,fg,"#FFFFFF",font)
        elif layout=="two_col":
            mid=(len(bullets)+1)//2
            _bullets(s,bullets[:mid],.9,2.0,5.45,4.7,font,fg,17)
            _bullets(s,bullets[mid:],6.9,2.0,5.4,4.7,font,fg,17)
            _add_image(s,img,5.75,3.15,1.0,1.0)
        elif layout=="text_focus":
            _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,.85,2.0,11.6,4.7,"#FFFFFF")
            _bullets(s,bullets,.1+1.05,2.25,10.6,4.2,font,fg,19)
        else:
            _bullets(s,bullets,.9,2.05,5.4,4.6,font,fg,17)
            _add_image(s,img,6.7,2.0,5.6,4.5)
        _text(s,f"STUDENT AI     •     {idx}/{total}",9.4,7.0,3.0,.2,8,accent,font,align=PP_ALIGN.RIGHT)

    # Closing slide with references when supplied.
    last=slides[-1] if slides else {}
    refs=last.get("references") if isinstance(last,dict) else None
    s=_slide_base(prs,bg,frame,accent,style)
    _shape(s,MSO_SHAPE.RECTANGLE,.75,1.0,.12,5.3,accent)
    _text(s,"XULOSA",1.2,1.2,10,.6,14,accent,font,True)
    _text(s,"E’tiboringiz uchun rahmat!",1.2,2.0,10.8,1.4,31,fg,font,True)
    if refs:
        _text(s,"Foydalanilgan manbalar:",1.25,4.0,5,.35,12,accent,font,True)
        _bullets(s,[str(x) for x in refs[:4]],1.25,4.4,10.7,1.7,font,fg,10)
    _text(s,f"{total}/{total}",11.4,6.9,1,.25,9,accent,font,align=PP_ALIGN.RIGHT)
    out=BytesIO(); prs.save(out); out.seek(0); return out
