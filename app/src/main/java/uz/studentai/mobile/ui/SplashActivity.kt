package uz.studentai.mobile.ui

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import uz.studentai.mobile.R
import uz.studentai.mobile.util.SessionManager

/** Ilova ochilganda: sessiya bor bo'lsa to'g'ridan-to'g'ri HomeActivity'ga,
 * bo'lmasa LoginActivity'ga o'tkazadi. */
class SplashActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_splash)

        val completed = getSharedPreferences("assistant_first_run", MODE_PRIVATE).getBoolean("complete", false)
        if (!completed) {
            startActivity(Intent(this, AssistantPermissionActivity::class.java))
            finish()
            return
        }
        val session = SessionManager(this)
        val target = if (session.isLoggedIn) HomeActivity::class.java else LoginActivity::class.java
        startActivity(Intent(this, target))
        finish()
    }
}
