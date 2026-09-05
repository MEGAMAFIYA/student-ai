"""
🧭 BOT MENTION + MAXSUS COMMAND DISPATCHER.

Muammo: "/tabrik", "/rasim", "/vid" — oddiy ASCII Telegram buyruqlari,
shuning uchun ularni Telegram o'zi "bot_command" entity sifatida
belgilaydi va CommandHandler ularni ORIGINAL ("/tabrik ...") holatda
avtomatik ushlaydi — HATTO Privacy Mode yoqilgan bo'lsa ham (Telegram
buyruqlarni har doim botga yetkazadi).

Lekin ikkita holat CommandHandler qamrovidan TASHQARIDA qoladi va
universal_chat.handle_message (oddiy MessageHandler) orqali keladi:

  1) "@Student_ai_uz_bot /vid URL" — xabar "@" bilan boshlangani uchun
     Telegram buni bot_command entity offset=0 sifatida BELGILAMAYDI
     (CommandHandler faqat xabar boshida turgan buyruqni ushlaydi).
  2) "/qo‘shiq ..." — "qo‘shiq" tarkibida lotin harflari/raqam/pastki
     chiziqdan boshqa belgi (apostrof: ‘ ’ ' ʻ) borligi uchun Telegram
     buni umuman bot_command entity sifatida belgilamaydi (Telegram
     buyruq nomi qoidasi: faqat [a-z0-9_], 1-32 belgi) — u oddiy matn
     xabari hisoblanadi.

Shu modul ana shu ikki holatni (va ularning kombinatsiyasini —
"@bot /qo‘shiq ...") aniqlab, ASOSIY handler funksiyasiga (tabrik_cmd /
rasim_cmd / vid_cmd / qoshiq_cmd) TO'G'RIDAN-TO'G'RI yo'naltiradi — hech
qanday mantiq DUPLICATE qilinmaydi, bir xil funksiyalar CommandHandler
orqali ham, shu dispatcher orqali ham chaqiriladi.

MUHIM (Telegram platforma cheklovi, kodda tuzatib bo'lmaydi): guruhda
Privacy Mode YOQILGAN bo'lsa, "/qo‘shiq ..." xabari (mention'siz, apostrof
tufayli haqiqiy buyruq hisoblanmagani uchun) botga UMUMAN YETIB
BORMAYDI — Telegram uni oddiy, botga aloqasi yo'q guruh xabari deb
hisoblaydi. Bunday holatda guruhda FAQAT quyidagilar ishlaydi:
  - "@Student_ai_uz_bot /qo‘shiq ..." (mention — privacy mode'ni chetlab o'tadi)
  - "/qoshiq ..." (apostrofsiz ASCII alias — haqiqiy buyruq, CommandHandler)
To'liq (mention'siz "/qo‘shiq ...") ishlashi uchun BotFather'da
/setprivacy → Disable qilish kerak (bot.py'dagi eslatmaga qarang).
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# bot.py > _post_init ichida application.bot.get_me() natijasi bilan
# to'ldiriladi. Shu vaqtgacha config.BOT_USERNAME_FALLBACK ishlatiladi.
_bot_username: str = ""


def set_bot_username(username: str) -> None:
    global _bot_username
    _bot_username = (username or "").lstrip("@")
    logger.info(f"🧭 mention_dispatch: bot username o'rnatildi -> @{_bot_username}")


def get_bot_username() -> str:
    if _bot_username:
        return _bot_username
    import config
    return config.BOT_USERNAME_FALLBACK


# "qo‘shiq" so'zidagi apostrofning barcha keng tarqalgan variantlari
# (o'zbek klaviaturalar/qurilmalarda turlicha belgi kelishi mumkin) —
# hammasi bitta kanonik shaklga ("'") tushiriladi, shundan keyin taqqoslanadi.
_APOSTROPHE_VARIANTS = ["\u2018", "\u2019", "\u02bb", "\u02bc", "`", "\u00b4"]


def _normalize_apostrophes(text: str) -> str:
    for ch in _APOSTROPHE_VARIANTS:
        text = text.replace(ch, "'")
    return text


# Har bir maxsus buyruq uchun tan olinadigan "so'z" variantlari (kanonik
# apostrofdan keyin, kichik harfda taqqoslanadi). ASCII alias ("qoshiq")
# ham shu yerda — shu orqali "/qoshiq" ham "/qo'shiq" bilan bir xil
# handlerga tushadi (masalan @mention orqali kelganda).
COMMAND_ALIASES = {
    "tabrik": "tabrik",
    "rasim": "rasim",
    "vid": "vid",
    "qo'shiq": "qoshiq",
    "qoshiq": "qoshiq",
    "pro": "pro",
    "my": "my",
    "kino": "kino",
}

# Har biri "/" dan keyin, so'z chegarasidan oldin tekshiriladi.
_WORD_RE = re.compile(r"^/([^\s@]+)(?:@([A-Za-z0-9_]+))?(\s+|$)", re.UNICODE)


@dataclass
class ParsedCommand:
    command: str          # kanonik nom: "tabrik" | "rasim" | "vid" | "qoshiq"
    remainder_text: str   # handlerga yuboriladigan TO'LIQ matn (masalan "/tabrik Salom...")
    had_mention: bool


def _match_command_prefix(text: str):
    """Matn boshida "/buyruq" yoki "/buyruq@BotUsername" bormi — bo'lsa
    (kanonik_nom, mos_username_yoki_None) qaytaradi, aks holda None."""
    m = _WORD_RE.match(text)
    if not m:
        return None
    raw_word = _normalize_apostrophes(m.group(1)).lower()
    canonical = COMMAND_ALIASES.get(raw_word)
    if not canonical:
        return None
    return canonical, m.group(2)


def strip_mention_prefix(text: str):
    """Agar `text` "@BotUsername" bilan boshlansa (case-insensitive),
    o'sha qismni (va undan keyingi bo'sh joyni) olib tashlab qoladigan
    matnni qaytaradi. Aks holda None qaytaradi (mention yo'q)."""
    if not text:
        return None
    username = get_bot_username()
    if not username:
        return None
    stripped = text.lstrip()
    prefix = "@" + username
    if not stripped.lower().startswith(prefix.lower()):
        return None
    rest = stripped[len(prefix):]
    # "@BotUsernameSomethingElse" kabi qisman moslikni oldini olish —
    # mention'dan keyin bo'sh joy yoki xabar tugashi kerak.
    if rest and not rest[0].isspace():
        return None
    return rest.lstrip()


def resolve(raw_text: str):
    """
    Kiruvchi (buyruq sifatida Telegram tomonidan ANIQLANMAGAN) matnni
    tahlil qilib, quyidagi uchta holatdan birini qaytaradi:

      ("command", ParsedCommand)  — /tabrik, /rasim, /vid yoki /qo'shiq
                                     (mention orqali yoki to'g'ridan-to'g'ri,
                                     masalan apostrofli "/qo'shiq ...")
                                     aniqlandi, mos handlerga yuborilsin.
      ("mention_ai", cleaned_text) — bot mention qilingan, lekin undan
                                      keyingi matn maxsus buyruq EMAS —
                                      mavjud AI (universal_chat) mexanizmiga
                                      (mention qismisiz) yuborilsin.
      ("none", raw_text)          — na mention, na maxsus buyruq — chaqiruvchi
                                      o'zining odatiy (mavjud) logikasini davom ettirsin.
    """
    if not raw_text:
        return "none", raw_text

    after_mention = strip_mention_prefix(raw_text)
    had_mention = after_mention is not None
    text_to_check = after_mention if had_mention else raw_text

    match = _match_command_prefix(text_to_check)
    if match:
        canonical, mentioned_bot = match
        # Agar "/vid@BoshqaBot ..." kabi ANIQ boshqa botga qaratilgan
        # bo'lsa — bizga tegishli emas, e'tiborsiz qoldiramiz.
        if mentioned_bot and mentioned_bot.lower() != get_bot_username().lower():
            return "none", raw_text
        return "command", ParsedCommand(
            command=canonical, remainder_text=text_to_check, had_mention=had_mention,
        )

    if had_mention:
        return "mention_ai", text_to_check

    return "none", raw_text
