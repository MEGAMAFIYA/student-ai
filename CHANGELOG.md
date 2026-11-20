# Student AI — PowerPoint Generator Changelog

## Professional generator audit — 2026-11-20

### O‘zgartirilgan
- `pptx_tools.py`
  - content-aware layout engine
  - safe chart rendering
  - Keynschilik xochi / investitsiya / multiplikator grafiklari
  - unified slide numbering
  - text-fit heuristics
  - dark-theme card contrast
  - timeline/process/comparison/statistics/infographic layouts
- `slide_images.py`
  - semantic offline illustrations
  - image text removal
  - topic-specific visuals
- `handlers/pptx_gen.py`
  - AI JSON validation
  - smart layout selection
  - source/reference cleaning
  - safe fallback for invalid AI content
- `slide_quality.py`
  - new content validation and layout-selection layer
- `slide_qa.py`
  - static PPTX geometry/text QA
- `tests/test_pptx_quality_gate.py`
  - new regression test
- `sample_decks.py`
  - four real sample presentations

### Saqlangan
- Telegram handler architecture
- payment/balance flow
- AI provider architecture
- existing 20 themes
- frames, palettes and fonts
- PPTX/PDF export interfaces
- cancellation/wizard flow
- optional external image API integration

### Xavfsizlik
- `.env` final ZIPga kiritilmaydi.
- `.env.example` zarur konfiguratsiya kalitlari nomlari bilan yaratildi.
- Maxfiy kalitlar source codega qo‘shilmadi.
