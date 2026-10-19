package uz.studentai.mobile.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.gson.Gson
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import retrofit2.Response
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivityLoginBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.util.AppLog
import uz.studentai.mobile.network.PollResponse
import uz.studentai.mobile.network.TelegramCodeRequest
import uz.studentai.mobile.network.TelegramCodeSentResponse
import uz.studentai.mobile.network.TelegramCodeVerifyRequest
import uz.studentai.mobile.util.SessionManager

/**
 * Login oqimi (parol/SMS SHART EMAS). Ikki usul:
 *
 * 1) "Telegramda kirish": /api/mobile/auth/start -> deep_link -> Telegram'da
 *    START bosiladi -> ilova /auth/poll'ni so'raydi, status="ok" bo'lguncha.
 * 2) Telegram ID + kod: ID kiritiladi, bot shaxsiy chatga 6 xonali kod yuboradi,
 *    kod ilovaga kiritiladi (/auth/code/request va /auth/code/verify).
 */
class LoginActivity : AppCompatActivity() {
    private lateinit var binding: ActivityLoginBinding
    private lateinit var session: SessionManager
    private var pollJob: Job? = null
    private var cooldownJob: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(binding.root)
        session = SessionManager(this)
        binding.versionText.text = "v" + uz.studentai.mobile.BuildConfig.VERSION_NAME + " · " + uz.studentai.mobile.BuildConfig.BASE_URL

        binding.loginButton.setOnClickListener { startLogin() }
        binding.retryButton.setOnClickListener { startLogin() }
        binding.sendCodeButton.setOnClickListener { requestTelegramCode() }
        binding.verifyCodeButton.setOnClickListener { verifyTelegramCode() }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()

    /** Xato javobidan serverning "message" maydonini o'qiydi (Retrofit body=null bo'lganda). */
    private fun errorMessage(response: Response<*>): String? = try {
        val raw = response.errorBody()?.string()
        if (raw.isNullOrBlank()) null
        else Gson().fromJson(raw, TelegramCodeSentResponse::class.java)?.message
    } catch (e: Exception) {
        null
    }

    private fun requestTelegramCode() {
        val telegramId = binding.telegramIdInput.text?.toString()?.trim()?.toLongOrNull()
        if (telegramId == null || telegramId <= 0L) {
            toast("Telegram ID raqamini to‘g‘ri kiriting.")
            return
        }
        binding.sendCodeButton.isEnabled = false
        lifecycleScope.launch {
            var cooldown = 0
            try {
                val response = ApiClient.get(this@LoginActivity)
                    .requestTelegramCode(TelegramCodeRequest(telegramId))
                if (response.isSuccessful && response.body()?.sent == true) {
                    AppLog.ok(this@LoginActivity, "Kirish", "Telegram ID $telegramId uchun kod botga yuborildi.")
                    toast("Kod botdagi shaxsiy chatga yuborildi. 5 daqiqa amal qiladi.")
                    binding.codeSection.visibility = View.VISIBLE
                    binding.telegramCodeInput.requestFocus()
                    cooldown = 45
                } else {
                    AppLog.error(this@LoginActivity, "Kirish", "Kod yuborilmadi (Telegram ID $telegramId)",
                        AppLog.explainHttp(response.code(), errorMessage(response)))
                    toast(
                        errorMessage(response) ?: when (response.code()) {
                            400 -> "Botda avval /start bosing va Telegram ID ni tekshiring."
                            429 -> "Juda ko‘p urinish. Biroz kutib qayta urining."
                            503 -> "Server hozir tayyor emas. Keyinroq urinib ko‘ring."
                            else -> "Kod yuborilmadi (${response.code()}). Telegramda kirish usulini sinab ko‘ring."
                        }
                    )
                    if (response.code() == 429) cooldown = 30
                }
            } catch (e: java.net.SocketTimeoutException) {
                toast("Server javob bermadi (u uxlab qolgan bo‘lishi mumkin). 1 daqiqadan so‘ng qayta urinib ko‘ring.")
            } catch (e: Exception) {
                toast("Ulanish xatosi: ${e.message ?: "tarmoq mavjud emas"}. Manzil: ${uz.studentai.mobile.BuildConfig.BASE_URL}")
            } finally {
                startSendCooldown(cooldown)
            }
        }
    }

