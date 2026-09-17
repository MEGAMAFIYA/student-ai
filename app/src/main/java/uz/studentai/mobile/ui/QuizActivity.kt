package uz.studentai.mobile.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.gson.Gson
import kotlinx.coroutines.launch
import retrofit2.Response
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivityQuizBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.QuizAnswerRequest
import uz.studentai.mobile.network.QuizAnswerResponse
import uz.studentai.mobile.network.QuizQuestionDto
import uz.studentai.mobile.network.QuizQuestionResponse
import uz.studentai.mobile.util.SessionManager

/**
 * Testni yechish ekrani.
 *  - Oddiy/Jas rejim: variantlar tugma sifatida ko'rsatiladi (bosilishi
 *    bilanoq javob yuboriladi).
 *  - Pro rejim: variantlar YO'Q — matn kiritish maydoni + "Javobni
 *    yuborish" tugmasi ko'rsatiladi.
 * Savol matni QALIN (bold) va kattaroq shriftda — o'qishni osonlashtirish
 * uchun (xuddi bot tomonidagi <b>...</b> o'zgarishi bilan bir xil g'oya).
 */
class QuizActivity : AppCompatActivity() {
    private lateinit var binding: ActivityQuizBinding
    private var pendingNext: QuizQuestionDto? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityQuizBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }

        val json = intent.getStringExtra(EXTRA_INITIAL_QUESTION_JSON)
        val initial = json?.let { Gson().fromJson(it, QuizQuestionResponse::class.java) }
        val firstQuestion = initial?.question
        if (firstQuestion == null) {
            finish()
            return
        }
        renderQuestion(firstQuestion)

        binding.submitProButton.setOnClickListener { submitProAnswer() }
    }

    private fun renderQuestion(q: QuizQuestionDto) {
        binding.resultText.visibility = View.GONE
        binding.nextButton.visibility = View.GONE
        binding.progressText.text = "${q.index + 1} / ${q.total}"
        binding.topicText.text = q.topic
        binding.questionText.text = q.question
        binding.optionsContainer.removeAllViews()

        if (q.options != null) {
            binding.proAnswerLayout.visibility = View.GONE
            binding.submitProButton.visibility = View.GONE
            val letters = listOf("A", "B", "C", "D", "E", "F", "G", "H")
            q.options.forEachIndexed { idx, optionText ->
                val button = MaterialButton(
                    this, null, com.google.android.material.R.attr.materialButtonOutlinedStyle
                )
                button.text = "${letters.getOrElse(idx) { (idx + 1).toString() }}) $optionText"
                button.isAllCaps = false
                button.textAlignment = View.TEXT_ALIGNMENT_TEXT_START
                val params = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
                )
                params.bottomMargin = dp(8)
                button.layoutParams = params
                button.setOnClickListener {
                    setOptionsEnabled(false)
                    submitAnswer(QuizAnswerRequest(index = idx))
                }
                binding.optionsContainer.addView(button)
            }
        } else {
            binding.proAnswerLayout.visibility = View.VISIBLE
            binding.submitProButton.visibility = View.VISIBLE
            binding.submitProButton.isEnabled = true
            binding.proAnswerInput.setText("")
        }
    }

    private fun submitProAnswer() {
        val text = binding.proAnswerInput.text?.toString()?.trim().orEmpty()
        if (text.isEmpty()) return
        binding.submitProButton.isEnabled = false
        submitAnswer(QuizAnswerRequest(text = text))
    }

    private fun submitAnswer(request: QuizAnswerRequest) {
        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@QuizActivity)
                val response = api.quizAnswer(request)
                handleAnswerResponse(response)
            } catch (e: Exception) {
                Toast.makeText(this@QuizActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
                setOptionsEnabled(true)
                binding.submitProButton.isEnabled = true
            }
        }
    }

    private fun handleAnswerResponse(response: Response<QuizAnswerResponse>) {
        if (!response.isSuccessful) {
            if (response.code() == 401) {
                Toast.makeText(this, R.string.error_unauthorized, Toast.LENGTH_LONG).show()
                SessionManager(this).clear()
                startActivity(Intent(this, LoginActivity::class.java))
                finish()
            } else {
                Toast.makeText(this, R.string.error_network, Toast.LENGTH_SHORT).show()
                setOptionsEnabled(true)
                binding.submitProButton.isEnabled = true
            }
            return
        }
        val body = response.body() ?: return

        setOptionsEnabled(false)
        binding.resultText.visibility = View.VISIBLE
        binding.resultText.text = if (body.correct) {
            "\uD83E\uDD70 To'g'ri javob!"
        } else {
            "\uD83D\uDE21 Xato! To'g'ri javob: ${body.correct_answer}"
        }
        binding.submitProButton.visibility = View.GONE
        binding.proAnswerLayout.visibility = View.GONE

        if (body.finished) {
            pendingNext = null
            binding.nextButton.text = getString(R.string.quiz_finish_title)
            binding.nextButton.visibility = View.VISIBLE
            binding.nextButton.setOnClickListener {
                val stats = body.stats
                Toast.makeText(
                    this,
                    "Natija: ${stats?.ok ?: 0}/${stats?.total ?: 0} to'g'ri",
                    Toast.LENGTH_LONG
                ).show()
                finish()
            }
        } else {
            pendingNext = body.next
            binding.nextButton.text = getString(R.string.quiz_next)
            binding.nextButton.visibility = View.VISIBLE
            binding.nextButton.setOnClickListener {
                pendingNext?.let { renderQuestion(it) }
            }
        }
    }

    private fun setOptionsEnabled(enabled: Boolean) {
        for (i in 0 until binding.optionsContainer.childCount) {
            binding.optionsContainer.getChildAt(i).isEnabled = enabled
        }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    companion object {
        const val EXTRA_INITIAL_QUESTION_JSON = "extra_initial_question_json"
    }
}
