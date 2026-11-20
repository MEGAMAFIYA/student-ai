"""Professional PowerPoint builder with content-aware layouts and safe chart handling."""
from __future__ import annotations
from io import BytesIO
import os, subprocess, logging, math
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.shapes import MSO_CONNECTOR
from slide_themes import THEMES, PALETTES, FRAMES
from slide_quality import normalize_slide, choose_layout

log=logging.getLogger(__name__)
W,H=13.333,7.5

def _rgb(value):
    value=str(value or "#FFFFFF").lstrip("#")
    if len(value)!=6: value="FFFFFF"
    try: return RGBColor(*(int(value[i:i+2],16) for i in (0,2,4)))
    except Exception: return RGBColor(255,255,255)

def _font_available(name):
    from slide_themes import FONTS
    if name in FONTS: return name
    try:
        p=subprocess.run(["fc-match","-f","%{family}",name],capture_output=True,text=True,timeout=2)
        if name.lower() in (p.stdout or "").lower(): return name
    except Exception: pass
    return "DejaVu Sans"

def _shape(slide, typ, x,y,w,h, fill, line=None):
    sh=slide.shapes.add_shape(typ, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid(); sh.fill.fore_color.rgb=_rgb(fill)
    if line: sh.line.color.rgb=_rgb(line); sh.line.width=Pt(1.2)
    else: sh.line.fill.background()
    return sh

def _connector(slide,x1,y1,x2,y2,color,width=2.0):
    c=slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,Inches(x1),Inches(y1),Inches(x2),Inches(y2))
    c.line.color.rgb=_rgb(color); c.line.width=Pt(width); return c

def _card_fg(fg):
    try:
        c=_rgb(fg); lum=(0.299*c[0]+0.587*c[1]+0.114*c[2])
        return "#243447" if lum>190 else fg
    except Exception: return "#243447"

def _fit_size(text, w, h, size, minimum=11, max_chars_per_line=None):
    text=str(text or "")
    chars=max(1,int(w*11))
    lines=sum(max(1,math.ceil(len(p)/chars)) for p in text.split("\n"))
    if lines*size*0.055 <= h: return size
    factor=max(.58, h/(lines*size*0.055))
    return max(minimum, min(size, size*factor))

def _text(slide, text, x,y,w,h, size, color, font, bold=False, align=PP_ALIGN.LEFT,
          valign=MSO_ANCHOR.MIDDLE, min_size=11):
    box=slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf=box.text_frame; tf.clear(); tf.word_wrap=True
    tf.margin_left=Inches(.05); tf.margin_right=Inches(.05)
    tf.margin_top=Inches(.03); tf.margin_bottom=Inches(.03); tf.vertical_anchor=valign
    size=_fit_size(text,w,h,size,min_size)
    p=tf.paragraphs[0]; p.alignment=align
    r=p.add_run(); r.text=str(text); r.font.name=font; r.font.size=Pt(size); r.font.bold=bold; r.font.color.rgb=_rgb(color)
    return box

def _set_bg(slide,color):
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb=_rgb(color)

