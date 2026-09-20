import html
import random
import re
import sys
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.error import BadRequest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import managed_tests as db


def _esc(value) -> str:
    return html.escape(str(value))


# ============================================================
# 🎮 Foydalanuvchi tomoni — inline "test" so'rovi va testni yechish
#
# Qo'llab-quvvatlanadigan inline so'rovlar (barchasi "test"/"tests"/
# "testlar" so'zi bilan boshlanadi, keyin ixtiyoriy tartibda bir nechta
# qo'shimcha berilishi mumkin):
#
#   "test"                -> barcha faol savollar, aralash, variantlar bilan
#   "test 10"              -> 10 ta savol aralash tanlanadi (variantlar bilan)
#   "test pro"              -> variantlarSIZ; savol ostida harf tugmalari chiqadi,
#                            harflarni bosib so'z yig'iladi va "Yuborish" bosiladi
#   "test pro 18"           -> pro rejim + 18 ta savol
#   "test jas"              -> variantlar matni TESKARI (harflari aylantirilgan)
#   "test jas 12"           -> jas rejim + 12 ta savol
#   "test pro jas 17" / "test jas pro 16" -> barcha 3 ta o'zgarish (yoki
#   ularning istalgan kombinatsiyasi) birga ishlaydi, tartib muhim emas.
# ============================================================

# "test"/"tests"/"testlar" so'zidan keyin FAQAT "pro", "jas" va son
# (1-3 xonali) so'zlari, istalgan tartibda, bir necha marta kelishi mumkin.
_QUERY_RE = re.compile(
    r'^(?:test|tests|testlar)((?:\s+(?:pro|jas|\d{1,3}))*)\s*$',
    re.IGNORECASE,
)


def parse_query(raw_query: str):
    """Inline so'rov matnini tahlil qiladi. Mos kelmasa `None` qaytaradi,
    aks holda {'n': int|None, 'pro': bool, 'jas': bool} lug'atini qaytaradi."""
    q = (raw_query or '').strip()
    if not q:
        return None
    m = _QUERY_RE.match(q)
    if not m:
        return None
    n = None
    pro = False
    jas = False
    for tok in m.group(1).split():
        tl = tok.lower()
        if tl == 'pro':
            pro = True
        elif tl == 'jas':
            jas = True
        elif tl.isdigit() and int(tl) > 0:
            n = int(tl)
    return {'n': n, 'pro': pro, 'jas': jas}


def _encode_flags(n, pro, jas) -> str:
    return f'{n or 0}:{int(bool(pro))}:{int(bool(jas))}'


def _decode_flags(data: str):
    """"mt:start" yoki "mt:start:<n>:<pro>:<jas>" formatini o'qiydi —
    eski (yangilanishdan oldingi) inline xabarlar bilan ham moslikni
    saqlash uchun qismlar yo'q bo'lsa standart qiymatlar qaytariladi."""
    parts = data.split(':')
    n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    pro = bool(int(parts[3])) if len(parts) > 3 and parts[3].isdigit() else False
    jas = bool(int(parts[4])) if len(parts) > 4 and parts[4].isdigit() else False
    return n, pro, jas


def inline_results(n=None, pro=False, jas=False):
    total = len(db.get_questions())
    if total == 0:
        return [InlineQueryResultArticle(
            'managed-tests-empty', '📝 Faol testlar yo\u2019q',
            InputTextMessageContent('📝 Hozircha faol testlar mavjud emas.'),
        )]

    title_bits = ['📝 Test']
    if n:
        title_bits.append(f'{n} ta')
    if pro:
        title_bits.append('Pro (harf tugmalari)')
    if jas:
        title_bits.append('Teskari variant')
    title = ' — '.join(title_bits)

    count_label = f'{n} ta' if n else f'{total} ta (barchasi)'
    mode_lines = []
    if pro:
        mode_lines.append('✍️ Pro rejim: variantlarsiz, javobni harf tugmalarini bosib yig\u2018asiz.')
    if jas:
        mode_lines.append('🔄 Jas rejim: variantlar teskari (harflari aylantirilgan) beriladi.')
    body = f'📝 {count_label} test tayyor.\n' + ('\n'.join(mode_lines) + '\n' if mode_lines else '') + 'Boshlash uchun pastdagi tugmani bosing.'

    flags = _encode_flags(n, pro, jas)
    return [InlineQueryResultArticle(
        'managed-tests-' + flags, title,
        InputTextMessageContent(body),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('▶️ Testlarni boshlash', callback_data=f'mt:start:{flags}')]]),
    )]


async def on_inline(update, context):
    parsed = parse_query(update.inline_query.query)
    if parsed is None:
        return
    await update.inline_query.answer(inline_results(**parsed), cache_time=0, is_personal=True)


