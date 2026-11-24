package uz.studentai.mobile.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

/** First-launch permission onboarding. Android still shows each system permission prompt separately. */
class AssistantPermissionActivity : AppCompatActivity() {
    private val request = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        finishSetup()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 36, 28, 24)
        }
        root.addView(TextView(this).apply {
            text = "AL Yordamchini sozlash"
            textSize = 24f
        })
        root.addView(TextView(this).apply {
            text = "Ovozli buyruqlar, kontaktlarni topish va fon eslatmalari ishlashi uchun quyidagi ruxsatlarni sozlang. Android har bir ruxsatni o'zining tizim oynasida tasdiqlatadi. Istalgan ruxsatni bermasangiz ham Student AI ishlashda davom etadi."
            textSize = 16f
            setPadding(0, 18, 0, 24)
        })
        root.addView(Button(this).apply {
            text = "Ruxsatlarni sozlash"
            setOnClickListener { request.launch(requiredPermissions()) }
        })
        root.addView(Button(this).apply {
            text = "Hozircha o'tkazib yuborish"
            setOnClickListener { finishSetup() }
        })
        setContentView(root)
    }

    private fun requiredPermissions(): Array<String> = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        add(Manifest.permission.READ_CONTACTS)
        if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
    }.toTypedArray()

    private fun finishSetup() {
        getSharedPreferences("assistant_first_run", MODE_PRIVATE).edit().putBoolean("complete", true).apply()
        startActivity(Intent(this, if (uz.studentai.mobile.util.SessionManager(this).isLoggedIn) HomeActivity::class.java else LoginActivity::class.java))
        finish()
    }
}
