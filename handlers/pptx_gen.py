"""Telegram taqdimot ustasi (wizard): mavzu → tavsiya etilgan dizayn → slaydlar soni → ramka → ranglar → shrift → eksport."""
import asyncio, html, json, logging, os, re
from io import BytesIO
from telegram import Update, InputFile, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode, ChatAction
from cancel_registry import cancel_markup
from config import PPTX_AI
from ai_clients import ask_ai
from pptx_tools import build_presentation
from slide_themes import THEMES, PALETTES, FRAMES, FONTS, recommend_theme
from slide_images import make_illustration
from slide_export import pptx_to_pdf
from slide_quality import normalize_slide, validate_deck, clean_references, choose_layout
from handlers.menu import main_menu_keyboard
from handlers import wallet_ui
import slide_previews as sp
import storage
import config

logger=logging.getLogger(__name__)
(PX_TOPIC,PX_COUNT,PX_DESIGN,PX_FRAME,PX_PALETTE,PX_CUSTOM_COLOR,PX_FONT,PX_CONFIRM)=range(8)
_COUNT_OPTIONS=sp.COUNT_OPTIONS
_DESIGN_KEYS=list(THEMES)
_FRAME_KEYS=list(FRAMES)
_PALETTE_KEYS=list(PALETTES)
_BUILD_SEMAPHORE=asyncio.Semaphore(max(1,int(os.getenv("SLIDE_BUILD_CONCURRENCY","2"))))
_STEP_STATE={sp.STEP_COUNT:PX_COUNT,sp.STEP_DESIGN:PX_DESIGN,sp.STEP_FRAME:PX_FRAME,
             sp.STEP_PALETTE:PX_PALETTE,sp.STEP_FONT:PX_FONT}
_NEXT_STEP={sp.STEP_DESIGN:sp.STEP_COUNT,sp.STEP_COUNT:sp.STEP_FRAME,sp.STEP_FRAME:sp.STEP_PALETTE,
            sp.STEP_PALETTE:sp.STEP_FONT}
_USER_LOCKS={}
_BG_TASKS=set()
_STALE_TEXT="⌛ Bu tugma eskirgan. Taqdimotni menyudan qaytadan boshlang."
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

def _user_lock(user_id):
    lock=_USER_LOCKS.get(user_id)
    if lock is None:
        if len(_USER_LOCKS)>2000:
            for k in [k for k,v in _USER_LOCKS.items() if not v.locked()][:1000]: _USER_LOCKS.pop(k,None)
        lock=_USER_LOCKS[user_id]=asyncio.Lock()
    return lock

async def _safe_answer(q,text=None,alert=False):
    try: await q.answer(text=text,show_alert=alert)
    except Exception as e: logger.info("callback answer() bajarilmadi: %s: %s",type(e).__name__,e)

async def _best_effort(coro,what=""):
    try: return await coro
    except Exception as e:
        logger.info("%s bajarilmadi: %s: %s",what or "Amal",type(e).__name__,e)
        return None

def _retry_seconds(exc):
    ra=getattr(exc,"retry_after",1)
    try: return float(ra.total_seconds() if hasattr(ra,"total_seconds") else ra)
    except Exception: return 1.0

def _short(text,n=150):
    text=str(text or "")
    return text if len(text)<=n else text[:n-1]+"…"

def _palette_label(s):
    if s.get("palette")=="custom":
        return f"O‘z ranglarim ({s.get('custom_bg','')} {s.get('custom_accent','')} {s.get('custom_text','')})"
    if s.get("palette") in PALETTES: return PALETTES[s["palette"]][0]
    return "Dizaynning o‘z ranglari"

def _prefetch(step,idx,settings):
    n=sp.total(step); snap=dict(settings)
    for j in {(idx+1)%n,(idx-1)%n}:
        try: t=asyncio.create_task(asyncio.to_thread(sp.get_preview,step,j,snap))
        except RuntimeError: return
        _BG_TASKS.add(t); t.add_done_callback(_BG_TASKS.discard)

