package uz.studentai.mobile.util

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Session data is encrypted at rest using a key protected by Android Keystore.
 * Only the server-issued session token is stored; passwords/SMS codes are not.
 */
class SessionManager(context: Context) {
    private val appContext = context.applicationContext
    private val prefs by lazy { openPrefs() }

    /**
     * Keystore ba'zi qurilmalarda (zaxira/qayta tiklash, tizim yangilanishi,
     * ayrim ishlab chiqaruvchilar) kalitni yo'qotadi va EncryptedSharedPreferences
     * yaratishda AEADBadTag/KeyStore xatosi beradi — bu ilovani ochilishida
     * yiqitardi. Bunday holda eski shifrlangan fayl va kalit o'chirilib, qaytadan
     * yaratiladi (foydalanuvchi oddiygina qayta kiradi).
     */
    private fun openPrefs(): SharedPreferences {
        return try {
            createEncrypted()
        } catch (e: Exception) {
            try {
                appContext.deleteSharedPreferences(PREFS_NAME)
                try {
                    java.security.KeyStore.getInstance("AndroidKeyStore").apply {
                        load(null)
                        deleteEntry(MasterKey.DEFAULT_MASTER_KEY_ALIAS)
                    }
                } catch (e: Exception) { }
                createEncrypted()
            } catch (e2: Exception) {
                // Oxirgi chora: shifrlanmagan, lekin ishlaydigan xotira (ilova yiqilmasin).
                appContext.getSharedPreferences(LEGACY_PREFS_NAME, Context.MODE_PRIVATE)
            }
        }
    }

    private fun createEncrypted(): SharedPreferences {
        val masterKey = MasterKey.Builder(appContext)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        val encrypted = EncryptedSharedPreferences.create(
            appContext,
            PREFS_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
        // One-time migration from the former plaintext preferences, then wipe
        // the old file so the bearer token is not left behind in cleartext.
        val legacy = appContext.getSharedPreferences(LEGACY_PREFS_NAME, Context.MODE_PRIVATE)
        if (legacy.contains(KEY_TOKEN) || legacy.contains(KEY_UID) || legacy.contains(KEY_NAME)) {
            encrypted.edit().apply {
                legacy.getString(KEY_TOKEN, null)?.let { putString(KEY_TOKEN, it) }
                putLong(KEY_UID, legacy.getLong(KEY_UID, 0L))
                legacy.getString(KEY_NAME, null)?.let { putString(KEY_NAME, it) }
            }.apply()
            legacy.edit().clear().apply()
        }
        return encrypted
    }

    var sessionToken: String?
        get() = prefs.getString(KEY_TOKEN, null)
        set(value) {
            prefs.edit().apply {
                if (value.isNullOrBlank()) remove(KEY_TOKEN) else putString(KEY_TOKEN, value)
            }.apply()
        }

    var userId: Long
        get() = prefs.getLong(KEY_UID, 0L)
        set(value) { prefs.edit().putLong(KEY_UID, value).apply() }

    var userFirstName: String?
        get() = prefs.getString(KEY_NAME, null)
        set(value) {
            prefs.edit().apply {
                if (value == null) remove(KEY_NAME) else putString(KEY_NAME, value)
            }.apply()
        }

    val isLoggedIn: Boolean get() = !sessionToken.isNullOrBlank()

    fun clear() {
        prefs.edit().clear().apply()
    }

    companion object {
        private const val PREFS_NAME = "student_ai_session_encrypted"
        private const val LEGACY_PREFS_NAME = "student_ai_session"
        private const val KEY_TOKEN = "session_token"
        private const val KEY_UID = "user_id"
        private const val KEY_NAME = "user_first_name"
    }
}