def _shuffle_question(row):
    """Bitta savolning variantlari tartibini aralashtiradi (mazmuni va
    to'g'ri javob o'zgarmaydi, faqat A/B/C/D pozitsiyasi o'zgaradi)."""
    i, topic, question, opts, correct = row
    order = list(range(len(opts)))
    random.shuffle(order)
    new_opts = [opts[j] for j in order]
    new_correct = order.index(correct)
    return (i, topic, question, new_opts, new_correct)


def _reverse_options(row):
    """"jas" rejimi uchun — variantlar tartibi va to'g'ri javob indeksi
    o'zgarmaydi, faqat har bir variant matnining harflari teskari
    yoziladi (masalan "dase" -> "esad")."""
    i, topic, question, opts, correct = row
    new_opts = [o[::-1] for o in opts]
    return (i, topic, question, new_opts, correct)


def kb(opts):
    return InlineKeyboardMarkup([[InlineKeyboardButton(f'{chr(65 + i)}) {x}', callback_data=f'mt:ans:{i}')] for i, x in enumerate(opts)])


def _question_html(topic, question) -> str:
    """Savol matnini KATTAROQ va aniq ko'rinishi uchun qalin (bold) qilib
    qaytaradi — Telegram xabar matnida haqiqiy shrift o'lchamini
    o'zgartirish imkoni yo'q, shuning uchun qalin yozuv eng kuchli
    (va hamma qurilmada ishlaydigan) ko'rinadigan farq beradi."""
    return f'📚 <b>{_esc(topic)}</b>\n\n<b>{_esc(question)}</b>'


# ============================================================
# ✍️ Pro rejim — harf tugmalari bilan javob yig'ish
#
# Savol (masalan: تَمْ) ostida lotin harflari tugmalar holida chiqadi.
# Foydalanuvchi harflarni bosadi — hosil bo'layotgan so'z xabarda
# jonli ko'rinib turadi (context.user_data['mt_buf']). "✅ Yuborish"
# bosilganda so'z to'g'ri javob bilan solishtiriladi.
#
# Qo'shimcha tugmalar (ch, sh, ng, c, o‘, ') FAQAT to'g'ri javobda shular bo'lsa chiqadi.
#
# callback_data: mt:k:<harf> | mt:x:<qo'shimcha indeksi> | mt:bk | mt:sub
# ============================================================

_PRO_LETTERS = 'qwertyuiopasdfghjklzxvbnm'
_PRO_ROW_SIZE = 7          # Telegram bir qatorda ko'pi bilan 8 ta tugma ruxsat beradi
_PRO_MAX_TOKENS = 40

# Qo'shimcha tugmalar: FAQAT joriy savolning to'g'ri javobida shu
# belgi/harf birikmasi bo'lsagina ko'rsatiladi (bo'lmasa chiqmaydi).
# Tartib muhim: callback_data indeksi shu ro'yxatga tayanadi.
# Ichki (normallashgan) ko'rinish — apostrof oddiy ' bilan yoziladi.
_PRO_EXTRAS = ['ch', 'sh', 'ng', 'c', "o'", "'"]
_APOSTROPHES = "\u02bb\u02bc\u2018\u2019`\u00b4"   # ʻ ʼ ‘ ’ ` ´


def _norm(text) -> str:
    """Solishtirish uchun: kichik harf + barcha apostrof ko'rinishlari
    (o‘, oʻ, o’, o` ...) bitta oddiy ' ga keltiriladi."""
    t = str(text or '').strip().casefold()
    for ch in _APOSTROPHES:
        t = t.replace(ch, "'")
    return t


def _extra_label(token: str) -> str:
    return token.replace("'", '\u2018')   # "o'" -> "o‘"


def pro_extras_for(correct_text) -> list:
    """To'g'ri javobda uchraydigan qo'shimcha tugmalar (indekslari bilan)."""
    ans = _norm(correct_text)
    return [(i, tok) for i, tok in enumerate(_PRO_EXTRAS) if tok in ans]


def pro_keyboard(correct_text='') -> InlineKeyboardMarkup:
    letters = [InlineKeyboardButton(ch, callback_data=f'mt:k:{ch}') for ch in _PRO_LETTERS]
    rows = [letters[i:i + _PRO_ROW_SIZE] for i in range(0, len(letters), _PRO_ROW_SIZE)]
    rows[-1].append(InlineKeyboardButton('⌫', callback_data='mt:bk'))
    extras = pro_extras_for(correct_text)
    if extras:
        rows.append([InlineKeyboardButton(_extra_label(tok), callback_data=f'mt:x:{i}') for i, tok in extras])
    rows.append([InlineKeyboardButton('✅ Yuborish', callback_data='mt:sub')])
    return InlineKeyboardMarkup(rows)


def pro_text(topic, question, buf: str) -> str:
    shown = _esc(_extra_label(buf)) if buf else '…'
    return (
        _question_html(topic, question)
        + f'\n\n✍️ Javobingiz: <b>{shown}</b>'
        + '\n\nHarflarni bosib so\u2018z yig\u2018ing, so\u2018ng «Yuborish»ni bosing.'
    )