    /** "Kod yuborish" tugmasini bir necha soniya o'chirib qo'yadi (spam va 429 oldini olish). */
    private fun startSendCooldown(seconds: Int) {
        cooldownJob?.cancel()
        if (seconds <= 0) {
            binding.sendCodeButton.isEnabled = true
            binding.sendCodeButton.setText(R.string.login_send_code)
            return
        }
        cooldownJob = lifecycleScope.launch {
            for (left in seconds downTo 1) {
                binding.sendCodeButton.isEnabled = false
                binding.sendCodeButton.text = "${getString(R.string.login_send_code)} ($left)"
                delay(1000)
            }
            binding.sendCodeButton.isEnabled = true
            binding.sendCodeButton.setText(R.string.login_send_code)
        }
    }

    private fun verifyTelegramCode() {
        val telegramId = binding.telegramIdInput.text?.toString()?.trim()?.toLongOrNull()
        val code = binding.telegramCodeInput.text?.toString()?.trim().orEmpty()
        if (telegramId == null || telegramId <= 0L || !code.matches(Regex("\\d{6}"))) {
            toast("Telegram ID va 6 xonali kodni kiriting.")
            return
        }
        binding.verifyCodeButton.isEnabled = false
        lifecycleScope.launch {
            try {
                val response = ApiClient.get(this@LoginActivity)
                    .verifyTelegramCode(TelegramCodeVerifyRequest(telegramId, code))
                val result = response.body()
                if (response.isSuccessful && result?.status == "ok" && !result.session_token.isNullOrBlank()) {
                    AppLog.ok(this@LoginActivity, "Kirish", "Kod tasdiqlandi, tizimga kirildi (ID $telegramId).")
                    saveSessionAndGoHome(result, telegramId)
                } else if (response.code() == 429) {
                    toast("Juda ko‘p urinish. Biroz kuting.")
                } else if (response.code() == 401) {
                    AppLog.error(this@LoginActivity, "Kirish", "Kod tasdiqlanmadi", "Kod noto'g'ri yoki muddati (5 daqiqa) tugagan.")
                    toast("Kod noto‘g‘ri yoki muddati tugagan. Qayta kod oling.")
                } else {
                    toast("Tasdiqlab bo‘lmadi (${response.code()}). Qayta urinib ko‘ring.")
                }
            } catch (e: Exception) {
                toast("Tasdiqlash xatosi: ${e.message ?: "tarmoq xatosi"}")
            } finally {
                binding.verifyCodeButton.isEnabled = true
            }
        }
    }

    private fun saveSessionAndGoHome(result: PollResponse, fallbackId: Long = 0L) {
        session.sessionToken = result.session_token
        session.userId = result.user?.id ?: fallbackId
        session.userFirstName = result.user?.first_name
        startActivity(Intent(this, HomeActivity::class.java))
        finish()
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
                if (!openTelegram(start.deep_link)) {
                    resetToInitialState()
                    return@launch
                }
                pollLogin(start.login_token, start.expires_in)
            } catch (e: retrofit2.HttpException) {
                toast(if (e.code() == 429) "Juda ko‘p urinish. Biroz kutib qayta urining." else "Server xatosi (${e.code()}). Keyinroq urinib ko‘ring.")
                resetToInitialState()
            } catch (e: Exception) {
                Toast.makeText(this@LoginActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
                resetToInitialState()
            }
        }
    }

    private fun openTelegram(deepLink: String): Boolean {
        return try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(deepLink)))
            true
        } catch (e: Exception) {
            toast("Telegram ilovasi yoki brauzer topilmadi. Telegramni o‘rnating.")
            false
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
                            if (result.session_token.isNullOrBlank()) continue
                            saveSessionAndGoHome(result)
                            return@launch
                        }
                        "expired" -> {
                            showExpired()
                            return@launch
                        }
                        // "pending" -> davom etamiz
                    }
                } catch (e: Exception) {
                    // Vaqtinchalik tarmoq xatosi yoki 429 — pollingni davom ettiramiz.
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

    override fun onDestroy() {
        super.onDestroy()
        pollJob?.cancel()
        cooldownJob?.cancel()
    }
}
