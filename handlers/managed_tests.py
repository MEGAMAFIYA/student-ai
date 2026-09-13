import json, random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import managed_tests as db

def inline_results():
 n=len(db.get_questions())
 return [InlineQueryResultArticle('managed-tests-' + str(n),'📝 Faol testlar',InputTextMessageContent(f'📝 {n} ta test faol\nTestni boshlash uchun pastdagi tugmani bosing.',parse_mode='HTML'),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('▶️ Testlarni boshlash',callback_data='mt:start')]]))]
async def on_inline(update,context):
 q=update.inline_query.query.strip().lower()
 if q in ('test','tests','testlar'):
  await update.inline_query.answer(inline_results(),cache_time=0,is_personal=True)

def kb(opts): return InlineKeyboardMarkup([[InlineKeyboardButton(f'{chr(65+i)}) {x}',callback_data=f'mt:ans:{i}') ] for i,x in enumerate(opts)])
async def callback(update,context):
 q=update.callback_query; await q.answer(); data=q.data
 if data=='mt:start':
  qs=db.get_questions(); random.shuffle(qs); context.user_data['mt_qs']=qs; context.user_data['mt_i']=0; context.user_data['mt_ok']=0; context.user_data['mt_bad']=0; context.user_data.pop('mt_answered', None)
  return await send(update,context)
 if data.startswith('mt:ans:'):
  if context.user_data.get('mt_answered'): return
  i=int(data.split(':')[-1]); qs=context.user_data.get('mt_qs',[]); pos=context.user_data.get('mt_i',0)
  if not qs:return
  context.user_data['mt_answered']=True; good=i==qs[pos][4]; context.user_data['mt_ok']+=good; context.user_data['mt_bad']+=not good
  next_label = '📊 Natija' if pos + 1 >= len(qs) else '➡️ Keyingisi'
  next_data = 'mt:result' if pos + 1 >= len(qs) else 'mt:next'
  await q.edit_message_text(('🥰 To‘g‘ri javob!' if good else '😡 Xato javob!')+f'\n\n{qs[pos][2]}',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(next_label,callback_data=next_data)]]))
 if data=='mt:next':
  context.user_data['mt_i']+=1; context.user_data.pop('mt_answered',None); return await send(update,context)
 if data=='mt:result':
  n=len(context.user_data.get('mt_qs',[])); ok=context.user_data.get('mt_ok',0); p=ok*100/n if n else 0
  s='yomon🙁' if p<=50 else 'o‘rta😐' if p<=85 else 'yaxshi🙂' if p<100 else 'alo🥰'
  await q.edit_message_text(f'Testlar: {n} ta\nTo‘g‘ri javoblar: {ok} ta\nXato javoblar: {n-ok} ta\nHolati: {s}')
async def send(update,context):
 qs=context.user_data.get('mt_qs',[]); i=context.user_data.get('mt_i',0)
 if i>=len(qs): return await update.callback_query.edit_message_text('Testlar tugadi.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📊 Natija',callback_data='mt:result')]]))
 _,topic,question,opts,correct=qs[i]
 await update.callback_query.edit_message_text(f'📚 {topic}\n\n{question}',reply_markup=kb(opts))

def admin_menu_text():
    return "📝 <b>Testlar bazasi</b>\n\nMavzularni ko‘rish yoki yangi mavzu yaratish uchun tanlang."

def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Mavjud testlar", callback_data="dev:mt_topics")],
        [InlineKeyboardButton("➕ Yangi test mavzusi", callback_data="dev:mt_new")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")],
    ])

def admin_topics_text():
    rows = db.topics(False)
    if not rows:
        return "📋 Hozircha test mavzulari mavjud emas."
    return "📋 <b>Mavjud test mavzulari</b>\n\n" + "\n".join(f"{i}. {name} — {'faol' if active else 'nofaol'}" for i, name, active in rows)

def admin_topics_keyboard():
    rows = db.topics(False)
    buttons = [[InlineKeyboardButton(f"{'🟢' if active else '⚪'} {name}", callback_data=f"dev:mt_topic:{tid}")] for tid,name,active in rows]
    buttons += [[InlineKeyboardButton("➕ Yangi mavzu", callback_data="dev:mt_new")], [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:managed_tests")]]
    return InlineKeyboardMarkup(buttons)
