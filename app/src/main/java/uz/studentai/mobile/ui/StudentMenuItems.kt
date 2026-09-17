package uz.studentai.mobile.ui

/**
 * Botdagi handlers/menu.py > MENU_CALLBACKS bilan BIR XIL tartib va
 * nomlar (+ tepasida UNIVERSAL CHAT, xuddi botdagi /start xabari kabi).
 * `implemented = true` bo'lganlar (universal chat, quiz) 1-bosqichda
 * to'liq ishlaydi; qolganlari 2, 3-bosqichda ANA SHU tartibda ulanadi.
 */
data class StudentMenuItem(val key: String, val label: String, val implemented: Boolean)

val STUDENT_MENU_ITEMS = listOf(
    StudentMenuItem("universal", "\uD83D\uDCAC UNIVERSAL CHAT", true),
    StudentMenuItem("course_work", "\uD83D\uDCD8 Kurs ishi / loyiha", false),
    StudentMenuItem("essay", "\uD83D\uDDD2 Referat/Insho", false),
    StudentMenuItem("translate", "\uD83C\uDF10 Tarjima qilish", false),
    StudentMenuItem("pptx", "\uD83D\uDCCA Taqdimot (PPTX)", false),
    StudentMenuItem("quiz", "\uD83D\uDCCB Test/Viktorina", true),
    StudentMenuItem("solve", "\uD83E\uDDEE Masala yechish", false),
    StudentMenuItem("summarize", "\uD83D\uDCD1 Konspekt qisqartirish", false),
    StudentMenuItem("grammar", "\u2705 Imlo tekshirish", false),
    StudentMenuItem("citation", "\uD83D\uDCDA Iqtibos generatori", false),
    StudentMenuItem("images_pdf", "\uD83D\uDDBC Suratlarni PDF qilish", false),
    StudentMenuItem("edit_pdf", "\uD83D\uDCDD PDF ni tahrirlash", false),
    StudentMenuItem("guide", "\uD83D\uDCD6 Qo'llanma tayyorlash", false),
    StudentMenuItem("myfiles", "\uD83D\uDDC2 Mening fayllarim", false),
    StudentMenuItem("remind", "\u23F0 Eslatmalar", false),
)
