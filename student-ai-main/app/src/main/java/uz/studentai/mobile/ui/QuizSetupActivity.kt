package uz.studentai.mobile.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.gson.Gson
import kotlinx.coroutines.launch
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivityQuizSetupBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.QuizStartRequest

/**
 * STUDENT > 📋 Test/Viktorina sozlamalari — botdagi inline "test <N> pro
 * jas" so'roviga TO'LIQ mos: savollar soni (bo'sh — barchasi), Pro rejim
 * (variantlarsiz, matn javob) va Jas rejim (variantlar teskari).
 */
class QuizSetupActivity : AppCompatActivity() {
    private lateinit var binding: ActivityQuizSetupBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityQuizSetupBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }

        binding.startButton.setOnClickListener { start() }
    }

    private fun start() {
        val n = binding.countInput.text?.toString()?.trim()?.toIntOrNull()
        val pro = binding.proCheck.isChecked
        val jas = binding.jasCheck.isChecked

        binding.loadingProgress.visibility = View.VISIBLE
        binding.startButton.isEnabled = false

        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@QuizSetupActivity)
                val response = api.quizStart(QuizStartRequest(n = n, pro = pro, jas = jas))
                val body = response.body()
                when {
                    response.isSuccessful && body?.question != null -> {
                        val intent = Intent(this@QuizSetupActivity, QuizActivity::class.java)
                        intent.putExtra(QuizActivity.EXTRA_INITIAL_QUESTION_JSON, Gson().toJson(body))
                        startActivity(intent)
                        finish()
                    }
                    response.code() == 401 -> {
                        Toast.makeText(this@QuizSetupActivity, R.string.error_unauthorized, Toast.LENGTH_LONG).show()
                    }
                    else -> {
                        Toast.makeText(this@QuizSetupActivity, "Hozircha faol testlar mavjud emas.", Toast.LENGTH_LONG).show()
                    }
                }
            } catch (e: Exception) {
                Toast.makeText(this@QuizSetupActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
            } finally {
                binding.loadingProgress.visibility = View.GONE
                binding.startButton.isEnabled = true
            }
        }
    }
}
