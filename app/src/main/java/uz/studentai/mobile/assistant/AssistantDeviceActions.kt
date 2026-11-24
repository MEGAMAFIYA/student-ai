package uz.studentai.mobile.assistant

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.database.Cursor
import android.net.Uri
import android.provider.AlarmClock
import android.provider.CalendarContract
import android.provider.ContactsContract
import android.provider.Settings
import android.content.pm.PackageManager
import android.os.Build
import android.widget.Toast
import android.util.Log
import androidx.appcompat.app.AlertDialog
import com.google.gson.JsonObject
import uz.studentai.mobile.assistant.ReminderReceiver
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

/** Executes only the allow-listed Android actions returned by the authenticated backend. */
object AssistantDeviceActions {
    private const val TAG = "AL_YORDAMCHI_ACTION"
    fun execute(context: Context, action: String, data: JsonObject, reply: String, onResult: (String) -> Unit) {
        Log.i(TAG, "action_requested=$action")
        val finish: (String) -> Unit = { result ->
            val failed = result.startsWith("Amalni bajarishda xatolik") || result.startsWith("Bu amal uchun ruxsat") ||
                result.contains("topilmadi", ignoreCase = true) || result.contains("topa olmadim", ignoreCase = true)
            Log.i(TAG, "action_finished=$action status=${if (failed) "failed" else "reported"} result_chars=${result.length}")
            onResult(result)
        }
        try {
            when (action) {
                "set_alarm" -> setAlarm(context, data, finish)
                "create_calendar_event" -> createCalendarEvent(context, data, finish)
                "create_reminder" -> createReminder(context, data, finish)
                "open_app" -> openApp(context, data, finish)
                "open_settings" -> openSettings(context, data, finish)
                "find_contact" -> findContact(context, data, finish)
                "make_call" -> makeCall(context, data, finish)
                else -> finish(reply.ifBlank { "Sizga qanday yordam bera olaman?" })
            }
        } catch (e: SecurityException) {
            Log.e(TAG, "action_failed=$action reason=permission", e)
            finish("Bu amal uchun ruxsat kerak. Student AI sozlamalaridan tegishli ruxsatni yoqing.")
        } catch (e: Exception) {
            Log.e(TAG, "action_failed=$action reason=${e.javaClass.simpleName}", e)
            finish("Amalni bajarishda xatolik: ${e.localizedMessage ?: "noma'lum xato"}")
        }
    }

    private fun setAlarm(context: Context, data: JsonObject, done: (String) -> Unit) {
        val hour = data.get("hour")?.asInt ?: throw IllegalArgumentException("Soat ko'rsatilmagan")
        val minute = data.get("minute")?.asInt ?: 0
        require(hour in 0..23 && minute in 0..59) { "Vaqt noto'g'ri" }
        val intent = Intent(AlarmClock.ACTION_SET_ALARM).apply {
            putExtra(AlarmClock.EXTRA_HOUR, hour)
            putExtra(AlarmClock.EXTRA_MINUTES, minute)
            putExtra(AlarmClock.EXTRA_MESSAGE, data.get("label")?.asString ?: "Student AI")
            putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        if (intent.resolveActivity(context.packageManager) != null) {
            context.startActivity(intent)
            done("Budilnik uchun ${"%02d:%02d".format(hour, minute)} vaqti telefon soat ilovasiga yuborildi. Saqlashni tasdiqlang.")
        } else done("Telefoningizda budilnik ilovasi topilmadi.")
    }

    private fun createCalendarEvent(context: Context, data: JsonObject, done: (String) -> Unit) {
        val title = data.get("title")?.asString?.take(120).orEmpty().ifBlank { "Student AI tadbiri" }
        val date = data.get("date")?.asString ?: throw IllegalArgumentException("Sana kerak")
        val time = data.get("time")?.asString ?: "09:00"
        val start = parseLocalMillis(date, time)
        val duration = (data.get("duration_minutes")?.asInt ?: 60).coerceIn(5, 1440)
        val intent = Intent(Intent.ACTION_INSERT).setData(CalendarContract.Events.CONTENT_URI).apply {
            putExtra(CalendarContract.Events.TITLE, title)
            putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, start)
            putExtra(CalendarContract.EXTRA_EVENT_END_TIME, start + duration * 60_000L)
            putExtra(CalendarContract.Events.DESCRIPTION, data.get("description")?.asString ?: "Student AI orqali yaratildi")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
        done("Kalendar tadbiri tayyorlandi. Ochilgan oynada saqlashni bosing: $title")
    }

    private fun createReminder(context: Context, data: JsonObject, done: (String) -> Unit) {
        val title = data.get("title")?.asString?.take(180).orEmpty().ifBlank { "Eslatma" }
        val date = data.get("date")?.asString ?: throw IllegalArgumentException("Sana kerak")
        val time = data.get("time")?.asString ?: throw IllegalArgumentException("Vaqt kerak")
        val at = parseLocalMillis(date, time)
        require(at > System.currentTimeMillis()) { "Eslatma vaqti kelajakda bo'lishi kerak" }
        if (Build.VERSION.SDK_INT >= 33 &&
            androidx.core.content.ContextCompat.checkSelfPermission(context, android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) throw SecurityException("Bildirishnomalar ruxsati yoqilmagan")
        val alarm = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val id = (title + date + time).hashCode()
        val pending = PendingIntent.getBroadcast(
            context, id,
            Intent(context, ReminderReceiver::class.java)
                .putExtra(ReminderReceiver.EXTRA_TITLE, title)
                .putExtra(ReminderReceiver.EXTRA_ID, id),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        context.getSharedPreferences("al_local_reminders", Context.MODE_PRIVATE)
            .edit().putString("reminder_$id", "$at|$title").apply()
        alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending)
        done("Eslatma telefonga saqlandi: $date, $time — $title")
    }

    private fun openApp(context: Context, data: JsonObject, done: (String) -> Unit) {
        val wanted = data.get("app_name")?.asString?.trim().orEmpty()
        if (wanted.isBlank()) { done("Qaysi ilovani ochay?"); return }
        val pm = context.packageManager
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val matches = pm.queryIntentActivities(launcher, PackageManager.MATCH_ALL)
        val found = matches.firstOrNull { it.loadLabel(pm).toString().equals(wanted, true) }
            ?: matches.firstOrNull { it.loadLabel(pm).toString().contains(wanted, true) }
        val launch = found?.activityInfo?.let { pm.getLaunchIntentForPackage(it.packageName) }
        if (launch == null) done("'$wanted' ilovasini topa olmadim.") else {
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(launch)
            done("${found.loadLabel(pm)} ilovasi ochildi.")
        }
    }

    private fun openSettings(context: Context, data: JsonObject, done: (String) -> Unit) {
        val setting = data.get("setting")?.asString?.lowercase(Locale.ROOT) ?: "general"
        val action = when (setting) {
            "wifi" -> Settings.ACTION_WIFI_SETTINGS
            "bluetooth" -> Settings.ACTION_BLUETOOTH_SETTINGS
            "sound" -> Settings.ACTION_SOUND_SETTINGS
            "display" -> Settings.ACTION_DISPLAY_SETTINGS
            "battery" -> Settings.ACTION_BATTERY_SAVER_SETTINGS
            "location" -> Settings.ACTION_LOCATION_SOURCE_SETTINGS
            else -> Settings.ACTION_SETTINGS
        }
        context.startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        done("Telefon sozlamalari ochildi.")
    }

    private fun findContact(context: Context, data: JsonObject, done: (String) -> Unit) {
        val name = data.get("contact_name")?.asString?.trim().orEmpty()
        if (name.isBlank()) { done("Kontakt ismini ayting."); return }
        val phone = lookupPhone(context, name)
        done(if (phone == null) "'$name' nomli kontakt topilmadi." else "$name: $phone")
    }

    private fun makeCall(context: Context, data: JsonObject, done: (String) -> Unit) {
        val direct = data.get("phone")?.asString?.trim().orEmpty()
        val name = data.get("contact_name")?.asString?.trim().orEmpty()
        val phone = direct.ifBlank { lookupPhone(context, name).orEmpty() }
        if (phone.isBlank()) { done("Kontakt yoki telefon raqamini topa olmadim."); return }
        AlertDialog.Builder(context)
            .setTitle("Qo'ng'iroqni tasdiqlaysizmi?")
            .setMessage("$name\n$phone")
            .setNegativeButton("Bekor qilish") { _, _ -> done("Qo'ng'iroq bekor qilindi.") }
            .setPositiveButton("Telefon oynasini ochish") { _, _ ->
                context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${Uri.encode(phone)}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                done("Telefon raqami terish oynasiga kiritildi. Qo'ng'iroqni o'zingiz boshlaysiz.")
            }.show()
    }

    private fun lookupPhone(context: Context, name: String): String? {
        val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(name))
        context.contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup.DISPLAY_NAME, ContactsContract.PhoneLookup.NUMBER), null, null, null)?.use { c ->
            if (c.moveToFirst()) return c.getString(1)
        }
        return null
    }

    private fun parseLocalMillis(date: String, time: String): Long {
        val parser = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply { isLenient = false }
        return parser.parse("$date $time")?.time ?: throw IllegalArgumentException("Sana yoki vaqt formati noto'g'ri")
    }
}
