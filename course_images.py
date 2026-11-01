"""
🖼 Kurs ishi uchun MAVZUGA MOS rasmlar (diagramma + AI illyustratsiya).

Jarayon (handlers/course_work.py matn tayyor bo'lgandan KEYIN chaqiradi):
  1. REJA — AI kurs ishi matnini tahlil qilib, qaysi kichik bo'limga qanday
     tasvir kerakligini JSON ko'rinishida belgilaydi (rasm turi, o'zbekcha izoh,
     inglizcha prompt yoki diagramma ma'lumoti).
  2. YARATISH:
       • blok-sxema / arxitektura / taqqoslash / statistik diagramma — ANIQ
         ma'lumotdan matplotlib bilan CHIZILADI (rasm generatoriga ishonilmaydi:
         generatorlar matn va raqamni buzadi). Statistik diagrammada har bir
         qiymat kurs ishi matnida HAQIQATAN uchrashi tekshiriladi — to'qib
         chiqarilgan raqam diagrammaga tushmaydi.
       • mavzuga mos ILLYUSTRATSIYA — Black Forest Labs / Stability AI orqali
         (image_clients.py). Provayder tanlovi + kalitlar orasida aylanish +
         faqat HAQIQIY kalit bor boshqa provayderga avtomatik o'tish.
  3. Natija: figures ro'yxati (fayl yo'li, o'zbekcha izoh, joylashuv) —
     pdf_tools.build_course_work_pdf() ularni tegishli kichik bo'limga qo'yadi.

HECH QACHON kurs ishini to'xtatmaydi: har bir rasm xatosi faqat loglanadi.
Vaqtinchalik fayllar `cwimg_*` papkasida — chaqiruvchi cleanup() qiladi; eskirgan
(1 soatdan eski) qoldiqlar har safar boshlanishda tozalanadi.
"""

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import textwrap
import time
from dataclasses import dataclass, field

import config
import image_clients

logger = logging.getLogger(__name__)

WORKDIR_PREFIX = "cwimg_"
STALE_WORKDIR_SEC = 3600
IMAGES_TOTAL_TIMEOUT_SEC = max(60, int(os.getenv("COURSE_IMAGES_TIMEOUT_SEC", "420")))
PLANNER_EXCERPT_CHARS = 1100
MAX_PROMPT_CHARS = 600
MAX_LABEL_CHARS = 42

_AI_KINDS = {"illustration"}
_DIAGRAM_KINDS = {"flowchart", "architecture", "comparison", "chart"}
_KIND_ALIASES = {
    "illustration": "illustration", "illyustratsiya": "illustration", "infographic": "illustration",
    "infografika": "illustration", "picture": "illustration", "rasm": "illustration", "scene": "illustration",
    "flowchart": "flowchart", "block": "flowchart", "blok-sxema": "flowchart", "blok": "flowchart",
    "block_diagram": "flowchart", "process": "flowchart", "scheme": "flowchart", "sxema": "flowchart",
    "technical": "flowchart", "texnik": "flowchart", "chizma": "flowchart",
    "circuit": "flowchart", "elektr": "flowchart",  # haqiqiy elektr sxema belgilari qo'llab-quvvatlanmaydi
    "architecture": "architecture", "arxitektura": "architecture", "layers": "architecture",
    "comparison": "comparison", "taqqoslash": "comparison", "compare": "comparison",
    "chart": "chart", "diagramma": "chart", "statistik": "chart", "bar": "chart", "statistics": "chart",
}

_PALETTE = {
    "ink": "#1f2937", "muted": "#6b7280", "box": "#e8f0fb", "box_edge": "#2a6fb0",
    "accent": "#2a6fb0", "accent2": "#1a8a6a", "band": "#f3f6fa", "arrow": "#374151",
}

_AI_STYLE_SUFFIX = (
    ", clean modern educational illustration, soft flat colors, uncluttered composition, "
    "no text, no letters, no numbers, no captions, no watermark, no logos"
)


# ============================================================
# Sozlamalarni aniqlash
# ============================================================

def resolve_settings(user_id: int | None) -> dict:
    """Foydalanuvchi tanlovi (storage) + administrator standarti (config)."""
    prefs = {}
    if user_id:
        try:
            import storage
            prefs = storage.get_course_image_prefs(user_id)
        except Exception as e:  # sozlama o'qilmasa ham kurs ishi to'xtamasin
            logger.warning(f"🖼 Foydalanuvchi rasm sozlamalarini o'qib bo'lmadi: {e}")
    return config.sanitize_course_image_settings(prefs, config.COURSE_IMAGE_DEFAULTS)