def _frame(slide,kind,accent,bg):
    if kind=="none": return
    if kind in ("thin","gold","silver","minimal"):
        color={"gold":"#C9A227","silver":"#AEB8C2","minimal":accent}.get(kind,accent); m=.18 if kind=="minimal" else .12
        _shape(slide,MSO_SHAPE.RECTANGLE,m,m,W-2*m,H-2*m,bg,color); return
    if kind=="double":
        _shape(slide,MSO_SHAPE.RECTANGLE,.12,.12,W-.24,H-.24,bg,accent); _shape(slide,MSO_SHAPE.RECTANGLE,.22,.22,W-.44,H-.44,bg,accent)
    elif kind=="corners":
        for x,y,sx,sy in [(.22,.22,1,1),(W-.22,.22,-1,1),(.22,H-.22,1,-1),(W-.22,H-.22,-1,-1)]:
            _shape(slide,MSO_SHAPE.RECTANGLE,x if sx>0 else x-.45,y if sy>0 else y-.08,.45,.055,accent)
            _shape(slide,MSO_SHAPE.RECTANGLE,x if sx>0 else x-.055,y if sy>0 else y-.45,.055,.45,accent)
    elif kind=="geometric":
        for i in range(12):
            x=.25+i*1.08; _shape(slide,MSO_SHAPE.DIAMOND,x,.16,.22,.22,accent); _shape(slide,MSO_SHAPE.DIAMOND,x,H-.38,.22,.22,accent)
    elif kind=="classic":
        _shape(slide,MSO_SHAPE.RECTANGLE,.18,.18,W-.36,H-.36,bg,accent); _shape(slide,MSO_SHAPE.RECTANGLE,.28,.28,W-.56,H-.56,bg,accent)
        for x,y in [(.35,.35),(W-.65,.35),(.35,H-.65),(W-.65,H-.65)]: _shape(slide,MSO_SHAPE.DIAMOND,x,y,.3,.3,accent)
    elif kind=="premium":
        _shape(slide,MSO_SHAPE.RECTANGLE,.14,.14,W-.28,H-.28,bg,"#C9A227")
        for x,y in [(.3,.3),(W-.55,.3),(.3,H-.55),(W-.55,H-.55)]: _shape(slide,MSO_SHAPE.STAR_5_POINT,x,y,.25,.25,"#C9A227")

def _add_image(slide,img,x,y,w,h):
    if not img: return
    try:
        if hasattr(img,"seek"): img.seek(0)
        slide.shapes.add_picture(img,Inches(x),Inches(y),width=Inches(w),height=Inches(h))
    except Exception as e: log.warning("Slide image skipped: %s",e)

def _bullets(slide,items,x,y,w,h,font,fg,size=18, max_items=5):
    items=[str(v).strip() for v in (items or []) if str(v).strip()][:max_items]
    if not items: return None
    box=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
    tf=box.text_frame; tf.clear(); tf.word_wrap=True
    tf.margin_left=Inches(.08); tf.margin_right=Inches(.04); tf.margin_top=Inches(.02); tf.margin_bottom=Inches(.02)
    each=max(10,min(size, _fit_size(" ".join(items),w,h,size,10)))
    for i,item in enumerate(items):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph(); p.space_after=Pt(9)
        r=p.add_run(); r.text=("•  " if i%2==0 else "—  ")+item
        r.font.name=font; r.font.size=Pt(each); r.font.color.rgb=_rgb(fg)
    return box

