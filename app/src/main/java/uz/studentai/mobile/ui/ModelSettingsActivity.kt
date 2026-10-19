package uz.studentai.mobile.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.DocumentsContract
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uz.studentai.mobile.databinding.ActivityModelSettingsBinding
import java.io.File
import java.io.FileOutputStream

/** Selects a model folder using Android's Storage Access Framework (no broad storage permission). */
class ModelSettingsActivity : AppCompatActivity() {
    private lateinit var binding: ActivityModelSettingsBinding
    private val prefs by lazy { getSharedPreferences("offline_model", MODE_PRIVATE) }

    private val folderPicker = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) importModelFromFolder(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityModelSettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.toolbar.navigationIcon = androidx.appcompat.content.res.AppCompatResources.getDrawable(this, androidx.appcompat.R.drawable.abc_ic_ab_back_material)

        binding.chooseFolderButton.setOnClickListener { folderPicker.launch(null) }
        binding.removeModelButton.setOnClickListener {
            val oldPath = prefs.getString(KEY_MODEL_PATH, null)
            if (oldPath != null) File(oldPath).delete()
            prefs.edit().remove(KEY_MODEL_PATH).remove(KEY_MODEL_NAME).remove(KEY_FOLDER_URI).apply()
            renderStatus()
            Toast.makeText(this, "Model ilova xotirasidan olib tashlandi", Toast.LENGTH_SHORT).show()
        }
        renderStatus()
    }

    private fun renderStatus() {
        val name = prefs.getString(KEY_MODEL_NAME, null)
        val path = prefs.getString(KEY_MODEL_PATH, null)
        binding.modelStatus.text = if (name != null && path != null && File(path).exists()) {
            "Tanlangan model: $name\nFayl tayyorlandi.\n\nLokal javob yaratish dvigateli hali ulanmagan."
        } else "Model tanlanmagan. GGUF model papkasini tanlang."
    }

    private fun importModelFromFolder(treeUri: Uri) {
        try {
            contentResolver.takePersistableUriPermission(
                treeUri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
        } catch (_: SecurityException) {
            // Read access from the returned URI is still usable for this import.
        }
        binding.progress.visibility = View.VISIBLE
        binding.chooseFolderButton.isEnabled = false
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val rootId = DocumentsContract.getTreeDocumentId(treeUri)
                    val found = mutableListOf<Pair<Uri, String>>()
                    findGgufFiles(treeUri, rootId, found, 0)
                    val chosen = found.firstOrNull()
                        ?: error("Tanlangan papkada .gguf model topilmadi. Model fayli shu papkada yoki uning ichki papkasida bo‘lishi kerak.")
                    val safeName = chosen.second.substringAfterLast('/').ifBlank { "model.gguf" }
                    val destination = File(filesDir, "local-models/$safeName")
                    destination.parentFile?.mkdirs()
                    contentResolver.openInputStream(chosen.first).use { input ->
                        requireNotNull(input) { "Model faylini o‘qib bo‘lmadi" }
                        FileOutputStream(destination).use { output -> input.copyTo(output) }
                    }
                    require(destination.length() > 1024 * 1024) { "Tanlangan fayl juda kichik; bu haqiqiy GGUF model bo‘lmasligi mumkin." }
                    Triple(destination.absolutePath, safeName, found.size)
                }
            }
            binding.progress.visibility = View.GONE
            binding.chooseFolderButton.isEnabled = true
            result.onSuccess { (path, name, count) ->
                prefs.edit().putString(KEY_MODEL_PATH, path).putString(KEY_MODEL_NAME, name)
                    .putString(KEY_FOLDER_URI, treeUri.toString()).apply()
                renderStatus()
                val extra = if (count > 1) " Bir nechta GGUF topildi; birinchi topilgan fayl tanlandi." else ""
                Toast.makeText(this@ModelSettingsActivity, "Model fayli tayyorlandi.$extra", Toast.LENGTH_LONG).show()
            }.onFailure { error ->
                Toast.makeText(this@ModelSettingsActivity, error.message ?: "Modelni o‘qishda xatolik", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun findGgufFiles(treeUri: Uri, parentId: String, output: MutableList<Pair<Uri, String>>, depth: Int) {
        if (depth > 12 || output.size >= 20) return
        val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, parentId)
        val projection = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE
        )
        contentResolver.query(childrenUri, projection, null, null, null)?.use { cursor ->
            val idCol = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
            val nameCol = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
            val typeCol = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_MIME_TYPE)
            while (cursor.moveToNext() && output.size < 20) {
                val id = cursor.getString(idCol)
                val name = cursor.getString(nameCol) ?: ""
                val type = cursor.getString(typeCol)
                if (type == DocumentsContract.Document.MIME_TYPE_DIR) {
                    findGgufFiles(treeUri, id, output, depth + 1)
                } else if (name.endsWith(".gguf", ignoreCase = true)) {
                    output.add(DocumentsContract.buildDocumentUriUsingTree(treeUri, id) to name)
                }
            }
        }
    }

    companion object {
        const val KEY_MODEL_PATH = "model_path"
        const val KEY_MODEL_NAME = "model_name"
        const val KEY_FOLDER_URI = "folder_uri"
    }
}