def _usable_pool(entries) -> list[dict]:
    return [e for e in (entries or []) if isinstance(e, dict) and str(e.get("key", "")).strip()]


def _user_image_pools(user_id: int | None) -> dict:
    if not user_id:
        return {}
    try:
        import user_ai_keys
        return user_ai_keys.get_image_pools(user_id)
    except Exception as e:
        logger.warning(f"🖼 Shaxsiy rasm kalitlarini o'qib bo'lmadi: {e}")
        return {}


def available_providers(user_id: int | None) -> dict[str, int]:
    """{provider: haqiqiy kalitlar soni} — faqat kamida bitta kaliti borlar."""
    user_pools = _user_image_pools(user_id)
    out = {}
    for p in config.IMAGE_PROVIDERS:
        n = len(_usable_pool(user_pools.get(p))) + len(_usable_pool(config.KEY_POOLS.get(p)))
        if n:
            out[p] = n
    return out


def candidate_keys(settings: dict, user_id: int | None) -> list[dict]:
    """Rasm yaratish uchun urinish tartibi: [{provider, key, model, source}, ...].

    • Tanlangan provayder birinchi; "auto" — kalitlari bor provayderlar
      config.IMAGE_PROVIDERS tartibida.
    • Har bir provayder ichida AVVAL foydalanuvchining shaxsiy kalitlari, so'ng
      botning umumiy (admin) kalitlari.
    • BOSHQA provayderga o'tish FAQAT unda haqiqiy (bo'sh bo'lmagan) kalit bo'lsa.
    • Tanlangan model faqat TANLANGAN provayderga qo'llanadi; zaxira provayder
      o'z kalitidagi (yoki standart) modeldan foydalanadi."""
    chosen = settings.get("provider", "auto")
    order = list(config.IMAGE_PROVIDERS)
    if chosen in order:
        order.remove(chosen)
        order.insert(0, chosen)
    user_pools = _user_image_pools(user_id)
    out = []
    for p in order:
        for source, pool in (("user", user_pools.get(p)), ("bot", config.KEY_POOLS.get(p))):
            for entry in _usable_pool(pool):
                model = ""
                if p == chosen and settings.get("model"):
                    model = settings["model"]
                model = image_clients.normalize_model(p, model or entry.get("model") or "") \
                    or config.DEFAULT_MODEL_BY_PROVIDER.get(p, "")
                out.append({"provider": p, "key": entry["key"].strip(), "model": model, "source": source})
    return out


def estimate_job_cost(settings: dict, user_id: int | None, ai_count: int) -> tuple[float | None, str]:
    """Birinchi nomzod model bo'yicha taxminiy xarajat matni."""
    if ai_count <= 0:
        return 0.0, "AI generator ishlatilmaydi (diagrammalar bepul)"
    cands = candidate_keys(settings, user_id)
    if not cands:
        return None, "AI kalit yo'q"
    c = cands[0]
    credits, text = image_clients.estimate_cost(c["provider"], c["model"], ai_count)
    return credits, f"{config.PROVIDER_LABELS.get(c['provider'], c['provider'])} / {c['model']}: {text}"


# ============================================================
# Vaqtinchalik papkalar
# ============================================================

def make_workdir() -> str:
    cleanup_stale()
    return tempfile.mkdtemp(prefix=WORKDIR_PREFIX)


def cleanup(workdir: str | None) -> None:
    if workdir and os.path.basename(workdir).startswith(WORKDIR_PREFIX):
        shutil.rmtree(workdir, ignore_errors=True)


def cleanup_stale(max_age_sec: int = STALE_WORKDIR_SEC) -> int:
    """Crash/uzilish tufayli qolib ketgan eski cwimg_* papkalarni o'chiradi."""
    removed = 0
    base = tempfile.gettempdir()
    try:
        names = os.listdir(base)
    except OSError:
        return 0
    now = time.time()
    for name in names:
        if not name.startswith(WORKDIR_PREFIX):
            continue
        path = os.path.join(base, name)
        try:
            if os.path.isdir(path) and now - os.path.getmtime(path) > max_age_sec:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


