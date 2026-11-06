
# Student AI — Professional PowerPoint system

## User flow
`SLAYD TAYYORLASH` → topic → total slide count (5, 7, 10, 12, 15, 20, 25, 30) → one of 20 design previews → frame → palette/custom HEX → font → PPTX/PDF choice.

The selected total includes the cover and closing slide. The AI writes the remaining content slides. Each user’s wizard selections live in that user’s Telegram `context.user_data`.

## Visual system
- 20 separate theme definitions in `slide_themes.py`.
- 10 reusable frame/decorations in `pptx_tools.py`.
- 9 accessible color palettes plus custom background/accent/text HEX.
- Font fallback uses `fc-match` when available and falls back to DejaVu Sans.
- Content layouts include split image/text, reverse split, cards, chart, full image, two columns, text focus, and table.
- `slide_images.py` creates topic-aware offline illustrations, so slides remain visual without image credentials.
- Existing BFL/Stability image providers can be enabled optionally with `SLIDE_IMAGES_ENABLED=1`. This may consume provider credits. `SLIDE_IMAGES_PROVIDER=auto|bfl|stability` optionally chooses the provider. If generation fails, offline illustrations remain in place.

## Export
- PPTX is built locally with the existing `python-pptx` dependency.
- PDF conversion uses LibreOffice/soffice when available; otherwise ReportLab generates a fallback PDF.
- No Gamma, Canva, or other paid design API is required.

## Render deployment
No new Python package is required; current `requirements.txt` already includes `python-pptx`, Pillow, and ReportLab.
Optional image generation is OFF by default. Add `SLIDE_IMAGES_ENABLED=1` only if you deliberately want BFL/Stability image generation and the corresponding provider key is already configured in the bot’s existing key pool.

After deployment, test with a small 5-slide presentation first. The project’s `.env` and local runtime databases are intentionally excluded from the distributable ZIP; configure secrets in Render Environment instead.
