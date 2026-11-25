#!/usr/bin/env python3
# Run from the Student AI repository root:
# python apply_topic_slide_recommendation.py

from pathlib import Path

ROOT = Path(__file__).resolve().parent

def replace_once(path, old, new):
    file = ROOT / path
    source = file.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one matching block, found {count}.")
    file.write_text(source.replace(old, new, 1), encoding="utf-8")

theme_file = ROOT / "slide_themes.py"
theme_text = theme_file.read_text(encoding="utf-8")
if "def recommend_theme(topic: str)" not in theme_text:
    theme_text += r'''

def recommend_theme(topic: str) -> str:
    """Mavzu kalit so'zlari asosida eng mos original Student AI dizaynini tavsiya qiladi."""
    value = str(topic or "").lower().replace("’", "'").replace("‘", "'")
    groups = (
        ("medical", ("tibbiyot", "salomat", "kasallik", "anatomi", "yurak", "qon aylanish", "shifokor", "farmats", "inson organizm")),
        ("technology", ("sun'iy intellekt", "texnolog", "dastur", "kompyuter", "robot", "raqamli", "kiber", "algoritm", "internet", "neyron")),
        ("finance", ("iqtisod", "moliya", "bank", "bozor", "investits", "biznes", "marketing", "menejment", "buxgalter", "makro")),
        ("history", ("tarix", "qadimgi", "siviliz", "urush", "mustaqillik", "temur", "xonlik", "tarixiy")),
        ("nature", ("ekolog", "tabiat", "iqlim", "hayvon", "o'simlik", "geograf", "atrof-muhit")),
        ("science", ("fizika", "kimyo", "matematika", "astronom", "laborator", "formula", "energiya", "tadqiqot")),
        ("education", ("ta'lim", "pedagog", "maktab", "universitet", "dars", "o'qit", "talaba", "metodika")),
        ("creative", ("san'at", "dizayn", "adabiyot", "she'r", "musiqa", "madaniyat", "kino", "fotograf")),
        ("academic", ("kurs ishi", "referat", "dissertats", "ilmiy ish", "nazariya", "mustaqil ish")),
    )
    for theme, words in groups:
        if any(word in value for word in words):
            return theme
    return "modern_blue"
'''
    theme_file.write_text(theme_text, encoding="utf-8")

handler_path = "handlers/pptx_gen.py"
replace_once(handler_path,
    "from slide_themes import THEMES, PALETTES, FRAMES, FONTS",
    "from slide_themes import THEMES, PALETTES, FRAMES, FONTS, recommend_theme")
replace_once(handler_path,
    "_NEXT_STEP={sp.STEP_COUNT:sp.STEP_DESIGN,sp.STEP_DESIGN:sp.STEP_FRAME,sp.STEP_FRAME:sp.STEP_PALETTE,\n            sp.STEP_PALETTE:sp.STEP_FONT}",
    "_NEXT_STEP={sp.STEP_DESIGN:sp.STEP_COUNT,sp.STEP_COUNT:sp.STEP_FRAME,sp.STEP_FRAME:sp.STEP_PALETTE,\n            sp.STEP_PALETTE:sp.STEP_FONT}")

old_caption = '''def _carousel_caption(step,idx):
    icon,title=sp.STEP_TITLES[step]
    _,label=sp.items(step)[idx]
    return (f"{icon} <b>{html.escape(title)}: {html.escape(label)}</b>\\n"
            f"Namuna {idx+1} / {sp.total(step)}\\n\\n"
            f"{html.escape(sp.describe(step,idx))}\\n\\n"
            "Yoqsa «✅ Tasdiqlash»ni bosing yoki ⬅️ ➡️ bilan boshqa namunalarni ko‘ring.")'''
new_caption = '''def _carousel_caption(step,idx,recommended_design=None):
    icon,title=sp.STEP_TITLES[step]
    _,label=sp.items(step)[idx]
    note = "⭐ Mavzuingiz uchun tavsiya etilgan dizayn.\\n\\n" if step==sp.STEP_DESIGN and sp.items(step)[idx][0]==recommended_design else ""
    return (f"{icon} <b>{html.escape(title)}: {html.escape(label)}</b>\\n"
            f"Namuna {idx+1} / {sp.total(step)}\\n\\n"
            f"{note}{html.escape(sp.describe(step,idx))}\\n\\n"
            "Yoqsa «✅ Tasdiqlash»ni bosing yoki ⬅️ ➡️ bilan boshqa namunalarni ko‘ring.")'''
replace_once(handler_path, old_caption, new_caption)
replace_once(handler_path, "_carousel_caption(step,idx),",
             "_carousel_caption(step,idx,ud.get('px_recommended_design')),")
replace_once(handler_path,
    "    await _show_step(context,chat_id,None,sp.STEP_COUNT,0)\n    return PX_COUNT",
    "    ud[\"px_recommended_design\"] = recommend_theme(topic)\n"
    "    design_idx = next((i for i,(key,_) in enumerate(sp.items(sp.STEP_DESIGN)) if key==ud[\"px_recommended_design\"]),0)\n"
    "    await _show_step(context,chat_id,None,sp.STEP_DESIGN,design_idx)\n"
    "    return PX_DESIGN")

test_file = ROOT / "tests/test_pptx_professional.py"
test_text = test_file.read_text(encoding="utf-8")
if "def test_topic_recommends_relevant_slide_theme" not in test_text:
    test_text += '''

def test_topic_recommends_relevant_slide_theme():
    from slide_themes import recommend_theme
    assert recommend_theme("Inson yuragi va qon aylanish tizimi") == "medical"
    assert recommend_theme("Sun'iy intellekt va robototexnika") == "technology"
    assert recommend_theme("O‘zbekistonning qadimgi tarixi") == "history"
    assert recommend_theme("Makroiqtisodiy muvozanat va investitsiya") == "finance"
    assert recommend_theme("Oddiy noma'lum mavzu") == "modern_blue"
'''
    test_file.write_text(test_text, encoding="utf-8")

print("O'zgargan fayllar: slide_themes.py, handlers/pptx_gen.py, tests/test_pptx_professional.py")
print("Endi testlarni ishga tushirib, GitHub'ga commit/push qiling.")