# ============================================================
# REJA: AI dan JSON olish va TEKSHIRISH
# ============================================================

@dataclass
class FigureSpec:
    kind: str
    caption: str
    bob: int            # 0-based bob indeksi
    sub: int            # 0-based kichik bo'lim indeksi
    section_id: str     # "1.2"
    prompt: str = ""    # AI illyustratsiya uchun (inglizcha)
    data: dict = field(default_factory=dict)  # diagramma ma'lumoti


def _extract_json(raw: str) -> dict | None:
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?\s*|```\s*$", "", raw.strip(), flags=re.MULTILINE).strip()
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _clean_label(value, limit: int = MAX_LABEL_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip("\"'`*#")
    return (text[: limit - 1] + "…") if len(text) > limit else text


def _num_variants(v: float) -> set[str]:
    out = {f"{v:g}", str(v)}
    if float(v).is_integer():
        out.add(str(int(v)))
    out |= {x.replace(".", ",") for x in list(out)}
    return {x for x in out if x}


def _number_in_text(value: float, text: str) -> bool:
    for variant in _num_variants(value):
        if re.search(rf"(?<![\d.,]){re.escape(variant)}(?![\d]|[.,]\d)", text):
            return True
    return False


def _normalize_flowchart(item: dict) -> dict | None:
    nodes = [_clean_label(n) for n in (item.get("nodes") or [])]
    nodes = [n for n in nodes if n][:9]
    if len(nodes) < 2:
        return None
    edges = []
    for e in item.get("edges") or []:
        if not isinstance(e, (list, tuple)) or len(e) != 2:
            continue
        pair = []
        for end in e:
            if isinstance(end, int) and 0 <= end < len(nodes):
                pair.append(end)
            elif isinstance(end, str) and end.strip().isdigit() and 0 <= int(end) < len(nodes):
                pair.append(int(end))
            elif isinstance(end, str) and _clean_label(end) in nodes:
                pair.append(nodes.index(_clean_label(end)))
        if len(pair) == 2 and pair[0] != pair[1] and pair not in edges:
            edges.append(pair)
    if not edges:  # ketma-ket zanjir
        edges = [[i, i + 1] for i in range(len(nodes) - 1)]
    return {"nodes": nodes, "edges": edges}


def _normalize_groups(item: dict, key: str, items_key: str = "items", max_groups: int = 5) -> list | None:
    groups = []
    for g in (item.get(key) or [])[:max_groups]:
        if not isinstance(g, dict):
            continue
        name = _clean_label(g.get("name"), 36)
        items = [_clean_label(x) for x in (g.get(items_key) or [])]
        items = [x for x in items if x][:5]
        if name and items:
            groups.append({"name": name, "items": items})
    return groups if len(groups) >= 2 else None


def _normalize_chart(item: dict, section_text: str) -> dict | None:
    """Statistik diagramma: qiymatlar kurs ishi matnida AYNAN uchrashi shart."""
    chart = item.get("chart") if isinstance(item.get("chart"), dict) else item
    labels = [_clean_label(x, 28) for x in (chart.get("labels") or [])]
    raw_vals = chart.get("values") or []
    values = []
    for v in raw_vals:
        try:
            values.append(float(str(v).replace(",", ".").replace("%", "").strip()))
        except (TypeError, ValueError):
            values.append(None)
    pairs = [(l, v) for l, v in zip(labels, values) if l and v is not None]
    if len(pairs) < 2 or len(pairs) > 8:
        return None
    if not all(_number_in_text(v, section_text) for _, v in pairs):
        logger.info("🖼 Diagramma qiymatlari bo'lim matnida topilmadi — to'qib chiqarilgan raqam bo'lishi mumkin, tashlandi.")
        return None
    return {
        "labels": [l for l, _ in pairs], "values": [v for _, v in pairs],
        "unit": _clean_label(chart.get("unit"), 12), "title": _clean_label(chart.get("chart_title") or chart.get("title"), 60),
    }


