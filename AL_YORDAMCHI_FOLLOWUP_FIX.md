# AL Yordamchi follow-up fix

- AL Business javoblari endi `feature_guard.is_user_busy()` bilan bloklanmaydi. Egasi boshqa tugallanmagan bot funksiyasida bo'lsa ham AL peer xabariga javob beradi.
- API key `config.KEY_POOLS` ichiga bir marta qo'shiladi; takroriy kiritishda dublikat yozilmaydi.
- Agar tanlangan providerning umumiy KEY_POOLS'ida kalit allaqachon bo'lsa, AL keyni qayta so'ramaydi — faqat model tanlatadi.
- API key saqlangani haqida muvaffaqiyat xabari faqat Upstash, Neon yoki GitHub kabi doimiy backendga yozish muvaffaqiyatli bo'lganda beriladi. Faqat Render lokal diskiga yozish deploydan keyin saqlanishini kafolatlamaydi.
- API key va model egasining `al_yordamchi` sozlamalarida emas, umumiy `runtime_ai_config.json` / `KEY_POOLS` ichida saqlanadi; egaga tegishli provider/model tanlovi shaxsiy sozlama faylida qoladi.
