package uz.studentai.mobile.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognizerIntent
import android.speech.tts.TextToSpeech
import android.view.View
import android.util.Log
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import kotlinx.coroutines.launch
import uz.studentai.mobile.R
import uz.studentai.mobile.assistant.AssistantDeviceActions
import uz.studentai.mobile.databinding.ActivityChatBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.AssistantPlanRequest
import uz.studentai.mobile.network.ChatMessageDto
import uz.studentai.mobile.util.SessionManager
import java.util.Locale

/** AL Yordamchi chat: AI rejasini serverdan oladi, telefon amallarini faqat Android bajaradi. */
class ChatActivity : AppCompatActivity(), TextToSpeech.OnInitListener {
    private lateinit var binding: ActivityChatBinding
    private val messages = mutableListOf<ChatUiMessage>()
    private lateinit var adapter: ChatAdapter
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityChatBinding.inflate(layoutInflater)
        setContentView(binding.root)
        if (!intent.getBooleanExtra(EXTRA_AL_MODE, false)) {
            Log.e(TAG, "AL chat activity opened without AL mode; refusing other AI routes")
            finish()
            return
        }
        getSharedPreferences("al_assistant_mode", MODE_PRIVATE).edit().putBoolean("active", true).apply()
        Log.i(TAG, "AL mode entered; universal chat routing disabled in this screen")
        binding.toolbar.title = "AL Yordamchi"
        binding.toolbar.setNavigationOnClickListener { finish() }
        adapter = ChatAdapter(messages)
        binding.messagesList.layoutManager = LinearLayoutManager(this)
        binding.messagesList.adapter = adapter
        binding.sendButton.setOnClickListener { send() }
        binding.voiceButton.setOnClickListener { startVoiceInput() }
        tts = TextToSpeech(this, this)
        adapter.add(ChatUiMessage("assistant", "Salom! Men AL Yordamchiman. Buyruqni yozing yoki mikrofon orqali ayting. Masalan: ‘Soat 7:00 ga budilnik qo‘y’."))
    }

    private fun send() {
        val text = binding.messageInput.text?.toString()?.trim().orEmpty()
        if (text.isEmpty()) return
        binding.messageInput.setText("")
        adapter.add(ChatUiMessage("user", text))
        binding.messagesList.scrollToPosition(messages.size - 1)
        binding.typingProgress.visibility = View.VISIBLE
        binding.sendButton.isEnabled = false
        binding.voiceButton.isEnabled = false
        val history = messages.dropLast(1).takeLast(10).map { ChatMessageDto(it.role, it.text) }

        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@ChatActivity)
                Log.i(TAG, "request_started chars=${text.length} history=${history.size}")
                val response = api.assistantPlan(AssistantPlanRequest(text, history))
                if (response.isSuccessful && response.body() != null) {
                    val plan = response.body()!!
                    Log.i(TAG, "assistant_plan_received action=${plan.action}")
                    if (plan.action == "none") {
                        addAssistantMessage(plan.reply.ifBlank { "Tushundim." }, speak = true)
                    } else {
                        // Device actions run locally; the model cannot call arbitrary Android APIs.
                        AssistantDeviceActions.execute(this@ChatActivity, plan.action, plan.data, plan.reply) { result ->
                            runOnUiThread { addAssistantMessage(result, speak = true) }
                        }
                    }
                } else if (response.code() == 401) {
                    handleUnauthorized()
                } else {
                    val error = "AL Yordamchi serveri xato qaytardi: HTTP ${response.code()} ${response.errorBody()?.string()?.take(300).orEmpty()}"
                    Log.e(TAG, error)
                    addAssistantMessage(error)
                }
            } catch (e: Exception) {
                Log.e(TAG, "AL request failed; no fallback AI will be used", e)
                addAssistantMessage("AL Yordamchi bilan aloqa xatosi: ${e.localizedMessage ?: getString(R.string.error_network)}. Boshqa AI avtomatik ishga tushirilmadi.")
            } finally {
                binding.typingProgress.visibility = View.GONE
                binding.sendButton.isEnabled = true
                binding.voiceButton.isEnabled = true
                binding.messagesList.scrollToPosition(messages.size - 1)
            }
        }
    }

    private fun addAssistantMessage(text: String, speak: Boolean = false) {
        adapter.add(ChatUiMessage("assistant", text))
        binding.messagesList.scrollToPosition(messages.size - 1)
        if (speak && ttsReady) tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "al_reply_${System.currentTimeMillis()}")
    }

    private fun startVoiceInput() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "Ovozli buyruq uchun mikrofon ruxsatini yoqing.", Toast.LENGTH_LONG).show()
            return
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "uz-UZ")
            putExtra(RecognizerIntent.EXTRA_PROMPT, "AL Yordamchiga buyruq bering")
        }
        try { startActivityForResult(intent, REQUEST_SPEECH) }
        catch (_: Exception) { Toast.makeText(this, "Ovoz tanish xizmati topilmadi.", Toast.LENGTH_LONG).show() }
    }

    @Deprecated("Uses system speech recognizer activity")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_SPEECH && resultCode == RESULT_OK) {
            val spoken = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull().orEmpty()
            if (spoken.isNotBlank()) { binding.messageInput.setText(spoken); send() }
        }
    }

    override fun onInit(status: Int) {
        ttsReady = status == TextToSpeech.SUCCESS
        if (ttsReady) tts?.language = Locale("uz", "UZ")
    }

    private fun handleUnauthorized() {
        Toast.makeText(this, R.string.error_unauthorized, Toast.LENGTH_LONG).show()
        SessionManager(this).clear()
        startActivity(Intent(this, LoginActivity::class.java))
        finish()
    }

    override fun onDestroy() {
        tts?.stop()
        tts?.shutdown()
        getSharedPreferences("al_assistant_mode", MODE_PRIVATE).edit().putBoolean("active", false).apply()
        Log.i(TAG, "AL mode exited")
        super.onDestroy()
    }

    companion object {
        const val EXTRA_AL_MODE = "extra_al_mode"
        private const val REQUEST_SPEECH = 7412
        private const val TAG = "AL_YORDAMCHI"
    }
}
