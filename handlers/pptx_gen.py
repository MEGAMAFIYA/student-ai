
"""Telegram presentation wizard: topic, count, theme, frame, palette, font, export."""
import asyncio, json, logging, os, re, hashlib
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InputFile, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction
from cancel_registry import cancel_markup
from config import PPTX_AI
from ai_clients import ask_ai
from pptx_tools import build_presentation
from slide_themes import THEMES, PALETTES, FRAMES, FONTS
from slide_images import make_illustration
from slide_export import pptx_to_pdf
from handlers.menu import main_menu_keyboard
from handlers import wallet_ui
import storage
import config

logger=logging.getLogger(__name__)
(PX_TOPIC,PX_COUNT,PX_DESIGN,PX_FRAME,PX_PALETTE,PX_CUSTOM_COLOR,PX_FONT,PX_CONFIRM)=range(8)
_COUNT_OPTIONS=[5,7,10,12,15,20,25,30]
_DESIGN_KEYS=list(THEMES)
_FRAME_KEYS=list(FRAMES)
_PALETTE_KEYS=list(PALETTES)
_BUILD_SEMAPHORE=asyncio.Semaphore(max(1,int(os.getenv("SLIDE_BUILD_CONCURRENCY","2"))))

_SYSTEM="""Siz professional taqdimot muharriri va mavzu bo‘yicha mutaxassissiz.
BARCHA taqdimot matnlari qat'iy O‘ZBEK TILIDA, lotin yozuvida bo‘lsin. Mavzu inglizcha yoki boshqa tilda berilgan bo‘lsa ham, uni o‘zbekchaga tarjima qilib taqdimot yarating. Sarlavha, punktlar, jadval, diagramma yorliqlari va manba izohlari ham o‘zbekcha bo‘lsin. Inglizcha sarlavha yoki standart iboralarni ishlatmang. Mazmunli va faktlarga ehtiyotkor yondashing.
Javob FAQAT JSON obyekt bo‘lsin:
{"slides":[{"heading":"...","bullets":["..."],"layout":"split_right|split_left|cards|chart|full_image|two_col|text_focus|table","chart":{"labels":["..."],"values":[1,2,3]},"table":[["...","..."],["...","..."]]}],"references":["Haqiqiy manba nomi, muallif/tashkilot, yil, URL yoki DOI"]}
Har bir slaydda 2-4 qisqa, mazmunli punkt. Takroriy jumla ishlatmang.
Raqam/statistika faqat ishonchli bo‘lsa keltirilsin. Manbani to‘qimang:
aniq manbaga ishonchingiz bo‘lmasa references bo‘sh massiv bo‘lsin.
Diagramma uchun faqat mavzu uchun asosli va tushunarli raqamlar bering;
ishonchli raqam bo‘lmasa chart maydonini bermang. Jadval faqat foydali bo‘lsa.
Birinchi kontent slaydi mavzuga kirish, oxirgi kontent slaydi xulosa bo‘lsin.
Slaydlar soni so‘ralgan aniq miqdorda bo‘lsin (titul va yakuniy slayddan tashqari).
Matnlar slaydga sig‘adigan darajada ixcham bo‘lsin."""

def _keyboard(items,prefix,cols=2):
    rows=[]
    for i in range(0,len(items),cols):
        rows.append([InlineKeyboardButton(label,callback_data=f"{prefix}:{key}") for key,label in items[i:i+cols]])
    return InlineKeyboardMarkup(rows)

def _count_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{n} ta",callback_data=f"pptx:count:{n}") for n in _COUNT_OPTIONS[:4]],
                                 [InlineKeyboardButton(f"{n} ta",callback_data=f"pptx:count:{n}") for n in _COUNT_OPTIONS[4:]]])

def _design_keyboard():
    return _keyboard([(k,f"{i+1}. {THEMES[k][0]}") for i,k in enumerate(_DESIGN_KEYS)],"pptx:design",2)

def _design_preview_keyboard(index):
    total=len(_DESIGN_KEYS)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Orqaga",callback_data="pptx:design_prev"),
         InlineKeyboardButton(f"{index+1}/{total}",callback_data="pptx:design_count"),
         InlineKeyboardButton("Keyingisi ➡️",callback_data="pptx:design_next")],
        [InlineKeyboardButton("✅ Tasdiqlash",callback_data="pptx:design_confirm")],
    ])