def _pro_active(context) -> bool:
    return bool(
        context.user_data.get('mt_pro')
        and context.user_data.get('mt_qs')
        and not context.user_data.get('mt_answered')
    )


def _pro_state(context):
    """Joriy savol: (topic, question, correct_text) yoki None."""
    qs = context.user_data.get('mt_qs', [])
    pos = context.user_data.get('mt_i', 0)
    if pos >= len(qs):
        return None
    _, topic, question, opts, correct = qs[pos]
    return topic, question, str(opts[correct]).strip()


def _set_toks(context, toks) -> str:
    """Bosilgan tugmalar ro'yxati (har bir bosish = bitta token: 'a', 'ch', "o'" ...).
    ⌫ oxirgi BOSISHNI o'chiradi (masalan 'ch' tugmasi bitta bosish)."""
    context.user_data['mt_toks'] = toks
    buf = ''.join(toks)
    context.user_data['mt_buf'] = buf
    return buf


async def _render_pro(query, context) -> None:
    st = _pro_state(context)
    if st is None:
        return
    topic, question, correct_text = st
    try:
        await query.edit_message_text(
            pro_text(topic, question, context.user_data.get('mt_buf', '')),
            reply_markup=pro_keyboard(correct_text),
            parse_mode='HTML',
        )
    except BadRequest as e:
        if 'message is not modified' not in str(e).lower():
            raise


async def callback(update, context):
    q = update.callback_query
    data = q.data
    # "mt:sub" o'zi q.answer() ni chaqiradi (bo'sh javobda ogohlantirish ko'rsatish uchun).
    if data != 'mt:sub':
        await q.answer()

    if data.startswith('mt:k:') or data.startswith('mt:x:') or data == 'mt:bk':
        if not _pro_active(context):
            return
        toks = list(context.user_data.get('mt_toks', []))
        if data == 'mt:bk':
            if not toks:
                return
            toks.pop()
        else:
            if len(toks) >= _PRO_MAX_TOKENS:
                return
            if data.startswith('mt:k:'):
                tok = data[len('mt:k:'):]
                if len(tok) != 1 or tok not in _PRO_LETTERS:
                    return
            else:
                idx = data[len('mt:x:'):]
                st = _pro_state(context)
                if st is None or not idx.isdigit():
                    return
                # Faqat shu savol uchun ko'rsatilgan qo'shimcha tugmalar qabul qilinadi.
                allowed = dict(pro_extras_for(st[2]))
                tok = allowed.get(int(idx))
                if tok is None:
                    return
            toks.append(tok)
        _set_toks(context, toks)
        return await _render_pro(q, context)

    if data == 'mt:sub':
        if not _pro_active(context):
            await q.answer()
            return
        buf = context.user_data.get('mt_buf', '')
        if not buf:
            await q.answer('✍️ Avval harflarni tanlang', show_alert=False)
            return
        await q.answer()
        qs = context.user_data['mt_qs']
        pos = context.user_data.get('mt_i', 0)
        _, topic, question, opts, correct = qs[pos]
        context.user_data['mt_answered'] = True
        correct_text = str(opts[correct]).strip()
        good = _norm(buf) == _norm(correct_text)
        context.user_data['mt_ok'] = context.user_data.get('mt_ok', 0) + (1 if good else 0)
        context.user_data['mt_bad'] = context.user_data.get('mt_bad', 0) + (0 if good else 1)
        next_label = '📊 Natija' if pos + 1 >= len(qs) else '➡️ Keyingisi'
        next_data = 'mt:result' if pos + 1 >= len(qs) else 'mt:next'
        result_line = (
            '🥰 To\u2018g\u2018ri javob!' if good
            else f'😡 Xato javob! To\u2018g\u2018ri javob: <b>{_esc(correct_text)}</b>'
        )
        await q.edit_message_text(
            result_line + f'\n\n<b>{_esc(question)}</b>\n✍️ Sizning javobingiz: <b>{_esc(_extra_label(buf))}</b>',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(next_label, callback_data=next_data)]]),
            parse_mode='HTML',
        )
        return

    if data.startswith('mt:start'):
        n, pro, jas = _decode_flags(data)
        qs = db.get_questions()
        if not qs:
            await q.edit_message_text('📝 Hozircha faol testlar mavjud emas.')
            return
        random.shuffle(qs)
        if n:
            qs = qs[:n]
        if pro:
            # Pro rejimda variantlar umuman ko'rsatilmaydi — faqat savol
            # matni va to'g'ri javob (matn taqqoslash uchun) kerak, shuning
            # uchun variant tartibini aralashtirish/teskari qilishning
            # ma'nosi yo'q (foydalanuvchi ularni ko'rmaydi).
            pass
        else:
            qs = [_shuffle_question(row) for row in qs]
            if jas:
                qs = [_reverse_options(row) for row in qs]
        context.user_data['mt_qs'] = qs
        context.user_data['mt_i'] = 0
        context.user_data['mt_ok'] = 0
        context.user_data['mt_bad'] = 0
        context.user_data['mt_pro'] = pro
        context.user_data.pop('mt_answered', None)
        context.user_data.pop('mt_wait_text', None)
        context.user_data.pop('mt_buf', None)
        context.user_data.pop('mt_toks', None)
        return await send(update, context)

    if data.startswith('mt:ans:'):
        if context.user_data.get('mt_answered'):
            return
        i = int(data.split(':')[-1])
        qs = context.user_data.get('mt_qs', [])
        pos = context.user_data.get('mt_i', 0)
        if not qs:
            return
        context.user_data['mt_answered'] = True
        good = i == qs[pos][4]
        context.user_data['mt_ok'] += good
        context.user_data['mt_bad'] += not good
        next_label = '📊 Natija' if pos + 1 >= len(qs) else '➡️ Keyingisi'
        next_data = 'mt:result' if pos + 1 >= len(qs) else 'mt:next'
        await q.edit_message_text(
            ('🥰 To\u2018g\u2018ri javob!' if good else '😡 Xato javob!') + f'\n\n<b>{_esc(qs[pos][2])}</b>',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(next_label, callback_data=next_data)]]),
            parse_mode='HTML',
        )
        return
    if data == 'mt:next':
        context.user_data['mt_i'] += 1
        context.user_data.pop('mt_answered', None)
        context.user_data.pop('mt_wait_text', None)
        context.user_data.pop('mt_buf', None)
        context.user_data.pop('mt_toks', None)
        return await send(update, context)
    if data == 'mt:result':
        n = len(context.user_data.get('mt_qs', []))
        ok = context.user_data.get('mt_ok', 0)
        p = ok * 100 / n if n else 0
        s = 'yomon🙁' if p <= 50 else 'o\u2018rta😐' if p <= 85 else 'yaxshi🙂' if p < 100 else 'alo🥰'
        context.user_data.pop('mt_wait_text', None)
        context.user_data.pop('mt_buf', None)
        context.user_data.pop('mt_toks', None)
        context.user_data.pop('mt_pro', None)
        await q.edit_message_text(f'Testlar: {n} ta\nTo\u2018g\u2018ri javoblar: {ok} ta\nXato javoblar: {n - ok} ta\nHolati: {s}')


