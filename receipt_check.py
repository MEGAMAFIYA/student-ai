"""
🤖 CHEKNI BOT TOMONIDAN TEKSHIRISH — sof mantiq (Telegram/AI/tarmoqqa bog'liq EMAS).

Foydalanuvchi yuborgan chek rasmidan AI (vision) ma'lumot AJRATIB beradi
(handlers/wallet_ui.py > _extract_receipt_data). Bu modul o'sha ajratilgan
ma'lumotni bizning rekvizitlarimiz bilan solishtiradi:

  1) 💳 Karta raqami   — chekdagi QABUL QILUVCHI kartasi bizniki bilan mosmi
                         (maskalangan "8600 12** **** 3456" ko'rinishi ham qabul qilinadi)
  2) 👤 Karta egasi    — chekdagi qabul qiluvchi ismi bizniki bilan mosmi
                         ("ALISHER K." / "Karimov Alisher" / kirill yozuvi ham)
  3) 💰 Summa          — chekdagi summa foydalanuvchi tanlagan summaga TENGMI

MUHIM: AI'ga bizning karta raqamimiz/ismimiz HECH QACHON aytilmaydi — u
faqat chekda ko'ringanini o'qiydi (aks holda u "mos keladi" deb o'ylab
topishga moyil bo'ladi). Solishtirish shu yerda, oddiy kod bilan bajariladi.

Bu tekshiruv FIRIBGARLIKDAN TO'LIQ himoya QILMAYDI (tahrirlangan skrinshot
hamma tekshiruvdan o'tishi mumkin) — shuning uchun tizimda admin har doim
xabardor qilinadi va soxta to'lovni qaytara oladi (wallet.revoke_payment).
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Kartadagi yashirilgan (maskalangan) raqamlar uchun ishlatiladigan belgilar.
_MASK_CHARS = set("*•●·xX#✱∗◦_")
# Karta raqami guruhlari orasidagi ajratgichlar.
_SEPARATORS = set("-–— \u00a0\t")

# Ismlarni solishtirishda "o'xshash" hisoblash chegarasi (OCR xatolari uchun).
_NAME_FUZZY_RATIO = 0.85
_MIN_VISIBLE_CARD_DIGITS = 4

_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
    "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ў": "o", "қ": "q",
    "ғ": "g", "ҳ": "h",
}


# ============================================================
# Natija turi
# ============================================================

@dataclass
class ReceiptCheck:
    """Har bir tekshiruv: True (mos), False (mos EMAS), None (chekdan o'qib
    bo'lmadi yoki sozlama yo'q). `ok` — FAQAT hammasi True bo'lsa True."""
    card: bool | None = None
    name: bool | None = None
    amount: bool | None = None
    confidence_ok: bool | None = None
    ok: bool = False
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "card": self.card, "name": self.name, "amount": self.amount,
            "confidence_ok": self.confidence_ok, "ok": self.ok, "notes": list(self.notes),
        }


# ============================================================
# Summa
# ============================================================