def validate_plan(raw_plan: dict | None, sub_index: dict, sub_texts: dict, count: int, ai_available: bool) -> list[FigureSpec]:
    """AI rejasini xavfsiz FigureSpec ro'yxatiga aylantiradi.
    sub_index: {"1.2": (bob_i, sub_j)}, sub_texts: {"1.2": to'liq matn}."""
    specs: list[FigureSpec] = []
    if not raw_plan:
        return specs
    items = raw_plan.get("figures")
    if not isinstance(items, list):
        return specs
    used_sections, used_prompts = set(), set()
    for item in items:
        if not isinstance(item, dict) or len(specs) >= count:
            continue
        sid = str(item.get("section", "")).strip().rstrip(".")
        if sid not in sub_index or sid in used_sections:
            continue
        kind = _KIND_ALIASES.get(str(item.get("kind", "")).strip().lower())
        caption = _clean_label(item.get("caption"), 140).rstrip(".")
        if not kind or not caption:
            continue
        # "N-rasm." prefiksini AI qo'shgan bo'lsa olib tashlaymiz — raqamni PDF qo'yadi.
        caption = re.sub(r"^\s*\d+\s*-?\s*rasm[.:]?\s*", "", caption, flags=re.IGNORECASE).strip() or caption
        bob, sub = sub_index[sid]
        data: dict | None = {}
        prompt = ""
        if kind == "illustration":
            if not ai_available:
                continue
            prompt = re.sub(r"\s+", " ", str(item.get("prompt", ""))).strip()[:MAX_PROMPT_CHARS]
            norm = prompt.lower()
            if len(prompt) < 20 or norm in used_prompts:
                continue
            used_prompts.add(norm)
        elif kind == "flowchart":
            data = _normalize_flowchart(item)
        elif kind == "architecture":
            data = {"layers": _normalize_groups(item, "layers")}
            data = data if data["layers"] else None
        elif kind == "comparison":
            data = {"columns": _normalize_groups(item, "columns", max_groups=3)}
            data = data if data["columns"] else None
        elif kind == "chart":
            data = _normalize_chart(item, sub_texts.get(sid, ""))
        if data is None:
            continue
        used_sections.add(sid)
        specs.append(FigureSpec(kind=kind, caption=caption, bob=bob, sub=sub, section_id=sid, prompt=prompt, data=data))
    specs.sort(key=lambda s: (s.bob, s.sub))
    return specs


_PLANNER_SYSTEM = (
    "Siz ilmiy hujjatlar uchun tasvir rejalashtiruvchisiz. FAQAT JSON qaytaring, boshqa hech narsa yozmang."
)


def build_planner_prompt(topic: str, subs: list[dict], count: int, ai_available: bool) -> str:
    sections_txt = "\n\n".join(
        f"[{s['id']}] {s['title']}\n{s['text'][:PLANNER_EXCERPT_CHARS]}" for s in subs
    )
    ai_note = (
        '"illustration" — mavzuga mos illyustratsiya (AI rasm generatori; "prompt" maydoni INGLIZCHA, '
        "sahna/obyektlarni tasvirlaydi, matn/raqam/yozuv so'ramaydi)"
        if ai_available else
        "AI rasm generatori MAVJUD EMAS — 'illustration' turini ishlatmang"
    )
    return (
        f"Kurs ishi mavzusi: «{topic}».\n"
        f"Quyida uning kichik bo'limlari (qisqartirilgan). Eng ko'pi bilan {count} ta tasvir rejalashtiring: "
        "faqat mazmunga HAQIQATAN yordam beradigan (bezak emas), turli bob/bo'limlarga taqsimlangan, "
        "har bir kichik bo'limga bittadan ko'p emas.\n\n"
        "Ruxsat etilgan turlar (\"kind\"):\n"
        "- flowchart — jarayon/blok-sxema: {\"nodes\": [\"...\"], \"edges\": [[0,1],[1,2]]} (3-8 ta qisqa tugun)\n"
        "- architecture — qatlamli arxitektura: {\"layers\": [{\"name\": \"...\", \"items\": [\"...\"]}]} (2-5 qatlam, qatlamda 1-5 element)\n"
        "- comparison — taqqoslash: {\"columns\": [{\"name\": \"...\", \"items\": [\"...\"]}]} (2-3 ustun)\n"
        "- chart — statistik diagramma: {\"labels\": [...], \"values\": [...], \"unit\": \"%\", \"chart_title\": \"...\"}. "
        "FAQAT bo'lim matnida AYNAN yozilgan raqamlar bo'lsa ishlating; raqamni O'YLAB TOPMANG.\n"
        f"- {ai_note}\n\n"
        "Har bir tasvir uchun \"caption\" — O'ZBEK tilida qisqa izoh (raqamsiz, 'rasm' so'zisiz, nuqtasiz), "
        "bo'lim mazmuniga mos. Tugun/qatlam/ustun nomlari ham o'zbekcha.\n"
        "Javob FAQAT shu JSON:\n"
        '{"figures": [{"section": "1.2", "kind": "flowchart", "caption": "...", "nodes": [...], "edges": [...]}, ...]}\n\n'
        f"BO'LIMLAR:\n{sections_txt}"
    )