async def send(update, context):
    qs = context.user_data.get('mt_qs', [])
    i = context.user_data.get('mt_i', 0)
    if i >= len(qs):
        context.user_data.pop('mt_wait_text', None)
        return await update.callback_query.edit_message_text('Testlar tugadi.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📊 Natija', callback_data='mt:result')]]))
    _, topic, question, opts, correct = qs[i]
    if context.user_data.get('mt_pro'):
        # Pro rejim: variantlar YO'Q — savol ostida harf tugmalari chiqadi,
        # foydalanuvchi so'zni harflarni bosib yig'adi (qarang: pro_keyboard).
        _set_toks(context, [])
        context.user_data['mt_wait_text'] = False
        await update.callback_query.edit_message_text(
            pro_text(topic, question, ''), reply_markup=pro_keyboard(opts[correct]), parse_mode='HTML',
        )
        return
    await update.callback_query.edit_message_text(_question_html(topic, question), reply_markup=kb(opts), parse_mode='HTML')


# ============================================================
# ✍️ (ESKI) Pro rejim — matn javobini qabul qilish
#
# ESLATMA: endi pro rejimda javob harf tugmalari orqali yig'iladi va
# 'mt_wait_text' hech qachon True qilinmaydi, shuning uchun quyidagi
# funksiyalar faqat orqaga moslik uchun qoldirilgan.
#
# Chaqiruvchi (handlers/universal_chat.py) HAR bir oddiy matn xabaridan
# OLDIN `is_waiting_text_answer(context)`ni tekshiradi — True bo'lsa, shu
# xabar ENDI universal AI/mention oqimiga YUBORILMAYDI, balki to'g'ridan-
# to'g'ri shu yerga (`handle_pro_text_answer`) beriladi.
# ============================================================

def is_waiting_text_answer(context) -> bool:
    return bool(context.user_data.get('mt_pro') and context.user_data.get('mt_wait_text'))


