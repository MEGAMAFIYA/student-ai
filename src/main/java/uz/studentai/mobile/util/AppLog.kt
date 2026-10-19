package uz.studentai.mobile.util

import android.content.Context
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Ilova jurnali (LOG). Yozuvlar telefonning ichki xotirasidagi faylga yoziladi
 * va foydalanuvchi o'zi "Tozalash"ni bosmaguncha saqlanib qoladi (ilova yopilsa,
 * telefon o'chsa ham). Fayl juda kattalashib ketmasligi uchun ~5 MB dan oshsa
 * eng eski yozuvlar o'chiriladi.
 *
 * Format (har qator): millisekund \t DARAJA \t TEG \t xabar \t sabab
 * Daraja: OK (muvaffaqiyat), ERR (xato), INFO (jarayon).
 */
object AppLog {
    private const val FILE_NAME = "app_log.txt"
    private const val MAX_BYTES = 5L * 1024 * 1024
    private val lock = Any()
    @Volatile private var crashInstalled = false

    data class Entry(val time: Long, val level: String, val tag: String, val message: String, val reason: String)

    private fun file(ctx: Context) = File(ctx.applicationContext.filesDir, FILE_NAME)

    private fun clean(s: String?) = (s ?: "").replace("\t", " ").replace("\r", " ").replace("\n", " ⏎ ")

    private fun write(ctx: Context, level: String, tag: String, message: String, reason: String?) {
        try {
            synchronized(lock) {
                val f = file(ctx)
                if (f.exists() && f.length() > MAX_BYTES) {
                    val lines = f.readLines()
                    f.writeText(lines.drop(lines.size / 2).joinToString("\n") + "\n")
                }
                f.appendText("${System.currentTimeMillis()}\t$level\t${clean(tag)}\t${clean(message)}\t${clean(reason)}\n")
            }
        } catch (e: Exception) {
            // Jurnal yozilmasa ham ilova ishlashda davom etadi.
        }
    }

    fun info(ctx: Context, tag: String, message: String) = write(ctx, "INFO", tag, message, null)
    fun ok(ctx: Context, tag: String, message: String) = write(ctx, "OK", tag, message, null)
    fun error(ctx: Context, tag: String, message: String, reason: String?) = write(ctx, "ERR", tag, message, reason)

    /** Istisno (exception)ni tushunarli sabab matniga aylantiradi. */
    fun explain(e: Throwable): String = when (e) {
        is java.net.SocketTimeoutException ->
            "Server javob bermadi (vaqt tugadi). Server uxlab qolgan bo'lishi mumkin (~50 soniya) yoki so'rov juda og'ir."
        is java.net.UnknownHostException ->
            "Server manzili topilmadi: internet yo'q yoki manzil (BASE_URL) noto'g'ri."
        is java.net.ConnectException ->
            "Serverga ulanib bo'lmadi: server o'chiq yoki internet ulanishi yo'q."
        is javax.net.ssl.SSLException ->
            "Xavfsiz ulanish (SSL) xatosi: telefon vaqti/sanasi noto'g'ri yoki tarmoq ulanishni to'sayapti."
        is com.google.gson.JsonParseException ->
            "Server javobi kutilgan formatda emas (ilova va server versiyalari mos emas bo'lishi mumkin)."
        else -> "${e.javaClass.simpleName}: ${e.message ?: "noma'lum xato"}"
    }

    /** HTTP kodni tushunarli sabab matniga aylantiradi. */
    fun explainHttp(code: Int, serverError: String?): String {
        val base = when (code) {
            400 -> "So'rov noto'g'ri (ma'lumotlar yetarli emas yoki xato)."
            401 -> "Sessiya tugagan yoki yaroqsiz — qaytadan kirish kerak."
            403 -> "Ruxsat yo'q."
            404 -> "Manzil serverda topilmadi: server eski versiyada (yangi fayllar deploy qilinmagan) yoki BASE_URL noto'g'ri."
            408 -> "So'rov vaqti tugadi."
            429 -> "Juda ko'p so'rov yuborildi — biroz kutib qayta urinib ko'ring."
            500 -> "Serverda ichki xato (server logiga qarang)."
            502, 503, 504 -> "Server hozir mavjud emas (uxlayapti yoki qayta ishga tushayapti). 1 daqiqadan so'ng qayta urining."
            else -> if (code >= 500) "Server xatosi." else "Kutilmagan javob."
        }
        return if (serverError.isNullOrBlank()) base else "$base Server xabari: $serverError"
    }

    fun readAll(ctx: Context): List<Entry> = synchronized(lock) {
        val f = file(ctx)
        if (!f.exists()) return emptyList()
        f.readLines().mapNotNull { line ->
            val p = line.split("\t")
            if (p.size < 4) null else Entry(
                p[0].toLongOrNull() ?: 0L, p[1], p[2], p[3], p.getOrNull(4).orEmpty()
            )
        }
    }

    fun clear(ctx: Context) {
        synchronized(lock) { try { file(ctx).delete() } catch (e: Exception) { } }
    }

    private val fmt = SimpleDateFormat("dd.MM HH:mm:ss", Locale.US)

    fun formatTime(t: Long): String = fmt.format(Date(t))

    fun toPlainText(entries: List<Entry>): String = entries.joinToString("\n") { e ->
        val mark = when (e.level) { "OK" -> "✅"; "ERR" -> "❌"; else -> "ℹ️" }
        val base = "${formatTime(e.time)} $mark [${e.tag}] ${e.message}"
        if (e.reason.isBlank()) base else "$base\n    Sabab: ${e.reason}"
    }

    /** Kutilmagan qulashlar (crash) ham jurnalga yoziladi. */
    fun installCrashHandler(ctx: Context) {
        if (crashInstalled) return
        crashInstalled = true
        val app = ctx.applicationContext
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            error(app, "Ilova", "Ilova kutilmaganda to'xtadi (crash)", explain(throwable) + " — " +
                (throwable.stackTrace.firstOrNull()?.toString() ?: ""))
            previous?.uncaughtException(thread, throwable)
        }
    }
}
