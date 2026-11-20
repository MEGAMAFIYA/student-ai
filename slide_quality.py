"""Content validation and deterministic layout selection for Student AI slides."""
from __future__ import annotations
import re
from collections import Counter

CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
LATIN_RE = re.compile(r"[A-Za-zÀ-ž]")
ENGLISH_PHRASES = re.compile(r"\b(the|and|with|for|from|this|that|professional|educational|topic|slide)\b", re.I)
ALLOWED_LAYOUTS = {
    "title","text_focus","two_col","cards","full_image","chart","table",
    "timeline","comparison","process","statistics","infographic",
    "split_right","split_left",
}
CHART_TYPES = {"bar","line","horizontal_bar","pie","comparison","keynesian_cross","investment_interest","multiplier"}
MAX_BULLETS = 5

def _text(v):
    return str(v or "").strip()

def visible_text(slide: dict) -> list[str]:
    out=[_text(slide.get("heading"))]
    bullets=slide.get("bullets") or []
    if isinstance(bullets,str): bullets=[bullets]
    out.extend(_text(x) for x in bullets)
    table=slide.get("table") or []
    if isinstance(table,list):
        for row in table:
            if isinstance(row,list): out.extend(_text(x) for x in row)
    chart=slide.get("chart")
    if isinstance(chart,dict):
        out.extend(_text(x) for x in chart.get("labels",[]))
        out.append(_text(chart.get("title")))
        out.append(_text(chart.get("x_label")))
        out.append(_text(chart.get("y_label")))
    return [x for x in out if x]

def has_cyrillic(text: str) -> bool:
    return bool(CYRILLIC_RE.search(text or ""))

def has_probable_english(text: str) -> bool:
    return bool(ENGLISH_PHRASES.search(text or ""))

def normalize_slide(item: dict, index: int) -> dict:
    item = dict(item or {})
    heading=_text(item.get("heading"))[:140]
    bullets=item.get("bullets") or []
    if isinstance(bullets,str): bullets=[bullets]
    clean=[]
    for b in bullets[:MAX_BULLETS]:
        b=_text(b)
        if b and not has_cyrillic(b) and not has_probable_english(b):
            clean.append(b[:320])
        elif b:
            clean.append(b[:320])
    item["heading"]=heading or f"{index+1}-slayd"
    item["bullets"]=clean
    layout=_text(item.get("layout")).lower()
    item["layout"]=layout if layout in ALLOWED_LAYOUTS else ""
    chart=item.get("chart")
    if isinstance(chart,dict):
        vals=chart.get("values")
        labels=chart.get("labels")
        good_vals=isinstance(vals,list) and all(isinstance(v,(int,float)) and v == v for v in vals) and len(vals)>=2
        good_labels=not labels or (isinstance(labels,list) and len(labels)==len(vals))
        if not (good_vals and good_labels):
            item["chart"]=None
        else:
            item["chart"]={**chart, "values":[float(v) for v in vals[:8]], "labels":[str(x)[:40] for x in (labels or [])][:8]}
    elif isinstance(chart,list):
        if len(chart)>=2 and all(isinstance(v,(int,float)) for v in chart):
            item["chart"]={"values":[float(v) for v in chart[:8]],"labels":[]}
        else:
            item["chart"]=None
    else:
        item["chart"]=None
    table=item.get("table")
    if not isinstance(table,list) or not table or not all(isinstance(r,list) for r in table):
        item["table"]=None
    else:
        item["table"]=[[_text(x)[:80] for x in r[:6]] for r in table[:7] if r]
    if not item["bullets"] and not item["chart"] and not item["table"]:
        item["bullets"]=["Asosiy tushunchalarni qisqa va aniq bayon qiling."]
    return item

def _keywords(text: str) -> set[str]:
    t=text.lower().replace("’","'")
    return set(re.findall(r"[a-zʻ'‘-]{3,}",t))

def choose_layout(slide: dict, previous: str|None=None) -> str:
    """Choose a layout from content evidence; never invents chart/table data."""
    s=normalize_slide(slide,0)
    heading=s["heading"].lower()
    bullets=s["bullets"]
    n=sum(len(x) for x in bullets)
    kws=_keywords(heading+" "+" ".join(bullets))
    chart=s.get("chart")
    table=s.get("table")
    if chart:
        ctype=str(chart.get("type","bar")).lower()
        if ctype in {"keynesian_cross","investment_interest","multiplier"}: candidate="chart"
        else: candidate="chart"
    elif table:
        candidate="table"
    elif any(k in kws for k in ("bosqich","jarayon","qadam","amalga","ketma")) and len(bullets)>=3:
        candidate="process"
    elif any(k in kws for k in ("tarix","davr","yil","asr","xronolog")) and len(bullets)>=3:
        candidate="timeline"
    elif any(k in kws for k in ("taqqos","farq","afzallik","kamchilik","klassik","zamonaviy")) and len(bullets)>=2:
        candidate="comparison"
    elif any(k in kws for k in ("statistika","foiz","ulush","miqdor","ko'rsatkich","ko‘rsatkich")) and len(bullets)<=3:
        candidate="statistics"
    elif len(bullets)>=3 and n < 480:
        candidate="cards"
    elif n > 650 or len(bullets)>=4:
        candidate="text_focus"
    elif len(bullets)>=2:
        candidate="split_right"
    else:
        candidate="infographic"
    # Avoid monotonous repetition without making the design random.
    if candidate == previous:
        alternatives={"cards":"split_right","split_right":"two_col","two_col":"infographic",
                      "text_focus":"cards","timeline":"process","comparison":"cards",
                      "statistics":"infographic","infographic":"split_right","process":"timeline"}
        candidate=alternatives.get(candidate,candidate)
    return candidate

def validate_deck(slides:list[dict], expected_count:int) -> tuple[bool,list[str]]:
    errors=[]
    if len(slides)!=expected_count: errors.append(f"Slaydlar soni {len(slides)}, kutilgani {expected_count}.")
    seen=Counter()
    for i,s in enumerate(slides):
        if not s.get("heading"): errors.append(f"{i+1}-slaydda sarlavha yo‘q.")
        seen[_text(s.get("heading")).casefold()]+=1
        for text in visible_text(s):
            if has_cyrillic(text): errors.append(f"{i+1}-slaydda kirill yozuvi topildi.")
        if s.get("chart") and not isinstance(s["chart"],dict): errors.append(f"{i+1}-slayd diagrammasi noto‘g‘ri.")
    for h,c in seen.items():
        if h and c>1: errors.append(f"Takroriy sarlavha: {h}")
    return not errors, errors

def clean_references(refs) -> list[str]:
    if not isinstance(refs,list): return []
    out=[]
    for r in refs[:8]:
        r=_text(r)
        # We do not claim verification; preserve only explicit-looking references.
        if len(r)>=8 and (re.search(r"\b(19|20)\d{2}\b",r) or "doi.org" in r.lower() or "http" in r.lower()):
            out.append(r[:320])
    return out