async def handle_pro_text_answer(update, context) -> None:
    """Pro rejimdagi savolga foydalanuvchi yuborgan matn javobini tekshiradi.

    Katta/kichik harf farqi E'TIBORGA OLINMAYDI ("DASe", "daSe", "dase" —
    barchasi bir xil qabul qilinadi), lekin boshqa harf/son/belgi qo'shilgan
    yoki yetishmagan javob XATO hisoblanadi (aniq mos kelish kerak)."""
    qs = context.user_data.get('mt_qs', [])
    pos = context.user_data.get('mt_i', 0)
    if not qs or pos >= len(qs):
        context.user_data.pop('mt_wait_text', None)
        return

    context.user_data['mt_wait_text'] = False
    _, topic, question, opts, correct = qs[pos]
    correct_text = str(opts[correct]).strip()
    user_answer = (update.message.text or '').strip()
    good = user_answer.casefold() == correct_text.casefold()
    context.user_data['mt_ok'] = context.user_data.get('mt_ok', 0) + (1 if good else 0)
    context.user_data['mt_bad'] = context.user_data.get('mt_bad', 0) + (0 if good else 1)

    next_label = '📊 Natija' if pos + 1 >= len(qs) else '➡️ Keyingisi'
    next_data = 'mt:result' if pos + 1 >= len(qs) else 'mt:next'
    result_line = '🥰 To\u2018g\u2018ri javob!' if good else f'😡 Xato javob! To\u2018g\u2018ri javob: <b>{_esc(correct_text)}</b>'
    await update.message.reply_text(
        result_line + f'\n\n<b>{_esc(question)}</b>',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(next_label, callback_data=next_data)]]),
        parse_mode='HTML',
    )


# ============================================================
# 🛠 Admin tomoni — /developer > 📝 Testlar bazasi
# ============================================================

def admin_menu_text():
    return "📝 <b>Testlar bazasi</b>\n\nKerakli bo\u2018limni tanlang."


def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Mavjud testlar", callback_data="dev:mt_topics")],
        [InlineKeyboardButton("✏️ Testlarni tahrirlash", callback_data="dev:mt_edit_list")],
        [InlineKeyboardButton("🗑 Testlarni o\u2018chirish", callback_data="dev:mt_del_list")],
        [InlineKeyboardButton("➕ Yangi testlar", callback_data="dev:mt_addnew_list")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="dev:menu")],
    ])


def stop_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton('🛑 To\u2018xtatish', callback_data='dev:mt_stop')]])


_MODE_PREFIX = {'view': 'mt_topic', 'edit': 'mt_edit_topic', 'delete': 'mt_deltopic', 'addnew': 'mt_addq'}
_MODE_TITLE = {
    'view': '📋 Mavjud testlar',
    'edit': '✏️ Tahrirlash uchun mavzuni tanlang',
    'delete': '🗑 O\u2018chirish uchun mavzuni tanlang',
    'addnew': '➕ Savol qo\u2018shiladigan mavzuni tanlang (yoki yangisini yarating)',
}


def _topics_text(mode='view'):
    rows = db.topics(False)
    title = _MODE_TITLE.get(mode, _MODE_TITLE['view'])
    if not rows:
        return f"<b>{title}</b>\n\nHozircha test mavzulari mavjud emas."
    lines = [
        f"{idx}. {_esc(name)} — {'faol' if active else 'nofaol'} ({db.count_questions(tid)} ta savol)"
        for idx, (tid, name, active) in enumerate(rows, start=1)
    ]
    return f"<b>{title}</b>\n\n" + "\n".join(lines)


def _topics_keyboard(mode='view'):
    rows = db.topics(False)
    prefix = _MODE_PREFIX.get(mode, 'mt_topic')
    buttons = [[InlineKeyboardButton(f"{'🟢' if active else '⚪'} {name}", callback_data=f"dev:{prefix}:{tid}")] for tid, name, active in rows]
    if mode == 'addnew':
        buttons.append([InlineKeyboardButton('➕ Yangi qo\u2018shish', callback_data='dev:mt_new')])
    elif mode == 'view':
        buttons.append([InlineKeyboardButton('➕ Yangi mavzu', callback_data='dev:mt_new')])
    buttons.append([InlineKeyboardButton('⬅️ Orqaga', callback_data='dev:managed_tests')])
    return InlineKeyboardMarkup(buttons)


# Backward-compatible aliases (old callers before this rewrite).
def admin_topics_text():
    return _topics_text('view')


def admin_topics_keyboard():
    return _topics_keyboard('view')


def topic_detail_text(tid):
    topic = db.get_topic(tid)
    if not topic:
        return "⚠️ Mavzu topilmadi."
    _, name, active = topic
    n = db.count_questions(tid)
    return f"📝 <b>{_esc(name)}</b>\nHolati: {'🟢 faol' if active else '⚪ nofaol'}\nSavollar soni: {n} ta"


def topic_detail_keyboard(tid):
    topic = db.get_topic(tid)
    active = topic[2] if topic else 0
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('⚪ Nofaol qilish' if active else '🟢 Faol qilish', callback_data=f'dev:mt_toggle:{tid}')],
        [InlineKeyboardButton('➕ Savol qo\u2018shish', callback_data=f'dev:mt_addq:{tid}')],
        [InlineKeyboardButton('✏️ Savollarni tahrirlash', callback_data=f'dev:mt_edit_topic:{tid}')],
        [InlineKeyboardButton('🗑 Mavzuni o\u2018chirish', callback_data=f'dev:mt_deltopic:{tid}')],
        [InlineKeyboardButton('⬅️ Orqaga', callback_data='dev:mt_topics')],
    ])


