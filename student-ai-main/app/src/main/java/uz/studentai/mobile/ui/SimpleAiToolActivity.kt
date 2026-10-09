package uz.studentai.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import retrofit2.Response
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivitySimpleToolBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.SimpleAiResponse
import uz.studentai.mobile.network.SolveRequest
import uz.studentai.mobile.network.TextOnlyRequest
import uz.studentai.mobile.network.TranslateRequest
import uz.studentai.mobile.util.SessionManager

/** Bitta ekran — TO'RTTA oddiy "matn kiritiladi -> AI javob qaytaradi"
 * PULLIK funksiya uchun (botdagi bilan bir xil narx/balans tizimi orqali):
 * Tarjima, Imlo/Grammatika, Konspekt qisqartirish, Masala yechish. */
enum class SimpleToolKind { TRANSLATE, GRAMMAR, SUMMARIZE, SOLVE }

class SimpleAiToolActivity : AppCompatActivity() {
    private lateinit var binding: ActivitySimpleToolBinding
    private lateinit var kind: SimpleToolKind

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySimpleToolBinding.inflate(layoutInflater)
        setContentView(binding.root)

        kind = SimpleToolKind.valueOf(intent.getStringExtra(EXTRA_KIND) ?: SimpleToolKind.GRAMMAR.name)
        setupForKind()

        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.submitButton.setOnClickListener { submit() }
        binding.copyButton.setOnClickListener { copyResult() }
    }

    private fun setupForKind() {
        when (kind) {
            SimpleToolKind.TRANSLATE -> {
                binding.toolbar.title = getString(R.string.translate_title)
                binding.secondaryFieldLayout.visibility = View.VISIBLE
                binding.secondaryFieldLayout.hint = getString(R.string.translate_lang_hint)
                binding.mainInput.hint = getString(R.string.translate_text_hint)
                binding.submitButton.text = getString(R.string.translate_submit)
            }
            SimpleToolKind.GRAMMAR -> {
                binding.toolbar.title = getString(R.string.grammar_title)
                binding.mainInput.hint = getString(R.string.grammar_text_hint)
                binding.submitButton.text = getString(R.string.grammar_submit)
            }
            SimpleToolKind.SUMMARIZE -> {
                binding.toolbar.title = getString(R.string.summarize_title)
                binding.mainInput.hint = getString(R.string.summarize_text_hint)
                binding.submitButton.text = getString(R.string.summarize_submit)
            }
            SimpleToolKind.SOLVE -> {
                binding.toolbar.title = getString(R.string.solve_title)
                binding.mainInput.hint = getString(R.string.solve_text_hint)
                binding.submitButton.text = getString(R.string.solve_submit)
            }
        }
    }

    private fun submit() {
        val text = binding.mainInput.text?.toString()?.trim().orEmpty()
        if (text.isEmpty()) return
        val secondary = binding.secondaryFieldInput.text?.toString()?.trim().orEmpty()
        if (kind == SimpleToolKind.TRANSLATE && secondary.isEmpty()) {
            Toast.makeText(this, R.string.translate_lang_hint, Toast.LENGTH_SHORT).show()
            return
        }

        setLoading(true)
        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@SimpleAiToolActivity)
                val response = when (kind) {
                    SimpleToolKind.TRANSLATE -> api.translate(TranslateRequest(text, secondary))
                    SimpleToolKind.GRAMMAR -> api.grammar(TextOnlyRequest(text))
                    SimpleToolKind.SUMMARIZE -> api.summarize(TextOnlyRequest(text))
                    SimpleToolKind.SOLVE -> api.solve(SolveRequest(text))
                }
                handleResponse(response)
            } catch (e: Exception) {
                Toast.makeText(this@SimpleAiToolActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
            } finally {
                setLoading(false)
            }
        }
    }

    private fun handleResponse(response: Response<SimpleAiResponse>) {
        val body = response.body()
        when {
            response.isSuccessful && body?.result != null -> {
                binding.resultCard.visibility = View.VISIBLE
                binding.resultText.text = body.result
            }
            response.code() == 401 -> {
                Toast.makeText(this, R.string.error_unauthorized, Toast.LENGTH_LONG).show()
                SessionManager(this).clear()
                startActivity(Intent(this, LoginActivity::class.java))
                finish()
            }
            response.code() == 402 -> {
                val price = body?.price ?: 0
                val balance = body?.balance ?: 0
                Toast.makeText(
                    this,
                    getString(R.string.insufficient_balance_message, price, balance),
                    Toast.LENGTH_LONG
                ).show()
            }
            response.code() == 403 -> Toast.makeText(this, R.string.feature_disabled_message, Toast.LENGTH_LONG).show()
            else -> Toast.makeText(this, R.string.error_network, Toast.LENGTH_SHORT).show()
        }
    }

    private fun copyResult() {
        val text = binding.resultText.text?.toString().orEmpty()
        if (text.isEmpty()) return
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("result", text))
        Toast.makeText(this, R.string.copied_toast, Toast.LENGTH_SHORT).show()
    }

    private fun setLoading(loading: Boolean) {
        binding.loadingProgress.visibility = if (loading) View.VISIBLE else View.GONE
        binding.submitButton.isEnabled = !loading
    }

    companion object {
        const val EXTRA_KIND = "extra_kind"
    }
}
