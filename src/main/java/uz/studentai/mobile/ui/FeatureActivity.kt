package uz.studentai.mobile.ui

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.util.Base64
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.ApiService
import uz.studentai.mobile.network.MobileTaskResponse
import uz.studentai.mobile.network.MobileFileItem
import uz.studentai.mobile.network.MobileFileTaskRequest
import uz.studentai.mobile.network.MobileTaskRequest
import uz.studentai.mobile.util.SessionManager
import java.io.ByteArrayOutputStream

/** Native screen for the remaining bot features.  It deliberately uses the
 * same /api/mobile backend instead of duplicating Telegram handler logic. */
class FeatureActivity : AppCompatActivity() {
    private lateinit var feature: String
    private lateinit var titleText: String
    private lateinit var input: EditText
    private lateinit var output: TextView
    private lateinit var progress: ProgressBar
    private lateinit var action: MaterialButton
    private lateinit var pick: MaterialButton
    private var pagesInput: EditText? = null
    private val picked = mutableListOf<MobileFileItem>()
    private var pendingFileBytes: ByteArray? = null
    private var pendingFileName: String = "student_ai_file"
    private var pendingMime: String = "application/octet-stream"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        feature = intent.getStringExtra(EXTRA_FEATURE).orEmpty()
        titleText = intent.getStringExtra(EXTRA_TITLE) ?: feature
        setContentView(buildUi())
        if (feature in AUTO_FEATURES) sendTask() 
    }

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(10), dp(16), dp(16))
        }
        val toolbar = com.google.android.material.appbar.MaterialToolbar(this).apply {
            title = titleText
            setNavigationIcon(android.R.drawable.ic_menu_close_clear_cancel)
            setNavigationOnClickListener { finish() }
        }
        root.addView(toolbar, LinearLayout.LayoutParams(-1, dp(56)))

        input = EditText(this).apply {
            hint = hintFor(feature)
            minLines = if (feature == "course_work" || feature == "essay" || feature == "summarize" || feature == "translate" || feature == "grammar" || feature == "citation" || feature == "guide") 6 else 3
            gravity = android.view.Gravity.TOP
            setPadding(dp(12), dp(12), dp(12), dp(12))
        }
        if (feature !in AUTO_FEATURES) {
            root.addView(input, LinearLayout.LayoutParams(-1, 0, 1f))
        }

        if (feature in LONG_FEATURES) {
            pagesInput = EditText(this).apply {
                hint = if (feature == "pptx") "Slaydlar soni (3-20)" else "Betlar soni (1-15)"
                inputType = android.text.InputType.TYPE_CLASS_NUMBER
                setText(if (feature == "pptx") "8" else "5")
                setPadding(dp(12), dp(8), dp(12), dp(8))
            }
            root.addView(pagesInput, LinearLayout.LayoutParams(-1, dp(52)))
        }

        pick = MaterialButton(this).apply {
            text = if (feature == "images_pdf") "🖼 Rasmlarni tanlash" else "📎 Fayl tanlash"
            visibility = if (feature in setOf("images_pdf", "edit_pdf", "solve", "translate", "summarize")) View.VISIBLE else View.GONE
            setOnClickListener { chooseFiles(feature == "images_pdf") }
        }
        root.addView(pick, LinearLayout.LayoutParams(-1, dp(52)))

        action = MaterialButton(this).apply {
            text = "Bajarish"
            visibility = if (feature in AUTO_FEATURES) View.GONE else View.VISIBLE
            setOnClickListener { sendTask() }
        }
        root.addView(action, LinearLayout.LayoutParams(-1, dp(52)))

        progress = ProgressBar(this).apply { visibility = View.GONE }
        root.addView(progress, LinearLayout.LayoutParams(-1, dp(42)))

        output = TextView(this).apply {
            textSize = 16f
            setPadding(dp(4), dp(10), dp(4), dp(10))
            setTextIsSelectable(true)
        }
        val scroll = ScrollView(this).apply { addView(output) }
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        return root
    }

    private fun hintFor(f: String): String = when (f) {
        "course_work" -> "Kurs ishi mavzusi..."
        "essay" -> "Referat/Insho mavzusi..."
        "translate" -> "Tarjima qilinadigan matn..."
        "solve" -> "Masalani yozing yoki rasm tanlang..."
        "summarize" -> "Konspekt qilinadigan matn..."
        "grammar" -> "Tekshiriladigan matn..."
        "citation" -> "Manba ma'lumotlari..."
        "guide" -> "Savollarni har qatorda bittadan yozing..."
        "pptx" -> "Taqdimot mavzusi..."
        "qoshiq" -> "Qo'shiq nomi yoki ijrochi..."
        "vid" -> "Video havolasi..."
        "tabrik" -> "Tabrik matni..."
        "pro" -> "Pro tabriknoma matni..."
        "remind" -> "YYYY-MM-DD HH:MM | eslatma matni"
        else -> "So'rovingiz..."
    }

    private fun chooseFiles(multiple: Boolean) {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = if (feature == "images_pdf") "image/*" else if (feature in setOf("edit_pdf", "summarize", "translate")) "application/pdf" else "image/*"
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, multiple)
        }
        startActivityForResult(intent, REQUEST_FILES)
    }

    @Deprecated("Activity Result API migration can be done later; this keeps compatibility with the current project.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_SAVE) {
            val bytes = pendingFileBytes
            if (resultCode == Activity.RESULT_OK && data?.data != null && bytes != null) {
                try {
                    contentResolver.openOutputStream(data.data!!)?.use { it.write(bytes) }
                    output.append("\n\n✅ Fayl saqlandi: $pendingFileName")
                } catch (e: Exception) {
                    output.append("\n\n❌ Faylni saqlab bo'lmadi: ${e.message}")
                }
            }
            pendingFileBytes = null
            return
        }
        if (requestCode != REQUEST_FILES || resultCode != Activity.RESULT_OK || data == null) return
        picked.clear()
        val uris = mutableListOf<Uri>()
        data.clipData?.let { clip -> for (i in 0 until clip.itemCount) uris += clip.getItemAt(i).uri }
        if (uris.isEmpty()) data.data?.let { uris += it }
        uris.take(if (feature == "images_pdf") 12 else 1).forEach { uri ->
            val bytes = readUri(uri) ?: return@forEach
            picked += MobileFileItem(queryName(uri), contentResolver.getType(uri) ?: "application/octet-stream", Base64.encodeToString(bytes, Base64.NO_WRAP))
        }
        output.text = "📎 ${picked.size} ta fayl tanlandi."
    }

    private fun sendTask() {
        val text = input.text?.toString()?.trim().orEmpty()
        if (feature !in AUTO_FEATURES && picked.isEmpty() && text.isEmpty()) return
        progress.visibility = View.VISIBLE
        action.isEnabled = false
        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@FeatureActivity)
                val response = if (picked.isNotEmpty() && feature in setOf("images_pdf", "edit_pdf", "solve", "translate", "summarize")) {
                    api.fileTask(MobileFileTaskRequest(feature, text, picked.toList()))
                } else {
                    val n = pagesInput?.text?.toString()?.trim()?.toIntOrNull()
                    api.task(MobileTaskRequest(
                        feature = feature, text = text,
                        pages = (n ?: 5).coerceIn(1, 15),
                        count = (n ?: 8).coerceIn(3, 20),
                    ))
                }
                if (response.code() == 401) {
                    SessionManager(this@FeatureActivity).clear()
                    startActivity(Intent(this@FeatureActivity, LoginActivity::class.java)); finish(); return@launch
                }
                var body = response.body()
                val jobId = body?.job_id
                if (response.isSuccessful && jobId != null) {
                    output.text = "⏳ Tayyorlanmoqda... Bu bir necha daqiqa olishi mumkin, ilovani yopmang."
                    body = waitForJob(api, jobId)
                }
                if (response.isSuccessful && body != null) {
                    output.text = body.reply ?: body.text ?: body.error ?: "Tayyor."
                    if (!body.file_base64.isNullOrBlank()) saveFile(body.file_base64!!, body.filename ?: "student_ai_file", body.mime ?: "application/octet-stream")
                } else output.text = "❌ Server xatosi: ${response.code()}"
            } catch (e: Exception) {
                output.text = "❌ Tarmoq xatosi. Qaytadan urinib ko'ring.\n\n${e.message ?: ""}"
            } finally {
                progress.visibility = View.GONE
                action.isEnabled = true
            }
        }
    }

    private suspend fun waitForJob(api: ApiService, jobId: String): MobileTaskResponse {
        var failures = 0
        val deadline = System.currentTimeMillis() + 30 * 60 * 1000L
        while (System.currentTimeMillis() < deadline) {
            delay(5000)
            try {
                val r = api.job(jobId)
                if (r.code() == 401) {
                    SessionManager(this).clear()
                    startActivity(Intent(this, LoginActivity::class.java)); finish()
                    return MobileTaskResponse(error = "Sessiya tugadi")
                }
                if (r.code() == 404) return MobileTaskResponse(reply = "❌ Vazifa topilmadi (server qayta ishga tushgan bo'lishi mumkin). Qaytadan urinib ko'ring.")
                val b = r.body()
                if (r.isSuccessful && b != null && b.status != "pending") return b
                failures = 0
            } catch (e: Exception) {
                failures++
                if (failures >= 12) return MobileTaskResponse(reply = "❌ Tarmoq xatosi. Qaytadan urinib ko'ring.")
            }
        }
        return MobileTaskResponse(reply = "❌ Vaqt tugadi. Kichikroq hajm bilan urinib ko'ring.")
    }

    private fun saveFile(base64: String, filename: String, mime: String) {
        pendingFileBytes = Base64.decode(base64, Base64.DEFAULT)
        pendingFileName = filename
        pendingMime = mime
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = mime
            putExtra(Intent.EXTRA_TITLE, filename)
        }
        startActivityForResult(intent, REQUEST_SAVE)
    }

    private fun readUri(uri: Uri): ByteArray? = try {
        contentResolver.openInputStream(uri)?.use { input ->
            val out = ByteArrayOutputStream()
            val buf = ByteArray(8192)
            while (true) { val n = input.read(buf); if (n <= 0) break; out.write(buf, 0, n) }
            out.toByteArray()
        }
    } catch (e: Exception) { null }

    private fun queryName(uri: Uri): String {
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) return c.getString(0)
        }
        return "file"
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    companion object {
        const val EXTRA_FEATURE = "feature"
        const val EXTRA_TITLE = "title"
        private val AUTO_FEATURES = setOf("my", "wallet_balance", "wallet_history", "myfiles")
        private val LONG_FEATURES = setOf("course_work", "essay", "pptx")
        private const val REQUEST_FILES = 1201
        private const val REQUEST_SAVE = 1202
    }
}