def questions_list_text(tid):
    topic = db.get_topic(tid)
    name = topic[1] if topic else '?'
    qs = db.list_questions(tid)
    if not qs:
        return f"✏️ <b>{_esc(name)}</b>\n\nBu mavzuda hali savollar yo\u2018q."
    lines = [f"{idx}. {_esc(q[:60])}" for idx, (qid, q, opts, correct, active) in enumerate(qs, start=1)]
    return f"✏️ <b>{_esc(name)}</b> — savolni tanlang:\n\n" + "\n".join(lines)


def questions_list_keyboard(tid):
    qs = db.list_questions(tid)
    buttons = [[InlineKeyboardButton(f"{idx}. {q[:30]}", callback_data=f"dev:mt_editq:{qid}")] for idx, (qid, q, opts, correct, active) in enumerate(qs, start=1)]
    buttons.append([InlineKeyboardButton('⬅️ Orqaga', callback_data=f'dev:mt_topic:{tid}')])
    return InlineKeyboardMarkup(buttons)


def question_detail_text(qid):
    row = db.get_question(qid)
    if not row:
        return "⚠️ Savol topilmadi."
    _, tid, question, opts, correct, active = row
    lines = [f"{chr(65 + i)}) {_esc(o)}" + (' ✅' if i == correct else '') for i, o in enumerate(opts)]
    return f"❓ {_esc(question)}\n\n" + "\n".join(lines)


def question_detail_keyboard(qid):
    row = db.get_question(qid)
    tid = row[1] if row else 0
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✏️ Savol matnini o\u2018zgartirish', callback_data=f'dev:mt_editqtext:{qid}')],
        [InlineKeyboardButton('🔁 Variantlarni qayta kiritish', callback_data=f'dev:mt_editqopts:{qid}')],
        [InlineKeyboardButton('🗑 Savolni o\u2018chirish', callback_data=f'dev:mt_delq:{qid}')],
        [InlineKeyboardButton('⬅️ Orqaga', callback_data=f'dev:mt_edit_topic:{tid}')],
    ])


def confirm_delete_topic_text(tid):
    topic = db.get_topic(tid)
    name = topic[1] if topic else '?'
    n = db.count_questions(tid)
    return f"❗ «{_esc(name)}» mavzusini butunlay o\u2018chirmoqchimisiz?\nBarcha savollar ({n} ta) ham o\u2018chib ketadi."


def confirm_delete_topic_keyboard(tid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Ha, o\u2018chirish', callback_data=f'dev:mt_deltopic_yes:{tid}')],
        [InlineKeyboardButton('❌ Yo\u2018q', callback_data=f'dev:mt_topic:{tid}')],
    ])


def confirm_delete_question_text(qid):
    row = db.get_question(qid)
    q = row[2] if row else '?'
    return f"❗ Ushbu savolni o\u2018chirmoqchimisiz?\n\n{_esc(q)}"


def confirm_delete_question_keyboard(qid):
    row = db.get_question(qid)
    tid = row[1] if row else 0
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Ha, o\u2018chirish', callback_data=f'dev:mt_delq_yes:{qid}')],
        [InlineKeyboardButton('❌ Yo\u2018q', callback_data=f'dev:mt_editq:{qid}')],
    ])


async def _show(query, text, keyboard):
    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='HTML')
    except BadRequest as e:
        if 'message is not modified' not in str(e).lower():
            raise


