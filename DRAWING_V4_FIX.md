# Drawing Duel V4 — AI evaluation and sharing fixes

## What changed

- AI evaluation no longer blocks `POST /api/draw/submit`.
  The image is accepted immediately and Gemini evaluation runs in the background.
- The Mini App polls the room status and shows the score/comment when ready.
- Added `/api/draw/share`: every share click creates a fresh Telegram PreparedInlineMessage.
  The same drawing can therefore be shared repeatedly to different chats.
- The Mini App no longer closes after a successful share.
- Added a separate `📤 Rasmni yuborish` / `🏆 Natija bilan yuborish` button.
- Drawing judge now evaluates:
  - 55% task completion / required object
  - 20% useful extra details
  - 15% beauty and composition
  - 10% creativity
- If the required object is missing, the score is capped at 49 even if the drawing is beautiful.
- If the required object is present, relevant extras (for example mountains + tulips/houses/trees) can increase the score and are not penalized.
- Added public state fields for AI status, detail and comment.
- Added background evaluation/share logging.

## Example

Task: `⛰️ Tog'`

A drawing containing a recognizable mountain plus tulips, houses, trees, clouds or a sunset can score higher than a bare mountain because relevant additions are considered for composition, detail and creativity.

## Security

No API keys or `.env` values are included in this archive.
