package uz.studentai.mobile.ui

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import uz.studentai.mobile.databinding.ActivityHomeBinding
import uz.studentai.mobile.util.SessionManager

/**
 * Bosh ekran — botdagi HAR BIR top-level buyruqqa (/start, /kino, /tabrik,
 * /rasim, /vid, /qoshiq, /pro, /my) mos BITTA tugma, xuddi foydalanuvchi
 * so'ragan tartibda:
 *   - STUDENT -> /start menyusi (StudentMenuActivity)
 *   - KINO    -> /kino          (KinoListActivity)
 *   - qolganlari -> 1-bosqichda hali ulanmagan, "Tez orada" ko'rsatiladi
 *     (2, 3-bosqichlarda xuddi shu naqsh bo'yicha ulanadi).
 */
class HomeActivity : AppCompatActivity() {
    private lateinit var binding: ActivityHomeBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityHomeBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)

        binding.btnStudent.setOnClickListener { startActivity(Intent(this, StudentMenuActivity::class.java)) }
        binding.btnKino.setOnClickListener { startActivity(Intent(this, KinoListActivity::class.java)) }

        binding.btnTabrik.setOnClickListener { openComingSoon(getString(uz.studentai.mobile.R.string.home_tabrik)) }
        binding.btnRasim.setOnClickListener { openComingSoon(getString(uz.studentai.mobile.R.string.home_rasim)) }
        binding.btnVid.setOnClickListener { openComingSoon(getString(uz.studentai.mobile.R.string.home_vid)) }
        binding.btnQoshiq.setOnClickListener { openComingSoon(getString(uz.studentai.mobile.R.string.home_qoshiq)) }
        binding.btnPro.setOnClickListener { openComingSoon(getString(uz.studentai.mobile.R.string.home_pro)) }
        binding.btnMy.setOnClickListener { openComingSoon(getString(uz.studentai.mobile.R.string.home_my)) }

        binding.btnLogout.setOnClickListener {
            SessionManager(this).clear()
            startActivity(Intent(this, LoginActivity::class.java))
            finish()
        }
    }

    private fun openComingSoon(title: String) {
        val intent = Intent(this, ComingSoonActivity::class.java)
        intent.putExtra(ComingSoonActivity.EXTRA_TITLE, title)
        startActivity(intent)
    }
}