async def handle_dev_callback(action, parts, update, context, query, DEV_MENU, DEV_WAIT_TEXT):
    """/developer > 📝 Testlar bazasi bo'limidagi barcha "dev:mt_*" callbacklarni
    boshqaradi. developer.py shu funksiyaga bitta joydan yo'naltiradi."""

    if action == 'managed_tests':
        await _show(query, admin_menu_text(), admin_menu_keyboard())
        return DEV_MENU

    if action == 'mt_topics':
        await _show(query, _topics_text('view'), _topics_keyboard('view'))
        return DEV_MENU

    if action == 'mt_edit_list':
        await _show(query, _topics_text('edit'), _topics_keyboard('edit'))
        return DEV_MENU

    if action == 'mt_del_list':
        await _show(query, _topics_text('delete'), _topics_keyboard('delete'))
        return DEV_MENU

    if action == 'mt_addnew_list':
        await _show(query, _topics_text('addnew'), _topics_keyboard('addnew'))
        return DEV_MENU

    if action == 'mt_topic':
        tid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _show(query, topic_detail_text(tid), topic_detail_keyboard(tid))
        return DEV_MENU

    if action == 'mt_toggle':
        tid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        db.toggle_topic(tid)
        await _show(query, topic_detail_text(tid), topic_detail_keyboard(tid))
        return DEV_MENU

    if action == 'mt_addq':
        tid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        topic = db.get_topic(tid)
        if not topic:
            await _show(query, "⚠️ Mavzu topilmadi.", _topics_keyboard('view'))
            return DEV_MENU
        name = topic[1]
        num = db.count_questions(tid) + 1
        context.user_data['dev_action'] = {'type': 'mt_add_q_question', 'topic_id': tid, 'topic_name': name, 'num': num}
        await _show(query, f"📝 <b>{_esc(name)}</b> uchun {num}-chi test savolini bering:", stop_keyboard())
        return DEV_WAIT_TEXT

    if action == 'mt_new':
        context.user_data['dev_action'] = {'type': 'mt_new_topic'}
        await _show(
            query, "📝 Yangi test mavzusi nomini yuboring:\n(masalan: ت)",
            InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Orqaga', callback_data='dev:managed_tests')]]),
        )
        return DEV_WAIT_TEXT

    if action == 'mt_stop':
        info = context.user_data.pop('dev_action', None) or {}
        tid = info.get('topic_id')
        if tid:
            n = db.count_questions(tid)
            name = info.get('topic_name', '?')
            await _show(
                query, f"🛑 To\u2018xtatildi.\n«{_esc(name)}» mavzusida hozir jami {n} ta savol bor (barchasi faol).",
                topic_detail_keyboard(tid),
            )
        else:
            await _show(query, admin_menu_text(), admin_menu_keyboard())
        return DEV_MENU

    if action == 'mt_edit_topic':
        tid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _show(query, questions_list_text(tid), questions_list_keyboard(tid))
        return DEV_MENU

    if action == 'mt_editq':
        qid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _show(query, question_detail_text(qid), question_detail_keyboard(qid))
        return DEV_MENU

    if action == 'mt_editqtext':
        qid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        context.user_data['dev_action'] = {'type': 'mt_edit_qtext', 'qid': qid}
        await _show(
            query, "✏️ Savolning yangi matnini yuboring:",
            InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Bekor qilish', callback_data=f'dev:mt_editq:{qid}')]]),
        )
        return DEV_WAIT_TEXT

    if action == 'mt_editqopts':
        qid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        context.user_data['dev_action'] = {'type': 'mt_edit_opt_correct', 'qid': qid}
        await _show(
            query, "✅ Yangi to\u2018g\u2018ri variantni bering:",
            InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Bekor qilish', callback_data=f'dev:mt_editq:{qid}')]]),
        )
        return DEV_WAIT_TEXT

    if action == 'mt_delq':
        qid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _show(query, confirm_delete_question_text(qid), confirm_delete_question_keyboard(qid))
        return DEV_MENU

    if action == 'mt_delq_yes':
        qid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        row = db.get_question(qid)
        tid = row[1] if row else 0
        db.delete_question(qid)
        await _show(query, questions_list_text(tid), questions_list_keyboard(tid))
        return DEV_MENU

    if action == 'mt_deltopic':
        tid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _show(query, confirm_delete_topic_text(tid), confirm_delete_topic_keyboard(tid))
        return DEV_MENU

    if action == 'mt_deltopic_yes':
        tid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        db.delete_topic(tid)
        await _show(query, _topics_text('delete'), _topics_keyboard('delete'))
        return DEV_MENU

    await _show(query, admin_menu_text(), admin_menu_keyboard())
    return DEV_MENU