def collect_subsections(sections: dict) -> tuple[list[dict], dict, dict]:
    """sections["bobs"][i]["subsections"] dan [{id,title,text}], index, matnlar."""
    subs, index, texts = [], {}, {}
    for bi, bob in enumerate(sections.get("bobs", [])):
        for sj, sub in enumerate(bob.get("subsections", [])):
            heading = str(sub.get("heading", "")).strip()
            m = re.match(r"^(\d+\.\d+)\.?\s*(.*)$", heading)
            if not m:
                continue
            sid, title = m.group(1), m.group(2) or heading
            content = str(sub.get("content", ""))
            if len(content.split()) < 40:  # bo'sh/juda qisqa bo'limga rasm qo'yilmaydi
                continue
            subs.append({"id": sid, "title": title, "text": content})
            index[sid] = (bi, sj)
            texts[sid] = content
    return subs, index, texts


async def plan_figures(topic: str, sections: dict, count: int, ai_available: bool, ask_fn) -> list[FigureSpec]:
    """ask_fn(prompt, system, tag) -> str | None (course_work._ask_retry o'rami)."""
    subs, index, texts = collect_subsections(sections)
    if not subs:
        return []
    raw = await ask_fn(build_planner_prompt(topic, subs, count, ai_available), _PLANNER_SYSTEM, "RASM-REJA")
    plan = _extract_json(raw or "")
    specs = validate_plan(plan, index, texts, count, ai_available)
    logger.info(f"🖼 Rasm rejasi: so'ralgan {count}, tasdiqlangan {len(specs)} ({[s.kind for s in specs]}).")
    return specs


# ============================================================
# DIAGRAMMALAR (matplotlib, Agg — tarmoqsiz va kalitsiz)
# ============================================================

def _new_figure(width_in: float, height_in: float):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(width_in, height_in), dpi=200, facecolor="white")
    FigureCanvasAgg(fig)
    return fig


def _save_fig(fig, path: str) -> None:
    fig.savefig(path, format="png", facecolor="white")


def _wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width, break_long_words=True)) or text


def _layers_of(n: int, edges: list) -> list[int]:
    """Eng uzun yo'l bo'yicha qatlam (siklga chidamli: orqaga qaytuvchi qirralar e'tiborsiz)."""
    adj = {i: [] for i in range(n)}
    for a, b in edges:
        adj[a].append(b)
    state, order = {}, []

    def dfs(u):
        state[u] = 1
        for v in adj[u]:
            if state.get(v) is None:
                dfs(v)
        state[u] = 2
        order.append(u)

    indeg = {i: 0 for i in range(n)}
    for a, b in edges:
        indeg[b] += 1
    for start in [i for i in range(n) if indeg[i] == 0] + list(range(n)):
        if state.get(start) is None:
            dfs(start)
    topo = list(reversed(order))
    pos = {u: i for i, u in enumerate(topo)}
    layer = {i: 0 for i in range(n)}
    for u in topo:
        for v in adj[u]:
            if pos[v] > pos[u]:  # orqaga qaytuvchi qirra (sikl) hisobga olinmaydi
                layer[v] = max(layer[v], layer[u] + 1)
    return [layer[i] for i in range(n)]


