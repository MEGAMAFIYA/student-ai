package uz.studentai.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivityCitationBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.CitationRequest
import uz.studentai.mobile.util.SessionManager

/** STUDENT > 📚 Iqtibos generatori — handlers/citation.py bilan bir xil
 * uslub (GOST/APA) va manba turlari. */
class CitationActivity : AppCompatActivity() {
    private lateinit var binding: ActivityCitationBinding
    private val sourceTypeKeys = listOf("book", "article", "web", "law", "other")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityCitationBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }

        val adapter = ArrayAdapter.createFromResource(
            this, R.array.citation_source_types, android.R.layout.simple_spinner_item
        )
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        binding.sourceTypeSpinner.adapter = adapter

        val hints = resources.getStringArray(R.array.citation_source_type_hints)
        binding.detailsHint.text = hints.getOrElse(0) { "" }
        binding.sourceTypeSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                binding.detailsHint.text = hints.getOrElse(position) { "" }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        binding.submitButton.setOnClickListener { submit() }
        binding.copyButton.setOnClickListener { copyResult() }
    }

    private fun submit() {
        val details = binding.detailsInput.text?.toString()?.trim().orEmpty()
        if (details.isEmpty()) return
        val style = if (binding.radioApa.isChecked) "APA" else "GOST"
        val sourceType = sourceTypeKeys.getOrElse(binding.sourceTypeSpinner.selectedItemPosition) { "other" }

        setLoading(true)
        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@CitationActivity)
                val response = api.citation(CitationRequest(style, sourceType, details))
                val body = response.body()
                when {
                    response.isSuccessful && body?.result != null -> {
                        binding.resultCard.visibility = View.VISIBLE
                        binding.resultText.text = body.result
                    }
                    response.code() == 401 -> {
                        Toast.makeText(this@CitationActivity, R.string.error_unauthorized, Toast.LENGTH_LONG).show()
                        SessionManager(this@CitationActivity).clear()
                        startActivity(Intent(this@CitationActivity, LoginActivity::class.java))
                        finish()
                    }
                    response.code() == 402 -> {
                        val price = body?.price ?: 0
                        val balance = body?.balance ?: 0
                        Toast.makeText(
                            this@CitationActivity,
                            getString(R.string.insufficient_balance_message, price, balance),
                            Toast.LENGTH_LONG
                        ).show()
                    }
                    response.code() == 403 ->
                        Toast.makeText(this@CitationActivity, R.string.feature_disabled_message, Toast.LENGTH_LONG).show()
                    else -> Toast.makeText(this@CitationActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(this@CitationActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
            } finally {
                setLoading(false)
            }
        }
    }

    private fun copyResult() {
        val text = binding.resultText.text?.toString().orEmpty()
        if (text.isEmpty()) return
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("citation", text))
        Toast.makeText(this, R.string.copied_toast, Toast.LENGTH_SHORT).show()
    }

    private fun setLoading(loading: Boolean) {
        binding.loadingProgress.visibility = if (loading) View.VISIBLE else View.GONE
        binding.submitButton.isEnabled = !loading
    }
}
