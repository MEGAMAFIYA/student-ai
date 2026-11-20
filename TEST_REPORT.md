# Student AI — PowerPoint Generator QA Report

## 1. Audit scope

Tekshirildi:
- `handlers/pptx_gen.py`
- `pptx_tools.py`
- `slide_images.py`
- `slide_themes.py`
- `slide_export.py`
- `image_clients.py`
- `ai_clients.py`
- `config.py`
- PPTX wizard va professional testlari
- Telegram handler ro‘yxatdan o‘tkazilishi
- mavjud Keyns PPTX faylining barcha 10 slaydi: matn + LibreOffice orqali vizual render

Asosiy namuna: `Keynsning_tovarlar_va_xizmatlar_bozorida_makroiqtisodiy_muvozanat_modeli.pptx`.

## 2. Auditda topilgan asosiy muammolar

1. **Soxta diagramma ma’lumotlari** — eski `_chart()` qiymat bo‘lmasa `[4,7,5,9]` ishlatardi. Bu ilmiy taqdimot uchun noto‘g‘ri.
2. **Layout cheklangan** — eski builder amalda 8 ta layout bilan ishlardi; yangi talabdagi timeline/process/comparison/statistics/infographic kabi semantik variantlar to‘liq yo‘q edi.
3. **Rasm semantikasi zaif** — offline illustration headingga asoslangan umumiy grafiklardan foydalangan va rasmning o‘ziga sarlavha matnini chizgan.
4. **Til muammolari** — tayyor Keyns PPTXda `disposable daromad` va `мультипликатор` kabi aralash terminlar ko‘rindi.
5. **Raqamlash nomuvofiqligi** — yuqori qismda `01 / 08`, pastki qismda esa `2/10` kabi ikki xil hisoblash tizimi mavjud edi.
6. **Manbalar umumiy** — eski yakuniy slaydda bibliografik ma’lumotlar yetarlicha strukturali emas edi.
7. **Kontrast muammosi** — ayrim qorong‘i mavzularda oq kartalar ichida mavzuning och rangli foreground rangi ishlatilishi mumkin edi.

## 3. Amalga oshirilgan tuzatishlar

### Kontent va validatsiya
- `slide_quality.py` qo‘shildi.
- JSON normalize/validatsiya qilinadi.
- Slayd soni tekshiriladi.
- Sarlavha, takroriy sarlavha, chart qiymatlari va jadval tuzilishi tekshiriladi.
- Kirill yozuvi aniqlanadi.
- Layout AI tomonidan noto‘g‘ri yoki takroriy qaytsa, deterministic fallback ishlaydi.
- Diagramma qiymati bo‘lmasa, soxta qiymat yaratish olib tashlandi.

### Layout engine
Qo‘llab-quvvatlanadi:
- title
- text_focus
- two_col
- cards
- full_image
- chart
- table
- timeline
- comparison
- process
- statistics
- infographic
- eski split_right/split_left

### Diagrammalar
- bar
- line
- horizontal/bar-style comparison
- pie-style proportional visualization
- comparison
- Keynschilik xochi
- investitsiya–foiz stavkasi
- multiplikator bosqichlari

**Muhim:** chart data bo‘lmasa, qiymat uydirilmaydi.

### Vizual material
- `slide_images.py` qayta yozildi.
- Rasm ichida avtomatik sarlavha/matn yozish olib tashlandi.
- Iqtisodiyot, AI, biologiya, tarix, ta’lim, tabiat va tibbiyot uchun semantik visual oilalar qo‘shildi.
- Image API ishlamasa deterministic offline fallback saqlanadi.

### Dizayn
- Mavjud 20 ta theme saqlandi.
- Mavjud frame/palette/font variantlari saqlandi.
- Oq kartalarda qorong‘i mavzular uchun kontrastli matn fallback qo‘shildi.
- 16:9 format saqlandi.
- Matn hajmi box o‘lchamiga qarab xavfsiz moslanadi.
- Barcha deck bo‘yicha raqamlash `01 / 10`, `02 / 10`, ... kabi yagona tizimga o‘tkazildi.

### Eksport
- PPTX eksporti tekshirildi.
- LibreOffice orqali PDF eksporti tekshirildi.
- PDF fallback mexanizmi saqlandi.

## 4. Test natijalari

| Test | Kutilgan natija | Natija | Holat |
|---|---|---|---|
| `test_pptx_professional.py` | 20 theme, frame/palette, PDF | 3/3 passed | PASS |
| `test_pptx_quality_gate.py` | Geometry QA | 1/1 passed | PASS |
| PPTX wizard PreviewEngine + font smoke | Preview/cache/font | 17/17 passed | PASS |
| 20 theme build smoke | Har bir theme PPTX ochilishi | 20/20 | PASS |
| 20 theme LibreOffice PDF conversion | Har bir theme PDFga o‘tishi | 20/20 | PASS |
| Keyns-final | 10 slayd + PDF | 10 + PDF | PASS |
| AI-final | 10 slayd + PDF | 10 + PDF | PASS |
| Biology-final | 10 slayd + PDF | 10 + PDF | PASS |
| History-final | 10 slayd + PDF | 10 + PDF | PASS |
| Visual preview render | 4×10 = 40 PNG | 40 | PASS |

### To‘liq test suite holati

`pytest -q` butun loyiha bo‘yicha **PASS emas**. Muhitda slayd generatoridan tashqari mavjud testlar import bosqichida to‘xtadi:

- `google.generativeai` moduli muhitda o‘rnatilmagan.
- `render_api.py` hozirgi muhitdagi `httpx` obyektida `Timeout` atributini topa olmadi.

Bu ikki xato PowerPoint patchidan kelib chiqmagan va boshqa bot modullariga tegmaslik talabi sababli bu auditda ularni yashirincha o‘zgartirish kiritilmadi.

## 5. Tashqi API testi

Real image API testi bajarilmadi, chunki audit muhitida bunday test uchun provider kalitlari bilan real tashqi chaqiruv bajarish sharti yo‘q edi. Offline fallback esa real ishlashda sinovdan o‘tkazildi.

## 6. Namuna taqdimotlari

- `Keyns-final.pptx`
- `AI-final.pptx`
- `Biology-final.pptx`
- `History-final.pptx`

Har biri 8 ta kontent + titul + yakuniy slayddan iborat, jami 10 slayd.

## 7. Qabul mezonlari

PowerPoint generatoriga tegishli qabul mezonlari bo‘yicha: **bajarildi**.

To‘liq loyiha test suite bo‘yicha: **muhitga bog‘liq 3 ta collection error sabab 100% PASS deb e’lon qilinmaydi**.