def parse_amount(value) -> int | None:
    """'10 000', '10,000', '10000.00', '10 000,00 UZS', 10000.0 -> 10000."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    text = re.sub(r"[^\d.,]", "", str(value))
    if not text or not re.search(r"\d", text):
        return None

    last_dot, last_comma = text.rfind("."), text.rfind(",")
    sep_pos = max(last_dot, last_comma)
    if sep_pos == -1:
        return int(text)

    tail = text[sep_pos + 1:]
    head = text[:sep_pos]
    # "1.000.000" / "1,000,000" — bir xil ajratgich takrorlansa, hammasi minglik.
    if text.count(text[sep_pos]) > 1:
        return int(re.sub(r"[.,]", "", text))
    only_one_kind = text.count(".") + text.count(",") == 1
    # "10,000" / "10.000" (aynan 3 raqam va bitta ajratgich) — minglik ajratgich.
    if only_one_kind and len(tail) == 3 and head:
        return int(head + tail)
    # Aks holda oxirgi ajratgich — kasr ("10 000,00" / "10000.5").
    head_digits = re.sub(r"[.,]", "", head) or "0"
    try:
        return int(round(float(f"{head_digits}.{tail or '0'}")))
    except ValueError:
        return None


# ============================================================
# Karta raqami
# ============================================================

def _card_pattern(value) -> str:
    """Faqat raqam va '?' (yashirin belgi) dan iborat naqsh qaytaradi."""
    out = []
    for ch in str(value or ""):
        if ch.isdigit():
            out.append(ch)
        elif ch in _MASK_CHARS:
            out.append("?")
        elif ch in _SEPARATORS:
            continue
        # boshqa belgilar (harf, nuqta va h.k.) e'tiborga olinmaydi
    return "".join(out)


def card_matches(expected, seen) -> bool:
    """`seen` (chekda ko'ringan karta) `expected` (bizniki) bilan mos keladimi.

    - To'liq raqam: aynan teng bo'lishi shart.
    - Maskalangan (16 belgi): ko'rinib turgan har bir raqam o'z o'rnida mos.
    - Faqat oxirgi raqamlar ("**** 3456"): oxiri mos bo'lishi shart.
    Kamida 4 ta ko'rinadigan raqam bo'lmasa — mos DEB HISOBLANMAYDI.
    """
    exp = "".join(ch for ch in str(expected or "") if ch.isdigit())
    pat = _card_pattern(seen)
    if not exp or not pat:
        return False

    visible = [ch for ch in pat if ch.isdigit()]
    if len(visible) < _MIN_VISIBLE_CARD_DIGITS:
        return False

    if len(pat) == len(exp):
        return all(p == "?" or p == e for p, e in zip(pat, exp))

    # Uzunlik mos emas: faqat "yashirin qism + oxirgi raqamlar" yoki
    # "boshi + yashirin qism" ko'rinishlarini qabul qilamiz.
    stripped_left = pat.lstrip("?")
    if "?" not in stripped_left and len(stripped_left) >= _MIN_VISIBLE_CARD_DIGITS and stripped_left != pat:
        return exp.endswith(stripped_left)
    stripped_right = pat.rstrip("?")
    if "?" not in stripped_right and len(stripped_right) >= _MIN_VISIBLE_CARD_DIGITS and stripped_right != pat:
        return exp.startswith(stripped_right)
    if "?" not in pat:
        # Yashirin belgisiz, lekin qisqa/uzun raqam (masalan faqat "3456").
        return len(pat) >= _MIN_VISIBLE_CARD_DIGITS and exp.endswith(pat)
    return False


# ============================================================
# Karta egasi ismi
# ============================================================

def _name_tokens(value) -> list:
    text = str(value or "").lower()
    for ch in ("'", "`", "’", "ʻ", "ʼ", "‘", "´"):
        text = text.replace(ch, "")
    text = "".join(_CYR_TO_LAT.get(ch, ch) for ch in text)
    return [t for t in re.findall(r"[a-z]+", text) if t]


def _tokens_similar(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _NAME_FUZZY_RATIO


def name_matches(expected, seen) -> bool:
    """Chekdagi ism bizning karta egasi ismiga mos keladimi.

    Qoidalar: chekdagi HAR BIR so'z bizning ismdagi biror so'zga mos bo'lishi
    kerak (bitta harfli so'z — bosh harf, masalan "A." — mos so'zning bosh
    harfi bo'lsa yetarli), va kamida BITTA to'liq so'z (2+ harf) aniq
    (yoki juda o'xshash) mos kelishi shart. So'zlar tartibi ahamiyatsiz.
    """
    exp_tokens = _name_tokens(expected)
    seen_tokens = _name_tokens(seen)
    if not exp_tokens or not seen_tokens:
        return False

    full_matches = 0
    for tok in seen_tokens:
        if len(tok) == 1:
            if not any(e.startswith(tok) for e in exp_tokens):
                return False
        else:
            if not any(_tokens_similar(tok, e) for e in exp_tokens):
                return False
            full_matches += 1
    return full_matches >= 1


# ============================================================
# Umumiy tekshiruv
# ============================================================

def verify_receipt(extracted: dict | None, expected_amount: int, card_number: str,
                   card_holder: str, min_confidence: float = 0.6) -> ReceiptCheck:
    """Chekdan ajratilgan ma'lumotni (extracted) rekvizitlar bilan solishtiradi.
    `extracted` kalitlari: amount, receiver_card, receiver, confidence."""
    extracted = extracted if isinstance(extracted, dict) else {}
    result = ReceiptCheck()

    if not extracted:
        result.notes.append("Chekdan ma'lumot o'qib bo'lmadi.")
        return result

    # --- karta ---
    if not "".join(ch for ch in str(card_number or "") if ch.isdigit()):
        result.notes.append("Karta raqami (PAYMENT_CARD_NUMBER) sozlanmagan.")
    elif not extracted.get("receiver_card"):
        result.notes.append("Chekda qabul qiluvchi karta raqami ko'rinmadi.")
    else:
        result.card = card_matches(card_number, extracted.get("receiver_card"))
        if not result.card:
            result.notes.append("Karta raqami mos kelmadi.")

    # --- ism ---
    if not _name_tokens(card_holder):
        result.notes.append("Karta egasi (PAYMENT_CARD_HOLDER) sozlanmagan.")
    elif not extracted.get("receiver"):
        result.notes.append("Chekda qabul qiluvchi ismi ko'rinmadi.")
    else:
        result.name = name_matches(card_holder, extracted.get("receiver"))
        if not result.name:
            result.notes.append("Karta egasi ismi mos kelmadi.")

    # --- summa ---
    seen_amount = parse_amount(extracted.get("amount"))
    if seen_amount is None:
        result.notes.append("Chekda summa o'qilmadi.")
    else:
        result.amount = (seen_amount == int(expected_amount))
        if not result.amount:
            result.notes.append(f"Summa mos kelmadi (chekda {seen_amount}, kerak {int(expected_amount)}).")

    # --- AI o'qish ishonchliligi ---
    conf = extracted.get("confidence")
    try:
        conf_val = float(conf)
    except (TypeError, ValueError):
        conf_val = None
    if conf_val is None:
        result.confidence_ok = False
        result.notes.append("Chekni o'qish ishonchliligi noma'lum.")
    else:
        result.confidence_ok = conf_val >= float(min_confidence)
        if not result.confidence_ok:
            result.notes.append("Chek yomon o'qildi (ishonchlilik past).")

    result.ok = (
        result.card is True and result.name is True
        and result.amount is True and result.confidence_ok is True
    )
    return result