def _carousel_keyboard(step,idx):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Orqaga",callback_data=sp.make_cb(step,"p",idx)),
         InlineKeyboardButton("Keyingisi ➡️",callback_data=sp.make_cb(step,"n",idx))],
        [InlineKeyboardButton("✅ TASDIQLASH",callback_data=sp.make_cb(step,"ok",idx))],
        [InlineKeyboardButton("❌ Bekor qilish",callback_data="cancel:all")],
    ])

def _carousel_caption(step,idx,recommended=False):
    icon,title=sp.STEP_TITLES[step]
    _,label=sp.items(step)[idx]
    marker="✨ <b>Mavzuga mos tavsiya etilgan dizayn</b>\\n" if recommended else ""
    return (f"{marker}{icon} <b>{html.escape(title)}: {html.escape(label)}</b>\\n"
            f"Namuna {idx+1} / {sp.total(step)}\\n\\n"
            f"{html.escape(sp.describe(step,idx))}\\n\\n"
            "Yoqsa «✅ Tasdiqlash»ni bosing yoki ⬅️ ➡️ bilan boshqa namunalarni ko‘ring.")

def _confirm_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📄 PPTX + PDF",callback_data="pptx:pdf:yes"),
                                  InlineKeyboardButton("📊 Faqat PPTX",callback_data="pptx:pdf:no")],
                                 [InlineKeyboardButton("❌ Bekor qilish",callback_data="cancel:all")]])

def _summary_caption(ud):
    s=ud["pptx_settings"]
    return ("📋 <b>Taqdimot sozlamalari</b>\\n\\n"
            f"Mavzu: {html.escape(_short(ud.get('px_topic')))}\\n"
            f"Slaydlar: {ud.get('px_count')} ta\\n"
            f"Dizayn: {html.escape(THEMES[s['design']][0])}\\n"
            f"Ramka: {html.escape(FRAMES[s['frame']])}\\n"
            f"Ranglar: {html.escape(_palette_label(s))}\\n"
            f"Shrift: {html.escape(s['font'])}\\n\\nPDF nusxasi ham kerakmi?")

async def _show_photo(context,chat_id,message_id,data,caption,kb,filename):
    if message_id:
        for attempt in (1,2):
            try:
                await context.bot.edit_message_media(chat_id=chat_id,message_id=message_id,
                    media=InputMediaPhoto(media=data,caption=caption,parse_mode=ParseMode.HTML),reply_markup=kb)
                return message_id
            except BadRequest as e:
                if "message is not modified" in str(e).lower(): return message_id
                logger.error("edit_media BadRequest: %s",e); break
            except RetryAfter as e:
                wait=_retry_seconds(e)
                if attempt==1 and wait<=3: await asyncio.sleep(wait+0.2); continue
                break
            except TelegramError as e:
                logger.error("edit_media Telegram xatosi: %s: %s",type(e).__name__,e); break
            except Exception:
                logger.exception("edit_media kutilmagan xatosi"); break
        await _best_effort(context.bot.delete_message(chat_id=chat_id,message_id=message_id),"Eski namunani o‘chirish")
    sent=await context.bot.send_photo(chat_id=chat_id,photo=InputFile(data,filename=filename),
        caption=caption,parse_mode=ParseMode.HTML,reply_markup=kb)
    return sent.message_id

async def _show_step(context,chat_id,message_id,step,idx):
    ud=context.user_data; settings=ud["pptx_settings"]
    data=await asyncio.to_thread(sp.get_preview,step,idx,settings)
    recommended=(step==sp.STEP_DESIGN and ud.get("px_recommended_theme")==_DESIGN_KEYS[idx])
    mid=await _show_photo(context,chat_id,message_id,data,_carousel_caption(step,idx,recommended),
                          _carousel_keyboard(step,idx),f"{step}-{idx}.jpg")
    ud["px_msg_id"]=mid; ud["px_step"]=step
    ud.setdefault("px_pos",{})[step]=idx
    _prefetch(step,idx,settings)
    return mid

