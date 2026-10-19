package uz.studentai.mobile.local

import android.content.Context
import java.io.File

/** Offline GGUF inference backed by llama.cpp. Keep calls off the main thread. */
object LocalLlamaEngine {
    private const val PREFS = "offline_model"
    private const val KEY_MODEL_PATH = "model_path"
    private val lock = Any()
    private var nativeHandle: Long = 0L
    private var loadedPath: String? = null
    private var libraryLoaded = false

    fun selectedModel(context: Context): File? {
        val path = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_MODEL_PATH, null) ?: return null
        return File(path).takeIf { it.isFile && it.length() > 1024 * 1024 }
    }

    fun generate(context: Context, prompt: String): String = synchronized(lock) {
        val modelFile = selectedModel(context)
            ?: throw IllegalStateException("Sozlamalardan GGUF model papkasini tanlang.")
        if (!libraryLoaded) {
            System.loadLibrary("studentai_llama")
            libraryLoaded = true
        }
        if (loadedPath != modelFile.absolutePath || nativeHandle == 0L) {
            if (nativeHandle != 0L) nativeFree(nativeHandle)
            nativeHandle = nativeLoadModel(modelFile.absolutePath)
            loadedPath = modelFile.absolutePath
        }
        nativeGenerate(nativeHandle, prompt, 160).trim().ifBlank {
            "Model javob yaratmadi. Qisqaroq savol bilan qayta urinib ko'ring."
        }
    }

    fun release() = synchronized(lock) {
        if (nativeHandle != 0L && libraryLoaded) nativeFree(nativeHandle)
        nativeHandle = 0L
        loadedPath = null
    }

    private external fun nativeLoadModel(path: String): Long
    private external fun nativeGenerate(handle: Long, prompt: String, maxTokens: Int): String
    private external fun nativeFree(handle: Long)
}
