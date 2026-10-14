package uz.studentai.mobile.util

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Session data is encrypted at rest using a key protected by Android Keystore.
 * Only the server-issued session token is stored; passwords/SMS codes are not.
 */
class SessionManager(context: Context) {
    private val appContext = context.applicationContext
    private val prefs by lazy {
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
        encrypted
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