async def _show_design_preview(query,context,index,edit=False):
    index %= len(_DESIGN_KEYS)
    key=_DESIGN_KEYS[index]
    context.user_data["pptx_design_index"]=index
    context.user_data["pptx_settings"]["design"]=key
    preview=await asyncio.to_thread(_preview_png,key)
    caption=(f"🎨 <b>{THEMES[key][0]}</b>\n"
             f"Dizayn namunasi {index+1}/{len(_DESIGN_KEYS)}\n\n"
             "Yoqsa «Tasdiqlash»ni bosing yoki boshqa namunalarni ko‘ring.")
    if edit:
        await query.message.edit_media(
            media=InputMediaPhoto(media=InputFile(preview,filename="design-preview.png"),
                                  caption=caption,parse_mode=ParseMode.HTML),
            reply_markup=_design_preview_keyboard(index))
    else:
        await context.bot.send_photo(query.message.chat_id,
            photo=InputFile(preview,filename="design-preview.png"),caption=caption,
            parse_mode=ParseMode.HTML,reply_markup=_design_preview_keyboard(index))

def _frame_keyboard():
    return _keyboard(list(FRAMES.items()),"pptx:frame",2)

def _palette_keyboard():
    items=[("design_default","✨ Dizaynning o‘z ranglari")]+[(k,v[0]) for k,v in PALETTES.items()]+[("custom","🎨 O‘z ranglarim (HEX)")]
    return _keyboard(items,"pptx:palette",2)

def _font_keyboard():
    return _keyboard([(f"f{i}",name) for i,name in enumerate(FONTS)],"pptx:font",2)

def _preview_png(key):
    name,bg,accent,fg,_=THEMES[key]
    im=Image.new("RGB",(1000,563),bg); d=ImageDraw.Draw(im)
    try: font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",40)
    except Exception: font=ImageFont.load_default()
    try: body=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",22)
    except Exception: body=ImageFont.load_default()
    d.rounded_rectangle((32,32,968,531),radius=24,outline=accent,width=7)
    if key in ("technology","dark_premium","luxury","premium_gold","gradient"):
        d.rectangle((32,32,270,531),fill=accent)
        d.ellipse((720,65,900,245),outline=accent,width=14)
    elif key in ("newspaper","academic","history"):
        d.rectangle((75,90,925,100),fill=accent)
        d.rectangle((75,445,925,450),fill=accent)
    else:
        d.rounded_rectangle((70,75,930,480),radius=28,fill="#FFFFFF")
        d.rectangle((70,75,88,480),fill=accent)
    d.text((115,150),name,font=font,fill=fg)
    d.text((118,230),"Professional presentation",font=body,fill=accent)
    d.rounded_rectangle((118,300,480,370),radius=14,fill=accent)
    d.text((145,318),"STUDENT AI",font=body,fill="#FFFFFF")
    b=BytesIO(); im.save(b,"PNG"); b.seek(0); return b

