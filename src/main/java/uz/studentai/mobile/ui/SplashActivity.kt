package uz.studentai.mobile.ui

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import uz.studentai.mobile.R
import uz.studentai.mobile.util.AppLog
import uz.studentai.mobile.util.SessionManager

/** Ilova ochilganda: sessiya bor bo'lsa to'g'ridan-to'g'ri HomeActivity'ga,
 * bo'lmasa LoginActivity'ga o'tkazadi. */
class SplashActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_splash)

        AppLog.installCrashHandler(this)
        val session = SessionManager(this)
        AppLog.info(this, "Ilova", "Ilova ochildi (versiya ${uz.studentai.mobile.BuildConfig.VERSION_NAME}, server ${uz.studentai.mobile.BuildConfig.BASE_URL}). " +
            if (session.isLoggedIn) "Sessiya bor — bosh ekran." else "Sessiya yo'q — kirish ekrani.")
        val target = if (session.isLoggedIn) HomeActivity::class.java else LoginActivity::class.java
        startActivity(Intent(this, target))
        finish()
    }
}
