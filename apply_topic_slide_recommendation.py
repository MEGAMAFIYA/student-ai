#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

def edit(path, transform):
    p = ROOT / path
    if not p.exists():
        raise SystemExit(f"Fayl topilmadi: {p}")
    old = p.read_text(encoding="utf-8")
    new = transform(old)
    if new == old:
        raise SystemExit(f"O'zgarish qo'llanmadi yoki allaqachon mavjud: {p}")
    p.write_text(new, encoding="utf-8")
    print(f"OK: {path}")

def update_themes(src):
    if "def recommend_theme(topic):" in src:
        raise SystemExit("recommend_theme() allaqachon mavjud.")
    marker = "\nPALETTES = {"
    fn = '''
def recommend_theme(topic):
    """Mavzu kalit so'zlari asosida mos slayd dizaynini tavsiya qiladi."""
    t = str(topic or "").casefold()
    groups = {
        "medical": ("tibb", "shif", "kasallik", "sog'liq", "sog‘liq", "med", "health", "doctor", "medicine", "медицин"),
        "technology": ("texnolog", "sun'iy intellekt", "sun’iy intellekt", "robot", "dastur", "kompyuter", "internet", "technology", "software", "технолог"),
        "finance": ("moliya", "iqtisod", "bank", "invest", "finance", "econom", "бизнес", "финанс", "эконом"),
        "history": ("tarix", "amir temur", "buyuk ipak", "history", "civilization", "истори"),
        "nature": ("tabiat", "ekolog", "iqlim", "o'simlik", "o‘simlik", "hayvon", "nature", "environment", "climate", "природ"),
        "science": ("ilm", "fizika", "kimyo", "biolog", "astronom", "science", "research", "наук", "физик", "хими"),
        "education": ("ta'lim", "ta’lim", "maktab", "universitet", "o'qit", "o‘qit", "education", "learning", "school", "образован"),
        "creative": ("san'at", "san’at", "dizayn", "musiqa", "adabiyot", "creative", "design", "music", "искусств"),
    }
    for theme, words in groups.items():
        if any(word in t for word in words):
            return theme
    return "modern_blue"

'''
    if marker not in src:
        raise SystemExit("slide_themes.py tuzilishi kutilganidan farq qiladi.")
    return src.replace(marker, "\n" + fn + marker, 1)

def update_handler(src):
    if "recommend_theme" in src:
        raise SystemExit("pptx_gen.py allaqachon yangilangan.")
    src = src.replace(
        "from slide_themes import THEMES, PALETTES, FRAMES, FONTS",
        "from slide_themes import THEMES, PALETTES, FRAMES, FONTS, recommend_theme", 1)
    old_map = "_NEXT_STEP={sp.STEP_COUNT:sp.STEP_DESIGN,sp.STEP_DESIGN:sp.STEP_FRAME,sp.STEP_FRAME:sp.STEP_PALETTE,\n            sp.STEP_PALETTE:sp.STEP_FONT}"
    new_map = "_NEXT_STEP={sp.STEP_DESIGN:sp.STEP_COUNT,sp.STEP_COUNT:sp.STEP_FRAME,sp.STEP_FRAME:sp.STEP_PALETTE,\n            sp.STEP_PALETTE:sp.STEP_FONT}"
    if old_map not in src:
        raise SystemExit("_NEXT_STEP bloki topilmadi.")
    src = src.replace(old_map, new_map, 1)
    old_topic = '''    ud["px_topic"]=topic
    ud.setdefault("pptx_settings",dict(sp.DEFAULT_SETTINGS))
    chat_id=update.effective_chat.id
    # Mavzu so'rovi xabarini tozalaymiz va birinchi VIZUAL namunani ko'rsatamiz.
    if ud.get("px_prompt_id"):
        await _best_effort(context.bot.delete_message(chat_id=chat_id,message_id=ud["px_prompt_id"]),"Mavzu so‘rovini o‘chirish")
    await _best_effort(update.message.reply_chat_action(ChatAction.UPLOAD_PHOTO),"chat action")
    await _show_step(context,chat_id,None,sp.STEP_COUNT,0)
    return PX_COUNT'''
    new_topic = '''    ud["px_topic"]=topic
    ud.setdefault("pptx_settings",dict(sp.DEFAULT_SETTINGS))
    recommended= recommend_theme(topic)
    ud["pptx_settings"]["design"]=recommended
    ud["px_recommended_theme"]=recommended
    chat_id=update.effective_chat.id
    # Mavzu tahlil qilinadi va mos dizayn birinchi bo'lib tavsiya etiladi.
    if ud.get("px_prompt_id"):
        await _best_effort(context.bot.delete_message(chat_id=chat_id,message_id=ud["px_prompt_id"]),"Mavzu so‘rovini o‘chirish")
    await _best_effort(update.message.reply_chat_action(ChatAction.UPLOAD_PHOTO),"chat action")
    design_idx=_DESIGN_KEYS.index(recommended)
    await _show_step(context,chat_id,None,sp.STEP_DESIGN,design_idx)
    return PX_DESIGN'''
    if old_topic not in src:
        raise SystemExit("receive_topic bloki topilmadi.")
    return src.replace(old_topic, new_topic, 1)

def update_tests(src):
    if "test_recommend_theme_matches_topic_categories" in src:
        raise SystemExit("Tavsiya testlari allaqachon mavjud.")
    return src + '''

def test_recommend_theme_matches_topic_categories():
    from slide_themes import recommend_theme
    assert recommend_theme("Yurak kasalliklari va salomatlik") == "medical"
    assert recommend_theme("Sun'iy intellekt va robototexnika") == "technology"
    assert recommend_theme("O'zbekiston tarixi") == "history"
    assert recommend_theme("Tabiat va ekologiya") == "nature"
    assert recommend_theme("Umumiy mavzu") == "modern_blue"
'''

edit("slide_themes.py", update_themes)
edit("handlers/pptx_gen.py", update_handler)
edit("tests/test_pptx_professional.py", update_tests)
print("Patch qo'llandi. Test: pytest -q tests/test_pptx_professional.py")
