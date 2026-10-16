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
import uz.studentai.mobile.network.ApiClient\nimport uz.studentai.mobile.network.TelegramCodeRequest\nimport uz.studentai.mobile.network.TelegramCodeVerifyRequest
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
        binding.sendCodeButton.setOnClickListener { requestTelegramCode() }
        binding.verifyCodeButton.setOnClickListener { verifyTelegramCode() }
    }


    private fun requestTelegramCode() {
        val rawId = binding.telegramIdInput.text?.toString()?.trim().orEmpty()
        val telegramId = rawId.toLongOrNull()
        if (telegramId == null || telegramId <= 0L) {
            Toast.makeText(this, "Telegram ID raqamini to‘g‘ri kiriting.", Toast.LENGTH_LONG).show()
            return
        }
        binding.sendCodeButton.isEnabled = false
        lifecycleScope.launch {
            try {
                val response = ApiClient.get(this@LoginActivity)
                    .requestTelegramCode(TelegramCodeRequest(telegramId))
                if (response.isSuccessful && response.body()?.sent == true) {
                    Toast.makeText(this@LoginActivity, "Kod botdagi shaxsiy chatga yuborildi. 5 daqiqa amal qiladi.", Toast.LENGTH_LONG).show()
                } else {
                    val msg = response.body()?.message ?: when (response.code()) {
                        400 -> "Botda avval /start bosing, Telegram ID ni tekshiring."
                        429 -> "Juda ko‘p urinish. Biroz kuting."
                        else -> "Kod yuborilmadi (${response.code()}). Asosiy usulni sinab ko‘ring."
                    }
                    Toast.makeText(this@LoginActivity, msg, Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(this@LoginActivity, "Ulanish xatosi: ${e.message ?: "tarmoq mavjud emas"}", Toast.LENGTH_LONG).show()
            } finally {
                binding.sendCodeButton.isEnabled = true
            }
        }
    }

    private fun verifyTelegramCode() {
        val telegramId = binding.telegramIdInput.text?.toString()?.trim()?.toLongOrNull()
        val code = binding.telegramCodeInput.text?.toString()?.trim().orEmpty()
        if (telegramId == null || telegramId <= 0L || !code.matches(Regex("\\d{6}"))) {
            Toast.makeText(this, "Telegram ID va 6 xonali kodni kiriting.", Toast.LENGTH_LONG).show()
            return
        }
        binding.verifyCodeButton.isEnabled = false
        lifecycleScope.launch {
            try {
                val response = ApiClient.get(this@LoginActivity)
                    .verifyTelegramCode(TelegramCodeVerifyRequest(telegramId, code))
                val result = response.body()
                if (response.isSuccessful && result?.status == "ok" && !result.session_token.isNullOrBlank()) {
                    session.sessionToken = result.session_token
                    session.userId = result.user?.id ?: telegramId
                    session.userFirstName = result.user?.first_name
                    goHome()
                } else {
                    Toast.makeText(this@LoginActivity, "Kod noto‘g‘ri yoki muddati tugagan. Qayta kod oling.", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(this@LoginActivity, "Tasdiqlash xatosi: ${e.message ?: "tarmoq xatosi"}", Toast.LENGTH_LONG).show()
            } finally {
                binding.verifyCodeButton.isEnabled = true
            }
        }
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