def render_flowchart(nodes: list[str], edges: list[list[int]], path: str) -> None:
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    n = len(nodes)
    layer = _layers_of(n, edges)
    n_layers = max(layer) + 1
    by_layer: dict[int, list[int]] = {}
    for i, l in enumerate(layer):
        by_layer.setdefault(l, []).append(i)
    max_in_layer = max(len(v) for v in by_layer.values())

    horizontal = n_layers <= 4 or max_in_layer > 3
    W, H = 10.0, 5.4
    fig = _new_figure(W, H)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    if horizontal:
        box_w = min(2.3, (W - 0.6) / n_layers - 0.45)
        box_h = 0.95
        wrap_w = max(10, int(box_w * 7.2))
    else:
        box_w, box_h = 3.4, 0.62
        wrap_w = 34
    centers = {}
    for l, members in by_layer.items():
        for k, i in enumerate(members):
            if horizontal:
                x = (l + 0.5) * (W / n_layers)
                y = H * (k + 1) / (len(members) + 1)
                y = H - y  # birinchi element tepada
            else:
                x = W * (k + 1) / (len(members) + 1)
                y = H - (l + 0.5) * (H / n_layers)
            centers[i] = (x, y)

    fs = 11 if n <= 6 else 9.5
    for i, label in enumerate(nodes):
        x, y = centers[i]
        patch = FancyBboxPatch((x - box_w / 2, y - box_h / 2), box_w, box_h,
                               boxstyle="round,pad=0.02,rounding_size=0.12",
                               linewidth=1.6, edgecolor=_PALETTE["box_edge"], facecolor=_PALETTE["box"])
        ax.add_patch(patch)
        ax.text(x, y, _wrap(label, wrap_w), ha="center", va="center", fontsize=fs, color=_PALETTE["ink"], linespacing=1.15)

    for a, b in edges:
        (x1, y1), (x2, y2) = centers[a], centers[b]
        if horizontal and x2 > x1:
            start, end = (x1 + box_w / 2, y1), (x2 - box_w / 2, y2)
        elif not horizontal and y2 < y1:
            start, end = (x1, y1 - box_h / 2), (x2, y2 + box_h / 2)
        else:  # orqaga yoki bir qatlam ichida
            start, end = (x1, y1), (x2, y2)
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=16, linewidth=1.5,
                                     color=_PALETTE["arrow"], shrinkA=2, shrinkB=2,
                                     connectionstyle="arc3,rad=0.0" if start != (x1, y1) else "arc3,rad=0.25"))
    _save_fig(fig, path)


def render_layers(layers: list[dict], path: str) -> None:
    """Arxitektura: yuqoridan pastga qatlam-bantlar, ichida elementlar, qatlamlar orasida o'q."""
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    n = len(layers)
    W, H = 10.0, max(4.2, 1.35 * n + 0.6)
    fig = _new_figure(W, H)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    band_h = (H - 0.4) / n
    for li, layer in enumerate(layers):
        top = H - 0.2 - li * band_h
        y0 = top - band_h + 0.18
        ax.add_patch(FancyBboxPatch((0.2, y0), W - 0.4, band_h - 0.36, boxstyle="round,pad=0.02,rounding_size=0.1",
                                    linewidth=1.2, edgecolor="#cbd5e1", facecolor=_PALETTE["band"]))
        ax.text(0.4, y0 + (band_h - 0.36) - 0.14, layer["name"], ha="left", va="top", fontsize=10.5,
                fontweight="bold", color=_PALETTE["accent"])
        items = layer["items"]
        k = len(items)
        slot = (W - 1.0) / k
        bw = min(2.6, slot - 0.25)
        for j, item in enumerate(items):
            cx = 0.5 + slot * (j + 0.5)
            cy = y0 + (band_h - 0.36) / 2 - 0.2
            ax.add_patch(FancyBboxPatch((cx - bw / 2, cy - 0.3), bw, 0.6, boxstyle="round,pad=0.02,rounding_size=0.1",
                                        linewidth=1.4, edgecolor=_PALETTE["box_edge"], facecolor=_PALETTE["box"]))
            ax.text(cx, cy, _wrap(item, max(10, int(bw * 7))), ha="center", va="center", fontsize=9.5,
                    color=_PALETTE["ink"], linespacing=1.1)
        if li < n - 1:
            ax.add_patch(FancyArrowPatch((W / 2, y0 + 0.02), (W / 2, y0 - 0.2), arrowstyle="-|>", mutation_scale=14,
                                         linewidth=1.5, color=_PALETTE["arrow"]))
    _save_fig(fig, path)