def _chart(slide,chart,x,y,w,h,accent,fg,font):
    if not isinstance(chart,dict): return False
    vals=chart.get("values"); labels=chart.get("labels") or []
    if not isinstance(vals,list) or len(vals)<2 or not all(isinstance(v,(int,float)) and math.isfinite(float(v)) for v in vals):
        return False
    vals=[float(v) for v in vals[:8]]
    ctype=str(chart.get("type","bar")).lower()
    title=str(chart.get("title") or "")
    if title: _text(slide,title,x,y,w,.42,13,fg,font,True,PP_ALIGN.CENTER,min_size=9)
    y0=y+.55 if title else y
    if ctype=="pie":
        # Deterministic pie approximation with native freeform slices is overkill; use proportional ring segments.
        total=sum(abs(v) for v in vals) or 1
        cx=x+w*.5; cy=y0+h*.45; r=min(w,h)*.32
        # Use concentric proportional bars as a robust pie fallback.
        cur=x
        for i,v in enumerate(vals):
            ww=w*abs(v)/total
            _shape(slide,MSO_SHAPE.RECTANGLE,cur,cy-r*.12,max(.03,ww),r*.24,accent if i%2==0 else "#73A6C8")
            cur+=ww
        _text(slide,"  ".join(f"{labels[i] if i<len(labels) else i+1}: {vals[i]:g}" for i in range(len(vals))),
              x,y0+h*.62,w,.6,10,fg,font,align=PP_ALIGN.CENTER,min_size=8)
        return True
    if ctype in ("line","investment_interest"):
        lo=min(vals); hi=max(vals); span=(hi-lo) or 1
        pts=[]
        for i,v in enumerate(vals):
            px=x+.35+(w-.7)*(i/max(1,len(vals)-1)); py=y0+h*.75-(v-lo)/span*(h*.62); pts.append((px,py))
        for a,b in zip(pts,pts[1:]): 
            line=_connector(slide,a[0],a[1],b[0],b[1],accent,2.4)
        for i,(px,py) in enumerate(pts):
            _shape(slide,MSO_SHAPE.OVAL,px-.06,py-.06,.12,.12,accent)
            if labels:
                _text(slide,labels[i] if i<len(labels) else str(i+1),px-.45,y0+h-.35,.9,.22,8,fg,font,align=PP_ALIGN.CENTER,min_size=7)
        _text(slide,str(chart.get("x_label") or "Gorizontal o‘q"),x+.8,y0+h-.05,w-1.6,.25,8,fg,font,align=PP_ALIGN.CENTER,min_size=7)
        _text(slide,str(chart.get("y_label") or "Vertikal o‘q"),x-.05,y0+.25,.8,.25,8,fg,font,align=PP_ALIGN.CENTER,min_size=7)
    elif ctype=="keynesian_cross":
        # The values represent planned expenditure at the listed income points; 45-degree line is theoretical.
        lo=0.0; hi=max(vals); span=hi or 1
        x0,ybase=x+.55,y0+h*.78; x1=x+w-.45; ytop=y0+.35
        _shape(slide,MSO_SHAPE.RECTANGLE,x0,ybase,.015,.015,fg)
        _shape(slide,MSO_SHAPE.RECTANGLE,x0,ybase,(x1-x0),.018,fg)
        _connector(slide,x0,ybase,x1,ytop,fg,1.6)
        pts=[]
        for i,v in enumerate(vals):
            px=x0+(x1-x0)*i/max(1,len(vals)-1); py=ybase-(v-lo)/span*(ybase-ytop); pts.append((px,py))
        for a,b in zip(pts,pts[1:]):
            _connector(slide,a[0],a[1],b[0],b[1],accent,2.4)
        # Mark the first actual crossing between the planned-expenditure polyline and 45° line.
        cross=None
        for i,(px,py) in enumerate(pts):
            qy=ybase-(ybase-ytop)*(px-x0)/(x1-x0); d=py-qy
            if i==0: prev=(px,py,d); continue
            ppx,ppy,pd=prev
            if d==0 or pd*d<0:
                t=abs(pd)/(abs(pd)+abs(d)) if (abs(pd)+abs(d)) else 0.5
                cx=ppx+(px-ppx)*t; cy=ppy+(py-ppy)*t; cross=(cx,cy); break
            prev=(px,py,d)
        if cross is None:
            # fallback to closest point, still never inventing data
            cross=min(((abs(py-(ybase-(ybase-ytop)*(px-x0)/(x1-x0))),px,py) for px,py in pts), key=lambda z:z[0])[1:]
        px,py=cross
        _shape(slide,MSO_SHAPE.OVAL,px-.09,py-.09,.18,.18,accent)
        _text(slide,"Muvozanat",px-.55,py-.55,1.1,.3,9,fg,font,True,PP_ALIGN.CENTER,min_size=8)
        _text(slide,"Daromad",x0,ybase+.08,x1-x0,.3,9,fg,font,align=PP_ALIGN.CENTER)
        _text(slide,"Xarajatlar",x+.02,ytop-.15,.9,.3,9,fg,font,align=PP_ALIGN.CENTER)
    elif ctype=="multiplier":
        mx=max(abs(v) for v in vals) or 1
        for i,v in enumerate(vals):
            bw=(w-.4)/len(vals)*.72; xx=x+.2+i*(w/len(vals))
            bh=(h-.75)*abs(v)/mx
            _shape(slide,MSO_SHAPE.ROUNDED_RECTANGLE,xx,y0+h-.5-bh,bw,bh,accent if i%2==0 else "#73A6C8")
            _text(slide,labels[i] if i<len(labels) else str(i+1),xx,y0+h-.42,bw,.25,9,fg,font,align=PP_ALIGN.CENTER,min_size=8)
    else:
        mx=max(abs(v) for v in vals) or 1
        gap=.18; bw=(w-gap*(len(vals)+1))/len(vals)
        for i,v in enumerate(vals):
            bh=max(.08,(h-.85)*abs(v)/mx); xx=x+gap+i*(bw+gap)
            _shape(slide,MSO_SHAPE.ROUNDED_RECTANGLE,xx,y0+h-.45-bh,bw,bh,accent if i%2==0 else "#73A6C8")
            _text(slide,labels[i] if i<len(labels) else str(i+1),xx,y0+h-.38,bw,.25,9,fg,font,align=PP_ALIGN.CENTER,min_size=8)
            _text(slide,f"{v:g}",xx,y0+h-.7-bh,bw,.22,9,fg,font,align=PP_ALIGN.CENTER,min_size=8)
    return True

