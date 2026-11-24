package uz.studentai.mobile.assistant

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.PendingIntent
import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import uz.studentai.mobile.R

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            restoreReminders(context)
            return
        }
        val title = intent.getStringExtra(EXTRA_TITLE) ?: "Student AI eslatmasi"
        val reminderId = intent.getIntExtra(EXTRA_ID, title.hashCode())
        context.getSharedPreferences("al_local_reminders", Context.MODE_PRIVATE).edit().remove("reminder_$reminderId").apply()
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(NotificationChannel(CHANNEL, "AL Yordamchi eslatmalari", NotificationManager.IMPORTANCE_HIGH))
        }
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("AL Yordamchi eslatmasi")
            .setContentText(title)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        try { NotificationManagerCompat.from(context).notify(title.hashCode(), notification) } catch (_: SecurityException) { }
    }
    private fun restoreReminders(context: Context) {
        val prefs = context.getSharedPreferences("al_local_reminders", Context.MODE_PRIVATE)
        val alarm = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val now = System.currentTimeMillis()
        prefs.all.forEach { (key, value) ->
            if (!key.startsWith("reminder_") || value !is String) return@forEach
            val id = key.removePrefix("reminder_").toIntOrNull() ?: return@forEach
            val split = value.split("|", limit = 2)
            val at = split.firstOrNull()?.toLongOrNull() ?: return@forEach
            val title = split.getOrNull(1) ?: "Eslatma"
            if (at <= now) { prefs.edit().remove(key).apply(); return@forEach }
            val pending = PendingIntent.getBroadcast(
                context, id, Intent(context, ReminderReceiver::class.java)
                    .putExtra(EXTRA_TITLE, title).putExtra(EXTRA_ID, id),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending)
        }
    }

    companion object {
        const val EXTRA_TITLE = "reminder_title"
        const val EXTRA_ID = "reminder_id"
        private const val CHANNEL = "al_assistant_reminders"
    }
}
