package uz.studentai.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan
import android.view.View
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import uz.studentai.mobile.util.AppLog

/** LOG bo'limi: ilovadagi barcha jarayonlar. Yozuvlar foydalanuvchi "Tozalash"ni
 * bosmaguncha saqlanadi. ✅ — ishladi, ❌ — ishlamadi (sababi bilan). */
class LogActivity : AppCompatActivity() {
    private lateinit var text: TextView
    private lateinit var summary: TextView
    private lateinit var scroll: ScrollView
    private var onlyErrors = false
    private lateinit var filterBtn: MaterialButton

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildUi())
    }

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(8), dp(12), dp(12))
        }
        root.addView(MaterialToolbar(this).apply {
            title = "📋 LOG"
            setNavigationIcon(android.R.drawable.ic_menu_close_clear_cancel)
            setNavigationOnClickListener { finish() }
        }, LinearLayout.LayoutParams(-1, dp(56)))

        summary = TextView(this).apply { textSize = 14f; setPadding(dp(4), dp(6), dp(4), dp(6)) }
        root.addView(summary, LinearLayout.LayoutParams(-1, -2))

        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        fun btn(label: String, onClick: () -> Unit) = MaterialButton(this).apply {
            this.text = label
            textSize = 12f
            setOnClickListener { onClick() }
        }.also { row.addView(it, LinearLayout.LayoutParams(0, -2, 1f).apply { setMargins(dp(2), 0, dp(2), 0) }) }

        btn("🔄 Yangilash") { render() }
        filterBtn = btn("❌ Faqat xatolar") {
            onlyErrors = !onlyErrors
            render()
        }
        btn("📋 Nusxa") { copyAll() }
        btn("🗑 Tozalash") { confirmClear() }
        root.addView(row, LinearLayout.LayoutParams(-1, -2))

        text = TextView(this).apply {
            textSize = 13f
            setTextIsSelectable(true)
            setPadding(dp(4), dp(10), dp(4), dp(10))
        }
        scroll = ScrollView(this).apply { addView(text) }
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        return root
    }

    private fun render() {
        val all = AppLog.readAll(this)
        val errors = all.count { it.level == "ERR" }
        val oks = all.count { it.level == "OK" }
        summary.text = "Jami: ${all.size}   ✅ $oks   ❌ $errors   (o'zingiz tozalamaguncha saqlanadi)"
        filterBtn.text = if (onlyErrors) "📄 Hammasi" else "❌ Faqat xatolar"

        val shown = (if (onlyErrors) all.filter { it.level == "ERR" } else all).asReversed().take(400)
        if (shown.isEmpty()) {
            text.text = if (onlyErrors) "Xatolar yo'q 👍" else "Hozircha yozuv yo'q. Ilovadan foydalaning — jarayonlar shu yerda ko'rinadi."
            return
        }
        val sb = SpannableStringBuilder()
        for (e in shown) {
            val (mark, color) = when (e.level) {
                "OK" -> "✅" to Color.parseColor("#4CAF50")
                "ERR" -> "❌" to Color.parseColor("#EF5350")
                else -> "ℹ️" to Color.parseColor("#90A4AE")
            }
            val start = sb.length
            sb.append("${AppLog.formatTime(e.time)}  $mark  [${e.tag}]\n")
            sb.setSpan(ForegroundColorSpan(color), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            sb.setSpan(StyleSpan(Typeface.BOLD), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            sb.append(e.message).append("\n")
            if (e.reason.isNotBlank()) {
                val s2 = sb.length
                sb.append("Sabab: ${e.reason}\n")
                sb.setSpan(ForegroundColorSpan(Color.parseColor("#FFB74D")), s2, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
            sb.append("\n")
        }
        if (all.size > shown.size && !onlyErrors) sb.append("… oldingi yozuvlar \"Nusxa\" orqali to'liq olinadi.")
        text.text = sb
        scroll.scrollTo(0, 0)
    }

    private fun copyAll() {
        val all = AppLog.readAll(this).asReversed()
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("Talaba AI log", AppLog.toPlainText(all)))
        Toast.makeText(this, "Log nusxalandi (${all.size} ta yozuv)", Toast.LENGTH_SHORT).show()
    }

    private fun confirmClear() {
        AlertDialog.Builder(this)
            .setTitle("Logni tozalash")
            .setMessage("Barcha yozuvlar o'chiriladi. Davom etasizmi?")
            .setPositiveButton("O'chirish") { _, _ -> AppLog.clear(this); render() }
            .setNegativeButton("Bekor qilish", null)
            .show()
    }
}
