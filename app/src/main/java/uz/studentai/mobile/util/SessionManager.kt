package uz.studentai.mobile.util

import android.content.Context
import androidx.core.content.edit

/** Login orqali olingan session_token va foydalanuvchi ma'lumotini
 * qurilmada saqlaydi (parol/SMS SAQLANMAYDI — faqat server tekshira
 * oladigan imzolangan token, qarang: mobile_auth.py). */
class SessionManager(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var sessionToken: String?
        get() = prefs.getString(KEY_TOKEN, null)
        set(value) = prefs.edit { putString(KEY_TOKEN, value) }

    var userId: Long
        get() = prefs.getLong(KEY_UID, 0L)
        set(value) = prefs.edit { putLong(KEY_UID, value) }

    var userFirstName: String?
        get() = prefs.getString(KEY_NAME, null)
        set(value) = prefs.edit { putString(KEY_NAME, value) }

    val isLoggedIn: Boolean get() = !sessionToken.isNullOrBlank()

    fun clear() = prefs.edit { clear() }

    companion object {
        private const val PREFS_NAME = "student_ai_session"
        private const val KEY_TOKEN = "session_token"
        private const val KEY_UID = "user_id"
        private const val KEY_NAME = "user_first_name"
    }
}