async def entry(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    context.user_data.clear()
    context.user_data["flow"]="pptx"
    context.user_data["pptx_settings"]={"design":"modern_blue","frame":"thin","palette":"design_default","font":"Arial","pdf":False}
    await q.edit_message_text("📊 PROFESSIONAL TAQDIMOT\n\nTaqdimot mavzusini kiriting:",reply_markup=cancel_markup())
    return PX_TOPIC

async def receive_topic(update:Update,context:ContextTypes.DEFAULT_TYPE):
    topic=(update.message.text or "").strip()
    if not topic or len(topic)>300:
        await update.message.reply_text("Mavzuni kiriting (1–300 belgi)."); return PX_TOPIC
    context.user_data["px_topic"]=topic
    await update.message.reply_text(f"✅ Mavzu: {topic}\n\nSlaydlarning umumiy sonini tanlang (titul va yakuniy slayd ham hisoblanadi):",reply_markup=_count_keyboard())
    return PX_COUNT

async def receive_count(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    try: count=int(q.data.rsplit(":",1)[1])
    except Exception: return PX_COUNT
    if count not in _COUNT_OPTIONS: return PX_COUNT
    context.user_data["px_count"]=count
    await q.edit_message_text("🎨 20 ta professional dizayndan birini tanlang:",reply_markup=_design_keyboard())
    return PX_DESIGN

async def choose_design(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    action=q.data
    if action.startswith("pptx:design:"):
        key=action.split(":",2)[-1]
        if key not in THEMES: return PX_DESIGN
        await _show_design_preview(q,context,_DESIGN_KEYS.index(key))
        return PX_DESIGN
    if action=="pptx:design_prev":
        index=int(context.user_data.get("pptx_design_index",0))-1
        await _show_design_preview(q,context,index,edit=True)
        return PX_DESIGN
    if action=="pptx:design_next":
        index=int(context.user_data.get("pptx_design_index",0))+1
        await _show_design_preview(q,context,index,edit=True)
        return PX_DESIGN
    if action=="pptx:design_count":
        return PX_DESIGN
    if action=="pptx:design_confirm":
        label=THEMES[context.user_data['pptx_settings']['design']][0]
        chat_id=q.message.chat_id
        await q.message.delete()
        await context.bot.send_message(chat_id,
            f"✅ <b>{label}</b> dizayni tasdiqlandi.\n\n🖼 Endi ramka va bezak turini tanlang:",
            parse_mode=ParseMode.HTML,reply_markup=_frame_keyboard())
        return PX_FRAME
    return PX_DESIGN

async def choose_frame(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    key=q.data.split(":",2)[-1]
    if key not in FRAMES: return PX_FRAME
    context.user_data["pptx_settings"]["frame"]=key
    await q.edit_message_text("🌈 Ranglar to‘plamini tanlang:",reply_markup=_palette_keyboard())
    return PX_PALETTE

async def choose_palette(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    key=q.data.split(":",2)[-1]
    if key=="custom":
        await q.edit_message_text("HEX ranglarini quyidagi tartibda yuboring:\n\nFon  Urg‘u  Matn\n\nMasalan: #102A43 #38BDF8 #FFFFFF")
        return PX_CUSTOM_COLOR
    if key!="design_default" and key not in PALETTES: return PX_PALETTE
    context.user_data["pptx_settings"]["palette"]=key
    await q.edit_message_text("🔤 Shriftni tanlang:",reply_markup=_font_keyboard())
    return PX_FONT

async def receive_custom_color(update:Update,context:ContextTypes.DEFAULT_TYPE):
    vals=re.findall(r"#[0-9a-fA-F]{6}",update.message.text or "")
    if len(vals)!=3:
        await update.message.reply_text("Uchta HEX rang kiriting. Masalan: #102A43 #38BDF8 #FFFFFF")
        return PX_CUSTOM_COLOR
    s=context.user_data["pptx_settings"]
    s.update({"custom_bg":vals[0],"custom_accent":vals[1],"custom_text":vals[2],"palette":"custom"})
    await update.message.reply_text("🔤 Shriftni tanlang:",reply_markup=_font_keyboard())
    return PX_FONT

async def choose_font(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    key=q.data.split(":",2)[-1]
    try: idx=int(key[1:]); font=FONTS[idx]
    except Exception: return PX_FONT
    context.user_data["pptx_settings"]["font"]=font
    return await show_confirmation(update,context)

async def show_confirmation(update,context):
    s=context.user_data["pptx_settings"]; topic=context.user_data["px_topic"]; count=context.user_data["px_count"]
    label=THEMES[s["design"]][0]; frame=FRAMES[s["frame"]]
    palette=PALETTES.get(s.get("palette"),("Dizayn ranglari",))[0]
    text=(f"📋 **Taqdimot sozlamalari**\n\nMavzu: {topic}\nSlaydlar: {count} ta\n"
          f"Dizayn: {label}\nRamka: {frame}\nRang: {palette}\nShrift: {s['font']}\n\n"
          "PDF nusxasi ham kerakmi?")
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("📄 PPTX + PDF",callback_data="pptx:pdf:yes"),
                              InlineKeyboardButton("📊 Faqat PPTX",callback_data="pptx:pdf:no")],
                             [InlineKeyboardButton("❌ Bekor qilish",callback_data="cancel:all")]])
    if update.callback_query:
        await update.callback_query.edit_message_text(text,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
    else: await update.message.reply_text(text,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)
    return PX_CONFIRM

async def choose_export(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    s=context.user_data["pptx_settings"]; s["pdf"]=q.data.endswith(":yes")
    topic=context.user_data.get("px_topic",""); count=int(context.user_data.get("px_count",5))
    chat_id=update.effective_chat.id; user=update.effective_user
    await q.edit_message_text("⏳ AI mavzuni tahlil qilmoqda va slaydlar mazmunini tayyorlamoqda…")
    try:
        slides=await _generate_slides(topic,count-2)
        if not slides: raise RuntimeError("AI slayd matnlarini yaratolmadi")
        images={}
        for i,item in enumerate(slides):
            images[i]=await asyncio.to_thread(make_illustration,topic,item.get("heading",""),THEMES[s["design"]][2],i)
        # Optional image API use is explicitly opt-in; offline illustrations always remain available.
        if os.getenv("SLIDE_IMAGES_ENABLED","0").lower() in ("1","true","yes"):
            await _try_optional_generated_images(topic,slides,images)
        await q.edit_message_text(f"🛠 {count} slaydli PowerPoint qurilmoqda…")
        async with _BUILD_SEMAPHORE:
            pptx=await asyncio.to_thread(build_presentation,topic,slides,"Student AI",s,images)
        filename=_safe_filename(topic)+".pptx"
        await context.bot.send_document(chat_id,document=InputFile(pptx,filename=filename),
            caption=f"📊 {topic}\n✅ {count} ta slayd · {THEMES[s['design']][0]}",reply_markup=main_menu_keyboard())
        if s.get("pdf"):
            try:
                async with _BUILD_SEMAPHORE:
                    pdf=await asyncio.to_thread(pptx_to_pdf,pptx,topic,slides,s,images)
                await context.bot.send_document(chat_id,document=InputFile(pdf,filename=_safe_filename(topic)+".pdf"),
                    caption=f"📄 {topic} — PDF nusxasi")
            except Exception as e:
                logger.exception("PDF export failed")
                await context.bot.send_message(chat_id,"⚠️ PPTX tayyor, ammo PDF eksportida xatolik yuz berdi.")
        if user:
            try:
                storage.record_file(user.id,"pptx",topic,sent_pptx.document.file_id if sent_pptx.document else "")
                storage.record_usage("pptx",user.id)
            except Exception: logger.exception("Could not record PPTX usage")
        await wallet_ui.finalize_success(context,update=update,chat_id=chat_id)
        try: await q.delete_message()
        except Exception: pass
    except Exception as e:
        logger.exception("PPTX generation failed for user=%s",getattr(user,"id",None))
        try: await context.bot.send_message(chat_id,"❌ Taqdimot yaratishda xatolik yuz berdi. Birozdan so‘ng qayta urinib ko‘ring.")
        except Exception: pass
        await wallet_ui.finalize_failure(context,update=update,chat_id=chat_id,reason="pptx_generation_failed")
    finally:
        context.user_data.clear()
    return ConversationHandler.END

def _safe_filename(topic):
    name=re.sub(r"[^\w\- ]+","",topic,flags=re.UNICODE).strip().replace(" ","_")[:48]
    return name or "student_ai_presentation"

async def _generate_slides(topic,count):
    prompt=(f"Mavzu: {topic}\nKontent slaydlari: aynan {count} ta.\n"
            "Taqdimot uchun ilmiy, mantiqiy va takrorlanmaydigan kontent yarating. "
            "MUHIM: JSON ichidagi barcha ko‘rinadigan matnlar faqat o‘zbek tilida (lotin yozuvida) bo‘lsin; inglizcha matn yozmang. "
            "Har bir heading, bullets, jadval kataklari va diagramma labels o‘zbekcha bo‘lsin. "
            "Kerak bo‘lsa chart/table qo‘shing. references ro‘yxatida faqat tekshirish mumkin bo‘lgan manbalar bo‘lsin.")
    for attempt in range(2):
        raw=await ask_ai(PPTX_AI,prompt,_SYSTEM)
        if not raw: continue
        try:
            cleaned=re.sub(r"^```(?:json)?\s*|```\s*$","",raw.strip(),flags=re.I|re.M)
            data=json.loads(cleaned)
            slides=data.get("slides") if isinstance(data,dict) else data
            if not isinstance(slides,list): continue
            result=[]
            for item in slides[:count]:
                if not isinstance(item,dict) or not item.get("heading"): continue
                bullets=item.get("bullets") or []
                if isinstance(bullets,str): bullets=[bullets]
                result.append({"heading":str(item["heading"])[:140],"bullets":[str(x)[:260] for x in bullets[:6]],
                               "layout":item.get("layout"),"chart":item.get("chart"),"table":item.get("table")})
            if len(result)==count:
                refs=data.get("references",[]) if isinstance(data,dict) else []
                if refs: result[-1]["references"]=[str(x)[:240] for x in refs[:4]]
                return result
        except Exception as e: logger.warning("PPTX AI JSON parse attempt %s failed: %s",attempt+1,e)
    return None

async def _try_optional_generated_images(topic,slides,images):
    """Paid provider integration is opt-in. Failed calls retain offline visuals."""
    try:
        import image_clients
        providers=[p for p in ("bfl","stability") if config.KEY_POOLS.get(p)]
        if not providers: return
        provider=os.getenv("SLIDE_IMAGES_PROVIDER","auto").lower()
        if provider not in providers: provider=providers[0]
        entry=next((x for x in config.KEY_POOLS.get(provider,[]) if x.get("key")),None)
        if not entry: return
        model=entry.get("model") or image_clients.DEFAULT_MODELS[provider]
        for i,item in enumerate(slides[:min(3,len(slides))]):
            result=await image_clients.generate_image(provider,entry["key"],model,
                f"Professional educational editorial illustration, no text, topic: {topic}. Slide: {item.get('heading','')}.",
                aspect="16:9")
            if result.ok:
                b=BytesIO(result.data); images[i]=b
    except Exception: logger.exception("Optional slide image generation failed; offline illustrations retained")