async def handle_dev_text(mt_action, update, context, DEV_MENU, DEV_WAIT_TEXT):
    """DEV_WAIT_TEXT holatida kelgan matnlarni "mt_*" turlari bo'yicha
    boshqaradi (yangi mavzu nomi, savol matni, to'g'ri/xato variantlar)."""
    t = mt_action.get('type')
    text = (update.message.text or '').strip()

    if t == 'mt_new_topic':
        if not text:
            await update.message.reply_text('⚠️ Mavzu nomi bo\u2018sh bo\u2018lmasin. Qaytadan yuboring:')
            return DEV_WAIT_TEXT
        existed = db.get_topic_by_name(text) is not None
        tid = db.add_topic(text)
        num = db.count_questions(tid) + 1
        context.user_data['dev_action'] = {'type': 'mt_add_q_question', 'topic_id': tid, 'topic_name': text, 'num': num}
        prefix = (
            f"ℹ️ «{_esc(text)}» mavzusi allaqachon mavjud, unga savol qo\u2018shilmoqda.\n\n"
            if existed else f"✅ «{_esc(text)}» mavzusi yaratildi.\n\n"
        )
        await update.message.reply_text(
            prefix + f"📝 <b>{_esc(text)}</b> uchun {num}-chi test savolini bering:",
            reply_markup=stop_keyboard(), parse_mode='HTML',
        )
        return DEV_WAIT_TEXT

    if t == 'mt_add_q_question':
        if not text:
            await update.message.reply_text('⚠️ Savol matni bo\u2018sh bo\u2018lmasin. Qaytadan yuboring:', reply_markup=stop_keyboard())
            return DEV_WAIT_TEXT
        mt_action['question'] = text
        mt_action['type'] = 'mt_add_q_correct'
        context.user_data['dev_action'] = mt_action
        await update.message.reply_text('✅ Savol qabul qilindi.\n\nTo\u2018g\u2018ri variantni bering:', reply_markup=stop_keyboard())
        return DEV_WAIT_TEXT

    if t == 'mt_add_q_correct':
        if not text:
            await update.message.reply_text('⚠️ Variant bo\u2018sh bo\u2018lmasin. Qaytadan yuboring:', reply_markup=stop_keyboard())
            return DEV_WAIT_TEXT
        mt_action['correct_text'] = text
        mt_action['wrongs'] = []
        mt_action['type'] = 'mt_add_q_wrong'
        context.user_data['dev_action'] = mt_action
        await update.message.reply_text('✅ To\u2018g\u2018ri variant qabul qilindi.\n\n1-chi xato variantni bering:', reply_markup=stop_keyboard())
        return DEV_WAIT_TEXT

    if t == 'mt_add_q_wrong':
        if not text:
            await update.message.reply_text('⚠️ Variant bo\u2018sh bo\u2018lmasin. Qaytadan yuboring:', reply_markup=stop_keyboard())
            return DEV_WAIT_TEXT
        wrongs = mt_action.get('wrongs', [])
        wrongs.append(text)
        mt_action['wrongs'] = wrongs
        if len(wrongs) < 3:
            context.user_data['dev_action'] = mt_action
            await update.message.reply_text(
                f'❌ {len(wrongs)}-chi xato variant qabul qilindi.\n\n{len(wrongs) + 1}-chi xato variantni bering:',
                reply_markup=stop_keyboard(),
            )
            return DEV_WAIT_TEXT
        tid = mt_action['topic_id']
        topic_name = mt_action['topic_name']
        num = mt_action['num']
        options = [mt_action['correct_text']] + wrongs
        db.add_question(tid, mt_action['question'], options, 0)
        next_num = num + 1
        context.user_data['dev_action'] = {'type': 'mt_add_q_question', 'topic_id': tid, 'topic_name': topic_name, 'num': next_num}
        await update.message.reply_text(
            f"✅ {_esc(topic_name)} uchun {num}-chi savol qabul qilindi.\n\n"
            f"📝 <b>{_esc(topic_name)}</b> uchun {next_num}-chi savolni bering:",
            reply_markup=stop_keyboard(), parse_mode='HTML',
        )
        return DEV_WAIT_TEXT

    if t == 'mt_edit_qtext':
        qid = mt_action['qid']
        if not text:
            await update.message.reply_text('⚠️ Matn bo\u2018sh bo\u2018lmasin. Qaytadan yuboring:')
            return DEV_WAIT_TEXT
        db.update_question(qid, question=text)
        context.user_data.pop('dev_action', None)
        await update.message.reply_text(
            '✅ Savol matni yangilandi.\n\n' + question_detail_text(qid),
            reply_markup=question_detail_keyboard(qid), parse_mode='HTML',
        )
        return DEV_MENU

    if t == 'mt_edit_opt_correct':
        qid = mt_action['qid']
        if not text:
            await update.message.reply_text('⚠️ Variant bo\u2018sh bo\u2018lmasin. Qaytadan yuboring:')
            return DEV_WAIT_TEXT
        mt_action['correct_text'] = text
        mt_action['wrongs'] = []
        mt_action['type'] = 'mt_edit_opt_wrong'
        context.user_data['dev_action'] = mt_action
        await update.message.reply_text(
            '✅ To\u2018g\u2018ri variant qabul qilindi.\n\n1-chi xato variantni bering:',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Bekor qilish', callback_data=f'dev:mt_editq:{qid}')]]),
        )
        return DEV_WAIT_TEXT

    if t == 'mt_edit_opt_wrong':
        qid = mt_action['qid']
        if not text:
            await update.message.reply_text('⚠️ Variant bo\u2018sh bo\u2018lmasin. Qaytadan yuboring:')
            return DEV_WAIT_TEXT
        wrongs = mt_action.get('wrongs', [])
        wrongs.append(text)
        mt_action['wrongs'] = wrongs
        if len(wrongs) < 3:
            context.user_data['dev_action'] = mt_action
            await update.message.reply_text(
                f'❌ {len(wrongs)}-chi xato variant qabul qilindi.\n\n{len(wrongs) + 1}-chi xato variantni bering:',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Bekor qilish', callback_data=f'dev:mt_editq:{qid}')]]),
            )
            return DEV_WAIT_TEXT
        options = [mt_action['correct_text']] + wrongs
        db.update_question(qid, options=options, correct=0)
        context.user_data.pop('dev_action', None)
        await update.message.reply_text(
            '✅ Variantlar yangilandi.\n\n' + question_detail_text(qid),
            reply_markup=question_detail_keyboard(qid), parse_mode='HTML',
        )
        return DEV_MENU

    context.user_data.pop('dev_action', None)
    return DEV_MENU