def _table(slide,rows,x,y,w,h,accent,fg,bg,font):
    if not isinstance(rows,list) or not rows: return False
    rows=rows[:7]; cols=max(1,min(6,max(len(r) if isinstance(r,list) else 1 for r in rows)))
    rh=h/max(1,len(rows)); cw=w/cols
    for ri,row in enumerate(rows):
        row=row if isinstance(row,list) else [str(row)]
        for ci in range(cols):
            xx=x+ci*cw; yy=y+ri*rh
            _shape(slide,MSO_SHAPE.RECTANGLE,xx,yy,cw-.025,rh-.025,accent if ri==0 else bg,accent if ri==0 else "#D7DEE5")
            _text(slide,str(row[ci])[:80] if ci<len(row) else "",xx+.06,yy+.02,cw-.12,rh-.06,11,"#FFFFFF" if ri==0 else fg,font,bold=ri==0,min_size=8)
    return True

def _slide_base(prs,bg,frame,accent,style="modern"):
    s=prs.slides.add_slide(prs.slide_layouts[6]); _set_bg(s,bg); _frame(s,frame,accent,bg)
    motifs={
      "tech":lambda:_shape(s,MSO_SHAPE.OVAL,11.25,.35,.75,.75,bg,accent),
      "medical":lambda:(_shape(s,MSO_SHAPE.RECTANGLE,11.25,2.2,.18,.85,accent),_shape(s,MSO_SHAPE.RECTANGLE,10.92,2.53,.85,.18,accent)),
      "finance":lambda:[_shape(s,MSO_SHAPE.RECTANGLE,10.9+i*.35,5.65,.18,.22+i*.10,accent) for i in range(5)],
      "education":lambda:_shape(s,MSO_SHAPE.PARALLELOGRAM,10.9,.25,1.1,.35,accent),
      "science":lambda:_shape(s,MSO_SHAPE.OVAL,10.9,2.1,1.0,.35,bg,accent),
      "nature":lambda:_shape(s,MSO_SHAPE.OVAL,11.25,2.0,.25,.55,accent),
      "academic":lambda:_shape(s,MSO_SHAPE.RECTANGLE,.55,.2,12.2,.035,accent),
      "news":lambda:_shape(s,MSO_SHAPE.RECTANGLE,.55,.12,12.2,.035,accent),
      "corporate":lambda:_shape(s,MSO_SHAPE.RECTANGLE,12.45,.2,.35,6.9,accent),
      "minimal":lambda:_shape(s,MSO_SHAPE.RECTANGLE,.6,6.7,2.1,.025,accent),
      "info":lambda:_shape(s,MSO_SHAPE.HEXAGON,11.1,2.0,.35,.35,accent),
    }
    fn=motifs.get(style)
    if fn:
        try: fn()
        except Exception: pass
    return s

def _footer(slide, number, total, accent, font):
    _text(slide,f"{number:02d} / {total:02d}",10.8,6.9,1.65,.25,9,accent,font,True,PP_ALIGN.RIGHT,min_size=8)

