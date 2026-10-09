package uz.studentai.mobile.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivityLoginBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.util.SessionManager

/**
 * Login oqimi (parol/SMS SHART EMAS):
 *  1. "Telegramda kirish" bosiladi -> backend /api/mobile/auth/start
 *     chaqiriladi -> login_token + deep_link olinadi.
 *  2. Telegram ilovasi ochiladi (deep_link), foydalanuvchi shunchaki
 *     botning shaxsiy chatida "START" bosadi.
 *  3. Ilova fonda /api/mobile/auth/poll'ni har 2 soniyada so'raydi, toki
 *     status="ok" (session_token olinadi) yoki "expired" bo'lguncha.
 */
class LoginActivity : AppCompatActivity() {
    private lateinit var binding: ActivityLoginBinding
    private lateinit var session: SessionManager
    private var pollJob: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(binding.root)
        session = SessionManager(this)

        binding.loginButton.setOnClickListener { startLogin() }
        binding.retryButton.setOnClickListener { startLogin() }
    }

    private fun startLogin() {
        pollJob?.cancel()
        binding.retryButton.visibility = View.GONE
        binding.statusText.visibility = View.VISIBLE
        binding.statusText.setText(R.string.login_waiting)
        binding.waitingProgress.visibility = View.VISIBLE
        binding.loginButton.isEnabled = false

        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@LoginActivity)
                val start = api.authStart()
                openTelegram(start.deep_link)
                pollLogin(start.login_token, start.expires_in)
            } catch (e: Exception) {
                Toast.makeText(this@LoginActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
                resetToInitialState()
            }
        }
    }

    private fun openTelegram(deepLink: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(deepLink)))
        } catch (e: Exception) {
            Toast.makeText(this, "Telegram ilovasi topilmadi. Iltimos, o'rnating.", Toast.LENGTH_LONG).show()
        }
    }

    private fun pollLogin(token: String, expiresInSeconds: Int) {
        pollJob = lifecycleScope.launch {
            val deadline = System.currentTimeMillis() + expiresInSeconds * 1000L
            val api = ApiClient.get(this@LoginActivity)
            while (System.currentTimeMillis() < deadline) {
                delay(2000)
                try {
                    val result = api.authPoll(token)
                    when (result.status) {
                        "ok" -> {
                            session.sessionToken = result.session_token
                            session.userId = result.user?.id ?: 0L
                            session.userFirstName = result.user?.first_name
                            goHome()
                            return@launch
                        }
                        "expired" -> {
                            showExpired()
                            return@launch
                        }
                        // "pending" -> davom etamiz, hech narsa qilinmaydi
                    }
                } catch (e: Exception) {
                    // Vaqtinchalik tarmoq xatosi bo'lishi mumkin — pollingni davom ettiramiz.
                }
            }
            showExpired()
        }
    }

    private fun showExpired() {
        binding.waitingProgress.visibility = View.GONE
        binding.statusText.setText(R.string.login_expired)
        binding.retryButton.visibility = View.VISIBLE
        binding.loginButton.isEnabled = true
    }

    private fun resetToInitialState() {
        binding.waitingProgress.visibility = View.GONE
        binding.statusText.visibility = View.GONE
        binding.loginButton.isEnabled = true
    }

    private fun goHome() {
        startActivity(Intent(this, HomeActivity::class.java))
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        pollJob?.cancel()
    }
}