async def _show_final(context,chat_id,message_id):
    ud=context.user_data
    data=await asyncio.to_thread(sp.get_preview,sp.STEP_FONT,0,ud["pptx_settings"],final=True)
    mid=await _show_photo(context,chat_id,message_id,data,_summary_caption(ud),_confirm_keyboard(),"final.jpg")
    ud["px_msg_id"]=mid; ud["px_step"]="confirm"
    return PX_CONFIRM

def _wizard_alive(ud): return "pptx_settings" in ud and "px_topic" in ud
def _is_wizard_message(q,ud):
    mid=ud.get("px_msg_id")
    return q.message is not None and mid is not None and q.message.message_id==mid
async def _drop_keyboard(q):
    await _best_effort(q.edit_message_reply_markup(reply_markup=None),"Eskirgan tugmalarni olib tashlash")

async def entry(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await _safe_answer(q)
    context.user_data.clear(); context.user_data["flow"]="pptx"
    context.user_data["pptx_settings"]=dict(sp.DEFAULT_SETTINGS)
    if q.message is not None: context.user_data["px_prompt_id"]=q.message.message_id
    await q.edit_message_text("📊 PROFESSIONAL TAQDIMOT\\n\\nTaqdimot mavzusini kiriting:",reply_markup=cancel_markup())
    return PX_TOPIC

async def receive_topic(update:Update,context:ContextTypes.DEFAULT_TYPE):
    topic=(update.message.text or "").strip()
    if not topic or len(topic)>300:
        await update.message.reply_text("Mavzuni kiriting (1–300 belgi)."); return PX_TOPIC
    ud=context.user_data; ud["px_topic"]=topic
    ud.setdefault("pptx_settings",dict(sp.DEFAULT_SETTINGS))
    recommended=recommend_theme(topic)
    ud["pptx_settings"]["design"]=recommended
    ud["px_recommended_theme"]=recommended
    chat_id=update.effective_chat.id
    if ud.get("px_prompt_id"):
        await _best_effort(context.bot.delete_message(chat_id=chat_id,message_id=ud["px_prompt_id"]),"Mavzu so‘rovini o‘chirish")
    await _best_effort(update.message.reply_chat_action(ChatAction.UPLOAD_PHOTO),"chat action")
    design_idx=_DESIGN_KEYS.index(recommended)
    await _show_step(context,chat_id,None,sp.STEP_DESIGN,design_idx)
    return PX_DESIGN

async def _confirm_step(update,context,step,idx):
    q=update.callback_query; ud=context.user_data; s=ud["pptx_settings"]
    shown=ud.get("px_pos",{}).get(step)
    if shown is not None and shown!=idx:
        logger.info("Eskirgan tasdiqlash rad etildi: step=%s idx=%s shown=%s",step,idx,shown)
        return _STEP_STATE[step]
    key,_=sp.items(step)[idx]; chat_id=q.message.chat_id; mid=q.message.message_id
    if step==sp.STEP_COUNT: ud["px_count"]=int(key)
    elif step==sp.STEP_DESIGN: s["design"]=key
    elif step==sp.STEP_FRAME: s["frame"]=key
    elif step==sp.STEP_PALETTE:
        if key=="custom": return await _ask_custom_colors(context,chat_id,mid)
        s["palette"]=key
        for k in ("custom_bg","custom_accent","custom_text"): s.pop(k,None)
    elif step==sp.STEP_FONT:
        s["font"]=FONTS[int(key[1:])]
        return await _show_final(context,chat_id,mid)
    nxt=_NEXT_STEP[step]
    ud.setdefault("px_pos",{}).pop(nxt,None)
    await _show_step(context,chat_id,mid,nxt,0)
    return _STEP_STATE[nxt]

async def _ask_custom_colors(context,chat_id,message_id):
    text=("🎨 <b>O‘z ranglarim (HEX)</b>\\n\\nFon, urg‘u va matn ranglarini shu tartibda HEX kodda yuboring:\\n\\n"
          "<code>#102A43 #38BDF8 #FFFFFF</code>")
    ok=await _best_effort(context.bot.edit_message_caption(chat_id=chat_id,message_id=message_id,caption=text,
        parse_mode=ParseMode.HTML,reply_markup=cancel_markup()),"HEX ko‘rsatmasi")
    if ok is None:
        await _best_effort(context.bot.delete_message(chat_id=chat_id,message_id=message_id),"Namunani o‘chirish")
        sent=await context.bot.send_message(chat_id,text,parse_mode=ParseMode.HTML,reply_markup=cancel_markup())
        context.user_data["px_msg_id"]=sent.message_id
    context.user_data["px_step"]="custom"
    return PX_CUSTOM_COLOR

async def _carousel_cb(update,context,step):
    q=update.callback_query; ud=context.user_data; state=_STEP_STATE[step]
    parsed=sp.parse_cb(q.data)
    if not _wizard_alive(ud):
        await _safe_answer(q,_STALE_TEXT,True); await _drop_keyboard(q); return ConversationHandler.END
    if parsed is None or parsed[0]!=step or not _is_wizard_message(q,ud):
        await _safe_answer(q,_STALE_TEXT,True); await _drop_keyboard(q); return state
    await _safe_answer(q); _,act,idx=parsed
    user=update.effective_user; lock=_user_lock(user.id if user else 0)
    if lock.locked(): return state
    async with lock:
        try:
            if act in ("p","n"):
                await _show_step(context,q.message.chat_id,q.message.message_id,step,sp.nav_index(idx,act,sp.total(step)))
                return state
            return await _confirm_step(update,context,step,idx)
        except asyncio.CancelledError: raise
        except Exception:
            logger.exception("Namuna tanlash xatosi: step=%s act=%s idx=%s",step,act,idx)
            await _best_effort(context.bot.send_message(q.message.chat_id,"⚠️ Namunani ko‘rsatishda xatolik yuz berdi."),"Xato xabari")
            return state

async def receive_count(update,context): return await _carousel_cb(update,context,sp.STEP_COUNT)
async def choose_design(update,context): return await _carousel_cb(update,context,sp.STEP_DESIGN)
async def choose_frame(update,context): return await _carousel_cb(update,context,sp.STEP_FRAME)
async def choose_palette(update,context): return await _carousel_cb(update,context,sp.STEP_PALETTE)
async def choose_font(update,context): return await _carousel_cb(update,context,sp.STEP_FONT)

_HEX_RE=re.compile(r"(?<![0-9A-Za-z])#?([0-9a-fA-F]{6})(?![0-9A-Za-z])")
async def receive_custom_color(update:Update,context:ContextTypes.DEFAULT_TYPE):
    vals=["#"+v.upper() for v in _HEX_RE.findall(update.message.text or "")]
    ud=context.user_data; chat_id=update.effective_chat.id
    if len(vals)!=3:
        old=ud.pop("px_err_id",None)
        if old: await _best_effort(context.bot.delete_message(chat_id=chat_id,message_id=old),"Eski xato xabari")
        sent=await update.message.reply_text("Uchta HEX rang kiriting. Masalan: #102A43 #38BDF8 #FFFFFF")
        ud["px_err_id"]=sent.message_id; return PX_CUSTOM_COLOR
    s=ud["pptx_settings"]; s.update({"custom_bg":vals[0],"custom_accent":vals[1],"custom_text":vals[2],"palette":"custom"})
    await _best_effort(update.message.delete(),"HEX xabarini o‘chirish")
    if ud.get("px_err_id"):
        await _best_effort(context.bot.delete_message(chat_id=chat_id,message_id=ud.pop("px_err_id")),"Xato xabarini o‘chirish")
    ud.setdefault("px_pos",{}).pop(sp.STEP_FONT,None)
    await _show_step(context,chat_id,ud.get("px_msg_id"),sp.STEP_FONT,0)
    return PX_FONT

async def show_confirmation(update,context):
    q=update.callback_query
    chat_id=q.message.chat_id if q and q.message else update.effective_chat.id
    mid=q.message.message_id if q and q.message else context.user_data.get("px_msg_id")
    return await _show_final(context,chat_id,mid)

async def choose_export(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; ud=context.user_data
    if not _wizard_alive(ud):
        await _safe_answer(q,_STALE_TEXT,True); await _drop_keyboard(q); return ConversationHandler.END
    if not _is_wizard_message(q,ud):
        await _safe_answer(q,_STALE_TEXT,True); await _drop_keyboard(q); return PX_CONFIRM
    await _safe_answer(q)
    if ud.get("px_building"): return PX_CONFIRM
    ud["px_building"]=True; s=ud["pptx_settings"]; s["pdf"]=q.data.endswith(":yes")
    topic=ud.get("px_topic",""); count=int(ud.get("px_count",5))
    chat_id=update.effective_chat.id; user=update.effective_user; status=None
    await _best_effort(q.message.delete(),"Yakuniy namunani o‘chirish")
    try: status=await context.bot.send_message(chat_id,"⏳ AI mavzuni tahlil qilmoqda va slaydlar mazmunini tayyorlamoqda…",reply_markup=cancel_markup())
    except Exception: logger.exception("Holat xabarini yuborib bo‘lmadi")
    async def _status(text):
        if status is not None: await _best_effort(status.edit_text(text,reply_markup=cancel_markup()),"Holat xabarini yangilash")
    try:
        slides=await _generate_slides(topic,count-2)
        if not slides: raise RuntimeError("AI slayd matnlarini yaratolmadi")
        images={}
        for i,item in enumerate(slides):
            images[i]=await asyncio.to_thread(make_illustration,topic,item.get("heading",""),THEMES[s["design"]][2],i)
        if os.getenv("SLIDE_IMAGES_ENABLED","0").lower() in ("1","true","yes"):
            await _try_optional_generated_images(topic,slides,images)
        await _status(f"🛠 {count} slaydli PowerPoint qurilmoqda…")
        async with _BUILD_SEMAPHORE:
            pptx=await asyncio.to_thread(build_presentation,topic,slides,"Student AI",s,images)
        filename=_safe_filename(topic)+".pptx"
        sent_pptx=await context.bot.send_document(chat_id,document=InputFile(pptx,filename=filename),
            caption=f"📊 {topic}\\n✅ {count} ta slayd · {THEMES[s['design']][0]}")
        if s.get("pdf"):
            try:
                async with _BUILD_SEMAPHORE:
                    pdf=await asyncio.to_thread(pptx_to_pdf,pptx,topic,slides,s,images)
                await context.bot.send_document(chat_id,document=InputFile(pdf,filename=_safe_filename(topic)+".pdf"),
                    caption=f"📄 {topic} — PDF nusxasi")
            except Exception:
                logger.exception("PDF export failed")
                await context.bot.send_message(chat_id,"⚠️ PPTX tayyor, ammo PDF eksportida xatolik yuz berdi.")
        if user:
            try:
                storage.record_file(user.id,"pptx",topic,sent_pptx.document.file_id if getattr(sent_pptx,"document",None) else "")
                storage.record_usage("pptx",user.id)
            except Exception: logger.exception("Could not record PPTX usage")
        await wallet_ui.finalize_success(context,update=update,chat_id=chat_id)
        await context.bot.send_message(chat_id,"🏠 Bosh menyu:",reply_markup=main_menu_keyboard())
    except Exception:
        logger.exception("PPTX generation failed for user=%s",getattr(user,"id",None))
        try: await context.bot.send_message(chat_id,"❌ Taqdimot yaratishda xatolik yuz berdi. Birozdan so‘ng qayta urinib ko‘ring.")
        except Exception: pass
        await wallet_ui.finalize_failure(context,update=update,chat_id=chat_id,reason="pptx_generation_failed")
    finally:
        if status is not None: await _best_effort(status.delete(),"Holat xabarini o‘chirish")
        context.user_data.clear()
    return ConversationHandler.END

async def cancel_cb(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; chat_id=q.message.chat_id if q.message else update.effective_chat.id
    try:
        from handlers import menu as _menu
        _menu._cancel_everything(update,context)
    except Exception:
        logger.exception("Bekor qilishda tozalash xatosi"); context.user_data.clear()
    await _safe_answer(q,"❌ Bekor qilindi")
    if q.message is not None: await _best_effort(q.message.delete(),"Namuna xabarini o‘chirish")
    await _best_effort(context.bot.send_message(chat_id,"❌ Bekor qilindi.",reply_markup=main_menu_keyboard()),"Bekor qilish xabari")
    return ConversationHandler.END

async def stale_cb(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await _safe_answer(q,_STALE_TEXT,True); await _drop_keyboard(q)
    return None

def _safe_filename(topic):
    name=re.sub(r"[^\\w\\- ]+","",topic,flags=re.UNICODE).strip().replace(" ","_")[:48]
    return name or "student_ai_presentation"

async def _generate_slides(topic,count):
    prompt=(f"Mavzu: {topic}\\nKontent slaydlari: aynan {count} ta.\\n"
            "Ilmiy, mantiqiy va takrorlanmaydigan kontent yarating. Barcha ko‘rinadigan matnlar O‘zbek tilida, lotin yozuvida bo‘lsin. "
            "JSON maydonlari: heading, bullets, layout, chart, table. "
            "layout tavsiya sifatida title|text_focus|two_col|cards|full_image|chart|table|timeline|comparison|process|statistics|infographic bo‘lishi mumkin. "
            "Diagramma uchun faqat tekshiriladigan yoki nazariy modelga tegishli aniq qiymatlar bering; ishonchli raqam bo‘lmasa chart bermang. "
            "references faqat real manbalar: muallif/tashkilot, asar, yil va URL/DOI mavjud bo‘lsa yozilsin; ishonchingiz bo‘lmasa bo‘sh massiv. "
            "Titul va yakuniy slaydni JSONga kiritmang.")
    for attempt in range(2):
        raw=await ask_ai(PPTX_AI,prompt,_SYSTEM)
        if not raw: continue
        try:
            cleaned=re.sub(r"^```(?:json)?\\s*|```\\s*$","",raw.strip(),flags=re.I|re.M)
            data=json.loads(cleaned)
            if not isinstance(data,dict) or not isinstance(data.get("slides"),list): continue
            raw_slides=data["slides"][:count]
            result=[normalize_slide(x,i) for i,x in enumerate(raw_slides) if isinstance(x,dict) and x.get("heading")]
            if len(result)!=count:
                logger.warning("PPTX AI kontent soni noto‘g‘ri: %s/%s",len(result),count); continue
            prev=None
            for i,item in enumerate(result):
                item["layout"]=choose_layout(item,prev); prev=item["layout"]
            refs=clean_references(data.get("references",[]))
            if refs: result[-1]["references"]=refs
            ok,errors=validate_deck(result,count)
            if not ok:
                logger.warning("PPTX kontent validatsiyasi: %s","; ".join(errors[:8])); continue
            return result
        except Exception as e: logger.warning("PPTX AI JSON parse/validation attempt %s failed: %s",attempt+1,e)
    return None

async def _try_optional_generated_images(topic,slides,images):
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
                f"Professional educational editorial illustration, no text, topic: {topic}. Slide: {item.get('heading','')}.",aspect="16:9")
            if result.ok: images[i]=BytesIO(result.data)
    except Exception: logger.exception("Optional slide image generation failed; offline illustrations retained")