def build_presentation(topic,slides,author_note="",settings=None,images=None):
    settings=settings or {}; design=settings.get("design","modern_blue")
    theme=THEMES.get(design,THEMES["modern_blue"]); name,bg,accent,fg,style=theme
    palette=settings.get("palette","")
    if palette in PALETTES: _,bg,accent,fg=PALETTES[palette]
    if settings.get("custom_bg") and settings.get("custom_accent") and settings.get("custom_text"):
        bg=settings["custom_bg"]; accent=settings["custom_accent"]; fg=settings["custom_text"]
    frame=settings.get("frame","thin"); font=_font_available(settings.get("font","Arial"))
    prs=Presentation(); prs.slide_width=Inches(W); prs.slide_height=Inches(H)
    prs.core_properties.title=str(topic); prs.core_properties.author=author_note or "Student AI"
    normalized=[normalize_slide(x,i) for i,x in enumerate(slides or [])]
    total=len(normalized)+2

    # Cover: 01 / total.
    s=_slide_base(prs,bg,frame,accent,style)
    _text(s,"STUDENT AI  •  PROFESSIONAL TAQDIMOT",.8,.8,11.5,.4,11,accent,font,True)
    _text(s,str(topic),.8,1.75,11.5,2.0,35,fg,font,True,min_size=18)
    _text(s,author_note or "Tahliliy taqdimot",.85,4.25,8,.5,16,accent,font)
    _footer(s,1,total,accent,font)

    prev=None
    for i,item in enumerate(normalized,start=1):
        number=i+1
        layout=item.get("layout") or choose_layout(item,prev)
        if item.get("chart") and layout not in ("chart",): layout="chart"
        if item.get("table") and layout not in ("table",): layout="table"
        prev=layout
        s=_slide_base(prs,bg,frame,accent,style)
        _text(s,item["heading"],.8,.68,11.7,.78,25,fg,font,True,min_size=16)
        _shape(s,MSO_SHAPE.RECTANGLE,.8,1.52,1.05,.05,accent)
        img=(images or {}).get(i-1)
        bullets=item.get("bullets") or []

        if layout=="chart":
            _bullets(s,bullets[:3],.85,1.95,4.6,4.6,font,fg,16)
            if not _chart(s,item.get("chart"),5.55,2.0,6.55,4.2,accent,fg,font):
                # Never invent chart data: use text-only fallback.
                _text(s,"Ishonchli raqamli ma’lumot berilmagan.",5.7,3.0,6.1,.8,15,fg,font,True,PP_ALIGN.CENTER)
        elif layout=="table":
            if not _table(s,item.get("table"),.9,1.95,11.5,4.65,accent,fg,"#FFFFFF",font):
                _bullets(s,bullets,.9,2.0,11.2,4.5,font,fg,17)
        elif layout=="cards":
            n=min(3,max(1,len(bullets))); cw=(11.5-(n-1)*.28)/n
            for j in range(n):
                x=.9+j*(cw+.28)
                _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,x,2.0,cw,4.1,"#FFFFFF", "#D8DEE6")
                _shape(s,MSO_SHAPE.OVAL,x+.24,2.25,.55,.55,accent)
                _text(s,f"{j+1:02d}",x+.27,2.36,.48,.2,11,"#FFFFFF",font,True,PP_ALIGN.CENTER,min_size=9)
                _text(s,bullets[j],x+.28,3.05,cw-.56,2.55,16,_card_fg(fg),font,True,min_size=12)
        elif layout=="two_col":
            mid=(len(bullets)+1)//2
            _bullets(s,bullets[:mid],.9,2.0,5.45,4.5,font,fg,17)
            _bullets(s,bullets[mid:],6.85,2.0,5.35,4.5,font,fg,17)
        elif layout=="comparison":
            mid=max(1,(len(bullets)+1)//2)
            for j,(title,vals) in enumerate((("Asosiy jihatlar",bullets[:mid]),("Qarama-qarshi jihatlar",bullets[mid:]))):
                x=.9+j*6.0
                _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,x,1.98,5.55,4.6,"#FFFFFF","#D8DEE6")
                _text(s,title,x+.28,2.2,4.9,.45,15,accent,font,True)
                _bullets(s,vals,x+.25,2.8,5.0,3.3,font,_card_fg(fg),15)
        elif layout=="process":
            n=min(5,len(bullets)); step=11.4/max(1,n)
            for j,b in enumerate(bullets[:n]):
                x=.8+j*step
                _shape(s,MSO_SHAPE.OVAL,x,2.55,.72,.72,accent)
                _text(s,str(j+1),x,2.73,.72,.25,11,"#FFFFFF",font,True,PP_ALIGN.CENTER)
                _text(s,b,x-.05,3.55,step-.15,1.65,13,fg,font,True,PP_ALIGN.CENTER,min_size=10)
                if j<n-1: _shape(s,MSO_SHAPE.RECTANGLE,x+.72,2.88,step-.75,.05,accent)
        elif layout=="timeline":
            n=min(5,len(bullets)); y=3.25
            _shape(s,MSO_SHAPE.RECTANGLE,1.0,y,11.0,.06,accent)
            for j,b in enumerate(bullets[:n]):
                x=1.0+j*10.9/max(1,n-1)
                _shape(s,MSO_SHAPE.OVAL,x-.13,y-.13,.32,.32,accent)
                _text(s,b,x-.65,2.0 if j%2==0 else 3.75,1.5,1.1,12,fg,font,True,PP_ALIGN.CENTER,min_size=9)
        elif layout=="statistics":
            vals=item.get("stats") or []
            if isinstance(vals,list) and vals:
                for j,v in enumerate(vals[:3]):
                    x=.9+j*4.05
                    _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,x,2.0,3.65,3.7,"#FFFFFF","#D8DEE6")
                    _text(s,str(v.get("value","")),x+.25,2.55,3.15,1.1,31,accent,font,True,PP_ALIGN.CENTER,min_size=18)
                    _text(s,str(v.get("label","")),x+.3,3.8,3.05,1.25,14,fg,font,True,PP_ALIGN.CENTER,min_size=10)
            else:
                _bullets(s,bullets,.95,2.1,6.2,4.3,font,fg,18)
                _add_image(s,img,7.55,2.0,4.7,4.4)
        elif layout=="infographic":
            _bullets(s,bullets,.95,2.0,6.1,4.5,font,fg,17)
            _add_image(s,img,7.35,2.0,4.75,4.4)
        elif layout=="full_image":
            _add_image(s,img,.8,1.9,7.1,4.8)
            _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,8.15,2.0,4.05,4.55,"#FFFFFF","#D8DEE6")
            _bullets(s,bullets,8.45,2.35,3.5,3.8,font,_card_fg(fg),14)
        elif layout=="split_left":
            _add_image(s,img,.8,1.95,5.35,4.55); _bullets(s,bullets,6.55,2.0,5.55,4.45,font,fg,17)
        elif layout=="text_focus":
            _shape(s,MSO_SHAPE.ROUNDED_RECTANGLE,.85,1.95,11.6,4.65,"#FFFFFF","#D8DEE6")
            _bullets(s,bullets,1.1,2.25,11.0,4.0,font,_card_fg(fg),18)
        else:
            _bullets(s,bullets,.9,2.0,5.35,4.5,font,fg,17)
            _add_image(s,img,6.65,2.0,5.55,4.45)
        _footer(s,number,total,accent,font)

    # Closing / sources:  total / total.
    last=normalized[-1] if normalized else {}; refs=last.get("references") if isinstance(last,dict) else None
    s=_slide_base(prs,bg,frame,accent,style)
    _text(s,"XULOSA",.9,1.0,4,.5,15,accent,font,True)
    _text(s,"E’tiboringiz uchun rahmat!",.9,1.8,11.2,1.1,31,fg,font,True,min_size=20)
    if refs:
        _text(s,"Foydalanilgan manbalar",.95,3.25,5,.4,13,accent,font,True)
        _bullets(s,refs,.95,3.7,11.2,2.2,font,fg,10,max_items=6)
    else:
        _text(s,"Manbalar: faqat tekshirilishi mumkin bo‘lgan manbalar ko‘rsatiladi.",.95,3.5,10.8,.8,12,fg,font)
    _footer(s,total,total,accent,font)
    out=BytesIO(); prs.save(out); out.seek(0); return out