def render_comparison(columns: list[dict], path: str) -> None:
    from matplotlib.patches import FancyBboxPatch
    k = len(columns)
    max_items = max(len(c["items"]) for c in columns)
    W, H = 10.0, max(4.0, 0.95 + 0.78 * max_items + 0.5)
    fig = _new_figure(W, H)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    gap = 0.3
    cw = (W - 0.4 - gap * (k - 1)) / k
    colors = [_PALETTE["accent"], _PALETTE["accent2"], "#9a5b13"]
    for ci, col in enumerate(columns):
        x0 = 0.2 + ci * (cw + gap)
        color = colors[ci % len(colors)]
        ax.add_patch(FancyBboxPatch((x0, H - 0.95), cw, 0.7, boxstyle="round,pad=0.02,rounding_size=0.1",
                                    linewidth=0, facecolor=color))
        ax.text(x0 + cw / 2, H - 0.6, _wrap(col["name"], max(12, int(cw * 4.5))), ha="center", va="center",
                fontsize=11.5, fontweight="bold", color="white")
        for ri, item in enumerate(col["items"]):
            y = H - 1.05 - (ri + 1) * 0.78 + 0.1
            ax.add_patch(FancyBboxPatch((x0, y), cw, 0.66, boxstyle="round,pad=0.02,rounding_size=0.08",
                                        linewidth=1.2, edgecolor=color, facecolor=_PALETTE["band"]))
            ax.text(x0 + cw / 2, y + 0.33, _wrap(item, max(12, int(cw * 5.2))), ha="center", va="center",
                    fontsize=9.5, color=_PALETTE["ink"], linespacing=1.1)
    _save_fig(fig, path)


def render_chart(labels: list[str], values: list[float], unit: str, title: str, path: str) -> None:
    fig = _new_figure(9.0, 4.8)
    ax = fig.add_axes([0.09, 0.2, 0.88, 0.66 if title else 0.74])
    colors = [_PALETTE["accent"] if i % 2 == 0 else _PALETTE["accent2"] for i in range(len(values))]
    bars = ax.bar(range(len(values)), values, color=colors, width=0.62)
    ax.set_xticks(range(len(values)))
    ax.set_xticklabels([_wrap(l, 14) for l in labels], fontsize=9.5, color=_PALETTE["ink"])
    ax.tick_params(axis="y", labelsize=9, colors=_PALETTE["muted"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")
    ax.yaxis.grid(True, color="#e5e7eb", linewidth=0.8)
    ax.set_axisbelow(True)
    top = max(values) if max(values) > 0 else 1
    ax.set_ylim(0, top * 1.18)
    for bar, v in zip(bars, values):
        shown = f"{v:g}".replace(".", ",") + (f" {unit}" if unit else "")
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + top * 0.02, shown, ha="center", va="bottom",
                fontsize=9.5, color=_PALETTE["ink"], fontweight="bold")
    if unit:
        ax.set_ylabel(unit, fontsize=9.5, color=_PALETTE["muted"])
    if title:
        fig.text(0.5, 0.94, title, ha="center", va="top", fontsize=11.5, fontweight="bold", color=_PALETTE["ink"])
    _save_fig(fig, path)


def render_diagram(spec: FigureSpec, path: str) -> None:
    d = spec.data
    if spec.kind == "flowchart":
        render_flowchart(d["nodes"], d["edges"], path)
    elif spec.kind == "architecture":
        render_layers(d["layers"], path)
    elif spec.kind == "comparison":
        render_comparison(d["columns"], path)
    elif spec.kind == "chart":
        render_chart(d["labels"], d["values"], d.get("unit", ""), d.get("title", ""), path)
    else:
        raise ValueError(f"Noma'lum diagramma turi: {spec.kind}")


# ============================================================
# AI illyustratsiya
# ============================================================

def _to_pdf_friendly_jpeg(data: bytes, path: str, max_px: int = 1600) -> bool:
    """Yuklangan baytlarni tekshiradi (haqiqiy rasm ekanini), RGB JPEG ga o'tkazadi va
    PDF hajmini kamaytirish uchun kichraytiradi."""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        logger.warning(f"🖼 Qaytgan baytlar rasm emas yoki buzilgan: {type(e).__name__}")
        return False
    img = img.convert("RGB")
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px))
    img.save(path, format="JPEG", quality=88, optimize=True)
    return True


async def _generate_illustration(spec: FigureSpec, candidates: list[dict], bad: set, path: str) -> dict | None:
    prompt = spec.prompt.rstrip(". ") + _AI_STYLE_SUFFIX
    skip_providers: set[str] = set()
    for c in candidates:
        ident = (c["provider"], hashlib.sha1(c["key"].encode()).hexdigest()[:10])
        if ident in bad or c["provider"] in skip_providers:
            continue
        res = await image_clients.generate_image(c["provider"], c["key"], c["model"], prompt, aspect="16:9")
        if res.ok:
            ok = await asyncio.to_thread(_to_pdf_friendly_jpeg, res.data, path)
            if ok:
                return {"source": f"ai:{c['provider']}/{c['model']}", "cost": res.cost_credits}
            skip_providers.add(c["provider"])
            continue
        logger.warning(f"🖼 [{spec.section_id}] {c['provider']}/{c['model']} ({c['source']} kalit) ishlamadi: "
                       f"{res.status} — {res.detail}")
        if res.status in ("invalid", "no_balance", "quota", "timeout", "error"):
            bad.add(ident)  # shu kalitni bu kurs ishi davomida qayta urmaymiz
        elif res.status in ("moderated", "bad_request", "model_not_found"):
            skip_providers.add(c["provider"])  # shu prompt/model bilan boshqa kalit ham yordam bermaydi
    return None


# ============================================================
# Asosiy kirish nuqtasi
# ============================================================

async def build_figures(topic: str, sections: dict, settings: dict, user_id: int | None,
                        ask_fn, workdir: str, status_cb=None) -> list[dict]:
    """Rasm rejasini tuzadi va rasmlarni yaratadi. Qaytaradi:
    [{"path","caption","bob","sub","kind","source"}] (hujjat tartibida).
    HECH QACHON istisno ko'tarmaydi."""
    try:
        return await asyncio.wait_for(
            _build_figures_impl(topic, sections, settings, user_id, ask_fn, workdir, status_cb),
            timeout=IMAGES_TOTAL_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error(f"🖼 Rasmlar {IMAGES_TOTAL_TIMEOUT_SEC}s ichida tayyor bo'lmadi — rasmlarsiz davom etiladi.")
    except Exception as e:
        logger.error(f"🖼 Rasm bosqichida kutilmagan xato: {type(e).__name__}: {e}", exc_info=True)
    return []


async def _build_figures_impl(topic, sections, settings, user_id, ask_fn, workdir, status_cb) -> list[dict]:
    count = int(settings.get("count", 3))
    candidates = candidate_keys(settings, user_id)
    ai_available = bool(candidates)

    specs = await plan_figures(topic, sections, count, ai_available, ask_fn)
    if not specs:
        logger.info("🖼 Rasm rejasi bo'sh — rasmlarsiz davom etiladi.")
        return []

    n_ai = sum(1 for s in specs if s.kind in _AI_KINDS)
    _, cost_txt = estimate_job_cost(settings, user_id, n_ai)
    logger.info(f"🖼 Rasmlar: {len(specs) - n_ai} diagramma + {n_ai} AI illyustratsiya. Taxminiy xarajat: {cost_txt}.")
    if status_cb:
        await status_cb(
            f"⏳ *{topic}* — rasmlar tayyorlanmoqda ({len(specs) - n_ai} diagramma, {n_ai} AI illyustratsiya; "
            f"taxminiy xarajat: {cost_txt})..."
        )

    bad_keys: set = set()
    results: list[dict | None] = [None] * len(specs)

    async def one(i: int, spec: FigureSpec):
        base = f"fig{i + 1}_{spec.section_id.replace('.', '_')}"
        try:
            if spec.kind in _AI_KINDS:
                path = os.path.join(workdir, base + ".jpg")
                info = await _generate_illustration(spec, candidates, bad_keys, path)
                if not info:
                    return
            else:
                path = os.path.join(workdir, base + ".png")
                await asyncio.to_thread(render_diagram, spec, path)
                info = {"source": "diagram:matplotlib", "cost": 0}
            if os.path.exists(path) and os.path.getsize(path) > 500:
                results[i] = {"path": path, "caption": spec.caption, "bob": spec.bob, "sub": spec.sub,
                              "kind": spec.kind, "source": info["source"], "section": spec.section_id}
        except Exception as e:
            logger.warning(f"🖼 [{spec.section_id}] {spec.kind} yaratilmadi: {type(e).__name__}: {e}")

    # Diagrammalar tezkor/bepul; AI chaqiruvlari image_clients ichidagi semaphore bilan cheklanadi.
    await asyncio.gather(*(one(i, s) for i, s in enumerate(specs)))
    figures = [r for r in results if r]
    figures.sort(key=lambda f: (f["bob"], f["sub"]))
    logger.info(f"🖼 Tayyor rasmlar: {len(figures)}/{len(specs)} ({[f['source'] for f in figures]}).")
    return figures
